"""
Agent Registry — multi-agent orchestration for Hermes.

Each sub-agent is defined by a YAML config and registered with the session DB.
Agents run as independent asyncio.Tasks, each with their own model, system prompt,
tools, token budget, and context memory.

Usage:
    from agent_registry import AgentRegistry
    from hermes_state import SessionDB

    db = SessionDB()
    registry = AgentRegistry(db)
    registry.load_all("agent_configs/")

    # Spawn a sub-agent
    await registry.spawn("coder")

    # Call a sub-agent directly
    response = await registry.call("coder", "session-id", "напиши сортировку")

    # Stream from a sub-agent
    async for chunk in registry.stream("coder", "session-id", "объясни"):
        print(chunk, end="")

    # Orchestrate across sub-agents
    final = await registry.orchestrate("session-id", "сложный запрос")

    # List all agents
    for agent in registry.list():
        print(agent["agent_id"], agent["model"], agent["status"])
"""

import asyncio
import json
import logging
import os
from pathlib import Path
from typing import Any, AsyncIterator

import yaml

logger = logging.getLogger(__name__)

DEFAULT_CONFIG_DIR = Path(__file__).parent / "agent_configs"


class AgentRegistry:
    """Registry and orchestrator for sub-agents.

    Each agent is a YAML config + a record in SessionDB.
    Does NOT rewrite the agent loop — wraps it via asyncio.Task.
    Uses native anthropic.AsyncAnthropic client (not a custom provider class).
    """

    def __init__(self, db=None, config_dir: Path | None = None):
        self._db = db  # SessionDB — set later if None
        self._config_dir = config_dir or DEFAULT_CONFIG_DIR
        self._agents: dict[str, dict] = {}         # agent_id -> config
        self._tasks: dict[str, asyncio.Task] = {}  # agent_id -> running task
        self._providers: dict[str, dict] = {}      # agent_id -> {client, model, max_tokens}

    def set_db(self, db):
        """Set SessionDB after init (avoids circular imports)."""
        self._db = db

    # ── Config management ────────────────────────────────────

    def load_config(self, config_path: str | Path) -> dict:
        """Load a single agent config from YAML file."""
        with open(config_path) as f:
            cfg = yaml.safe_load(f)
        if not cfg.get("agent_id"):
            cfg["agent_id"] = Path(config_path).stem
        return cfg

    def load_all(self, directory: str | Path | None = None) -> int:
        """Load all agent configs from a directory. Returns count."""
        directory = Path(directory or self._config_dir)
        count = 0
        if directory.exists():
            for f in sorted(directory.glob("*.yaml")):
                try:
                    cfg = self.load_config(f)
                    self.register(cfg["agent_id"], cfg)
                    count += 1
                except Exception as e:
                    logger.warning(f"Failed to load {f}: {e}")
        return count

    def register(self, agent_id: str, config: dict):
        """Register an agent by id and config dict."""
        config.setdefault("agent_id", agent_id)
        config.setdefault("model", "claude-sonnet-4-20250514")
        config.setdefault("system_prompt", "You are a helpful assistant.")
        config.setdefault("tools", [])
        config.setdefault("max_context_tokens", 8000)
        config.setdefault("provider", "anthropic")
        self._agents[agent_id] = config
        logger.info(f"Registered agent: {agent_id} ({config['model']})")

    def unregister(self, agent_id: str):
        """Remove an agent from the registry."""
        self._agents.pop(agent_id, None)
        self._tasks.pop(agent_id, None)
        self._providers.pop(agent_id, None)

    def list(self) -> list[dict]:
        """List all registered agents with status."""
        result = []
        for agent_id, cfg in self._agents.items():
            entry = dict(cfg)
            entry["status"] = (
                "running"
                if agent_id in self._tasks and not self._tasks[agent_id].done()
                else "stopped"
            )
            entry["sessions_count"] = self._count_sessions(agent_id)
            result.append(entry)
        return result

    def get(self, agent_id: str) -> dict | None:
        """Get agent config by id."""
        return self._agents.get(agent_id)

    # ── Lifecycle ────────────────────────────────────────────

    async def spawn(self, agent_id: str, message: str = "") -> asyncio.Task:
        """Spawn a sub-agent as an asyncio.Task.

        If message is provided, sends it to the agent immediately.
        """
        if agent_id not in self._agents:
            raise ValueError(f"Unknown agent: {agent_id}. Available: {list(self._agents)}")

        if agent_id in self._tasks and not self._tasks[agent_id].done():
            logger.info(f"Agent {agent_id} already running")
            return self._tasks[agent_id]

        cfg = self._agents[agent_id]
        task = asyncio.create_task(
            self._run_agent(agent_id, cfg, message),
            name=f"agent-{agent_id}",
        )
        self._tasks[agent_id] = task
        logger.info(f"Spawned agent: {agent_id}")
        return task

    async def stop(self, agent_id: str):
        """Stop a running sub-agent."""
        if agent_id in self._tasks:
            self._tasks[agent_id].cancel()
            try:
                await self._tasks[agent_id]
            except asyncio.CancelledError:
                pass
            del self._tasks[agent_id]
            logger.info(f"Stopped agent: {agent_id}")

    async def stop_all(self):
        """Stop all running sub-agents."""
        for agent_id in list(self._tasks):
            await self.stop(agent_id)

    async def call(
        self,
        agent_id: str,
        session_id: str,
        message: str,
        injected_context: str | None = None,
    ) -> str:
        """Direct call to a sub-agent. Returns text response.

        Uses native anthropic.AsyncAnthropic with the agent's configured model,
        system prompt, and conversation history from SessionDB.
        History is loaded with a sliding token window to respect max_context_tokens.
        """
        cfg = self._agents.get(agent_id)
        if not cfg:
            return f"Error: unknown agent '{agent_id}'"

        provider = self._get_provider(agent_id, cfg)
        client = provider["client"]

        # Load conversation history from SessionDB (sliding token window)
        history: list[dict] = []
        if self._db:
            try:
                msgs = self._db.get_messages_as_conversation(session_id)
                max_tokens = cfg.get("max_context_tokens", 8000)
                total = 0
                trimmed = []
                # Walk backwards, trim when token budget exhausted
                for m in reversed(msgs or []):
                    t = len((m.get("content") or "")) // 4  # rough token estimate
                    if total + t > max_tokens:
                        break
                    trimmed.insert(0, {"role": m["role"], "content": m["content"]})
                    total += t
                history = trimmed
            except Exception as e:
                logger.warning(f"Failed to load history for {agent_id}/{session_id}: {e}")

        # System prompt + optional injected context from orchestrator
        system = cfg.get("system_prompt", "You are a helpful assistant.")
        if injected_context:
            system += f"\n\n[Context from orchestrator]:\n{injected_context}"

        messages = history + [{"role": "user", "content": message}]

        try:
            response = await client.messages.create(
                model=provider["model"],
                max_tokens=provider["max_tokens"],
                system=system,
                messages=messages,
            )
            reply = response.content[0].text

            # Store turn in SessionDB
            if self._db:
                try:
                    if not self._db.get_session(session_id):
                        self._db.create_session(
                            session_id=session_id,
                            source=f"agent:{agent_id}",
                            model=provider["model"],
                        )
                    self._db.append_message(session_id, "user", message)
                    self._db.append_message(session_id, "assistant", reply)
                except Exception as e:
                    logger.warning(f"Failed to save turn for {agent_id}/{session_id}: {e}")

            return reply

        except Exception as e:
            logger.error(f"Agent {agent_id} call failed: {e}", exc_info=True)
            return f"Error from agent '{agent_id}': {e}"

    async def stream(
        self,
        agent_id: str,
        session_id: str,
        message: str,
    ) -> AsyncIterator[str]:
        """Stream response from a sub-agent chunk by chunk.

        Yields text tokens as they arrive. Saves full response to SessionDB
        once the stream completes.
        """
        cfg = self._agents.get(agent_id)
        if not cfg:
            yield f"Error: unknown agent '{agent_id}'"
            return

        import anthropic
        provider = self._get_provider(agent_id, cfg)
        client: anthropic.AsyncAnthropic = provider["client"]

        # Load conversation history
        history: list[dict] = []
        if self._db:
            try:
                msgs = self._db.get_messages_as_conversation(session_id)
                history = [{"role": m["role"], "content": m["content"]} for m in (msgs or [])]
            except Exception as e:
                logger.warning(f"stream: failed to load history for {agent_id}/{session_id}: {e}")

        messages = history + [{"role": "user", "content": message}]
        full_reply: list[str] = []

        try:
            async with client.messages.stream(
                model=provider["model"],
                max_tokens=provider["max_tokens"],
                system=cfg.get("system_prompt", "You are a helpful assistant."),
                messages=messages,
            ) as s:
                async for chunk in s.text_stream:
                    full_reply.append(chunk)
                    yield chunk
        except Exception as e:
            logger.error(f"Agent {agent_id} stream failed: {e}", exc_info=True)
            yield f"\n[Error: {e}]"

        # Save full reply after stream completes
        reply_text = "".join(full_reply)
        if self._db and reply_text:
            try:
                if not self._db.get_session(session_id):
                    self._db.create_session(
                        session_id=session_id,
                        source=f"agent:{agent_id}",
                        model=provider["model"],
                    )
                self._db.append_message(session_id, "user", message)
                self._db.append_message(session_id, "assistant", reply_text)
            except Exception as e:
                logger.warning(f"stream: failed to save turn for {agent_id}/{session_id}: {e}")

    async def orchestrate(
        self,
        session_id: str,
        message: str,
    ) -> str:
        """Route message through orchestrator agent.

        Orchestrator can delegate to sub-agents via:
            DELEGATE: <agent_id> | <task>
        Multiple DELEGATE lines = parallel execution.
        Returns synthesized final answer.
        """
        if "orchestrator" not in self._agents:
            return "Error: orchestrator agent not registered. Add agent_configs/orchestrator.yaml"

        agents_info = json.dumps(
            [{"id": a["agent_id"], "description": a.get("description", "")}
             for a in self._agents.values() if a["agent_id"] != "orchestrator"],
            ensure_ascii=False, indent=2,
        )

        # Prepare orchestrator system prompt with agent list
        orch_cfg = self._agents["orchestrator"]
        orch_system = orch_cfg.get("system_prompt", "") + f"""

Available sub-agents:
{agents_info}

To delegate tasks use this format (one line per agent):
DELEGATE: <agent_id> | <task description>

Rules:
- Delegate only when specialized expertise is needed
- Multiple DELEGATE lines = run agents in parallel
- If no delegation needed, answer directly
"""
        # Temporarily patch orchestrator system prompt
        original_system = orch_cfg.get("system_prompt", "")
        orch_cfg["system_prompt"] = orch_system
        orch_reply = await self.call("orchestrator", session_id, message)
        orch_cfg["system_prompt"] = original_system  # restore

        # Parse delegation directives
        delegates = []
        for line in orch_reply.split("\n"):
            line = line.strip()
            if line.startswith("DELEGATE:"):
                parts = line[9:].split("|", 1)
                if len(parts) == 2:
                    aid = parts[0].strip()
                    task = parts[1].strip()
                    if aid in self._agents and aid != "orchestrator":
                        delegates.append((aid, task))

        if not delegates:
            return orch_reply  # orchestrator answered directly

        # Parallel execution of delegated tasks
        results = await asyncio.gather(
            *[self.call(aid, session_id, task) for aid, task in delegates],
            return_exceptions=True,
        )

        # Synthesize final answer
        results_text = "\n\n".join(
            f"[{aid}]: {res}" if not isinstance(res, Exception) else f"[{aid}]: ERROR: {res}"
            for (aid, _), res in zip(delegates, results)
        )
        synthesis_prompt = (
            f"Sub-agents completed their tasks:\n\n{results_text}\n\n"
            "Synthesize a final answer for the user. "
            "Combine results into a coherent response without mentioning internal steps."
        )
        return await self.call("orchestrator", session_id, synthesis_prompt)

    def broadcast_context(self, session_id: str, context: str):
        """Inject context into all sub-agents' memory (except orchestrator).

        Useful for sharing environment info or task goals across agents.
        """
        if not self._db:
            return
        for agent_id, cfg in self._agents.items():
            if agent_id == "orchestrator":
                continue
            try:
                if not self._db.get_session(session_id):
                    self._db.create_session(
                        session_id=session_id,
                        source=f"agent:{agent_id}",
                        model=cfg.get("model", ""),
                    )
                self._db.append_message(
                    session_id, "user",
                    f"[Broadcast context]: {context}",
                )
                logger.debug(f"Broadcast context sent to {agent_id}")
            except Exception as e:
                logger.warning(f"broadcast to {agent_id} failed: {e}")

    def share_context(
        self,
        from_agent: str,
        to_agent: str,
        session_id: str,
        query: str,
        limit: int = 5,
    ) -> int:
        """Search from_agent's memory for query, inject results into to_agent's context.

        Uses SQL LIKE over SessionDB to find relevant messages.
        Returns count of found fragments.
        """
        if not self._db:
            return 0
        try:
            rows = self._db._conn.execute(
                """SELECT role, content FROM messages
                   WHERE session_id = ? AND content LIKE ?
                   ORDER BY id DESC LIMIT ?""",
                [session_id, f"%{query}%", limit],
            ).fetchall()
            if not rows:
                return 0
            context_text = "\n".join(f"{r[0]}: {r[1][:200]}" for r in rows)
            if not self._db.get_session(session_id):
                self._db.create_session(
                    session_id=session_id,
                    source=f"agent:{to_agent}",
                    model=self._agents.get(to_agent, {}).get("model", ""),
                )
            self._db.append_message(
                session_id, "user",
                f"[Context from agent '{from_agent}' about '{query}']:\n{context_text}",
            )
            return len(rows)
        except Exception as e:
            logger.warning(f"share_context failed: {e}")
            return 0

    # ── Internal ─────────────────────────────────────────────

    def _get_provider(self, agent_id: str, cfg: dict) -> dict:
        """Get or create a native Anthropic client for this agent.

        Returns a dict with 'client' (anthropic.AsyncAnthropic), 'model',
        and 'max_tokens'. Does NOT use a custom provider class — calls the
        Anthropic SDK directly so we don't depend on Hermes provider plumbing.
        """
        if agent_id not in self._providers:
            import anthropic
            self._providers[agent_id] = {
                "client": anthropic.AsyncAnthropic(),
                "model": cfg.get("model", "claude-sonnet-4-20250514"),
                "max_tokens": cfg.get("max_tokens", 8096),
            }
        return self._providers[agent_id]

    def _count_sessions(self, agent_id: str) -> int:
        """Count sessions for an agent."""
        if not self._db:
            return 0
        try:
            with self._db._lock:
                cursor = self._db._conn.execute(
                    "SELECT COUNT(*) FROM sessions WHERE agent_id = ?",
                    (agent_id,),
                )
                return cursor.fetchone()[0]
        except Exception:
            return 0

    async def _run_agent(self, agent_id: str, cfg: dict, initial_message: str = ""):
        """Run agent as asyncio.Task. Minimal loop — exits after response.

        Uses native anthropic.AsyncAnthropic for the API call.
        Stores turn in SessionDB via append_message().
        """
        provider = self._get_provider(agent_id, cfg)
        client = provider["client"]
        session_id = cfg.get("session_id", f"agent-{agent_id}-task")

        messages: list[dict] = []
        if initial_message:
            messages.append({"role": "user", "content": initial_message})

        try:
            if not messages:
                return  # nothing to do

            response = await client.messages.create(
                model=provider["model"],
                max_tokens=provider["max_tokens"],
                system=cfg.get("system_prompt", "You are a helpful assistant."),
                messages=messages,
            )
            reply = response.content[0].text
            logger.info(f"[{agent_id}] {reply[:120]}...")

            # Store in SessionDB
            if self._db:
                try:
                    if not self._db.get_session(session_id):
                        self._db.create_session(
                            session_id=session_id,
                            source=f"agent:{agent_id}",
                            model=provider["model"],
                        )
                    self._db.append_message(session_id, "user", initial_message)
                    self._db.append_message(session_id, "assistant", reply)
                except Exception as e:
                    logger.warning(f"Failed to store message for {agent_id}: {e}")

        except asyncio.CancelledError:
            logger.info(f"Agent {agent_id} task cancelled")
            raise
        except Exception as e:
            logger.error(f"Agent {agent_id} task error: {e}", exc_info=True)


# ── Singleton ────────────────────────────────────────────────

_registry: AgentRegistry | None = None


def get_registry(db=None) -> AgentRegistry:
    """Get or create the global agent registry."""
    global _registry
    if _registry is None:
        _registry = AgentRegistry(db)
        _registry.load_all()
    elif db is not None and _registry._db is None:
        _registry.set_db(db)
    return _registry
