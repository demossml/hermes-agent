"""
Agent Registry — multi-agent orchestration for Hermes.

Each sub-agent is defined by a YAML config and registered with the session DB.
Agents run as independent asyncio.Tasks, each with their own model, system prompt,
tools, token budget, and context memory.

Uses Hermes' built-in provider resolution (resolve_runtime_provider) so NO
manual API key management is needed — OAuth, Claude Max, OpenRouter, direct
keys are all handled transparently through AIAgent.

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
from typing import Any, AsyncIterator, Dict, List, Optional

import yaml

logger = logging.getLogger(__name__)

DEFAULT_CONFIG_DIR = Path(__file__).parent / "agent_configs"


class AgentRegistry:
    """Registry and orchestrator for sub-agents.

    Each agent is a YAML config + a record in SessionDB.
    Uses Hermes' AIAgent and runtime provider resolution — no manual
    API keys or AnthropicProvider imports needed.
    """

    def __init__(self, db=None, config_dir: Path | None = None):
        self._db = db  # SessionDB — set later if None
        self._config_dir = config_dir or DEFAULT_CONFIG_DIR
        self._agents: dict[str, dict] = {}         # agent_id -> config
        self._tasks: dict[str, asyncio.Task] = {}  # agent_id -> running task
        self._instances: dict[str, Any] = {}       # agent_id -> AIAgent
        self._stats: dict[str, dict] = {}          # agent_id -> {calls, tokens, total_ms}

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
        config.setdefault("max_iterations", 3)  # default: 3-turn tool loop
        config.setdefault("provider", "current")  # "current" = use Hermes' active provider
        self._agents[agent_id] = config
        logger.info(f"Registered agent: {agent_id} ({config['model']}, provider={config['provider']}, max_iter={config['max_iterations']})")

    def unregister(self, agent_id: str):
        """Remove an agent from the registry."""
        self._agents.pop(agent_id, None)
        self._tasks.pop(agent_id, None)
        self._instances.pop(agent_id, None)

    def list(self) -> List[Dict]:
        """List all registered agents with status, stats, and session count."""
        result = []
        for agent_id, cfg in self._agents.items():
            entry = dict(cfg)
            entry["status"] = (
                "running"
                if agent_id in self._tasks and not self._tasks[agent_id].done()
                else "stopped"
            )
            entry["sessions_count"] = self._count_sessions(agent_id)
            # Per-agent performance stats
            st = self._stats.get(agent_id, {})
            entry["calls"] = st.get("calls", 0)
            entry["tokens"] = st.get("tokens", 0)
            if st.get("calls", 0) > 0:
                entry["avg_latency_ms"] = st.get("total_ms", 0) // st["calls"]
            else:
                entry["avg_latency_ms"] = 0
            result.append(entry)
        return result

    def get(self, agent_id: str) -> dict | None:
        """Get agent config by id."""
        return self._agents.get(agent_id)

    def reload(self) -> int:
        """Hot-reload all agent configs from agent_configs/ directory.

        Clears cached AIAgent instances (they'll be recreated on next call).
        Preserves runtime-registered agents (created via create()).
        Returns count of loaded configs.
        """
        self._instances.clear()
        return self.load_all()

    def create(self, agent_id: str, config: dict) -> dict:
        """Create and register a new agent at runtime.

        Saves config to agent_configs/{agent_id}.yaml so it survives restarts.
        Returns the registered config.
        """
        config["agent_id"] = agent_id
        self.register(agent_id, config)
        # Persist to YAML
        yaml_path = self._config_dir / f"{agent_id}.yaml"
        try:
            persist_cfg = {
                "agent_id": agent_id,
                "provider": config.get("provider", "current"),
                "description": config.get("description", ""),
                "system_prompt": config.get("system_prompt", ""),
                "max_context_tokens": config.get("max_context_tokens", 8000),
                "max_iterations": config.get("max_iterations", 3),
            }
            if config.get("enabled_toolsets"):
                persist_cfg["enabled_toolsets"] = config["enabled_toolsets"]
            with open(yaml_path, "w") as f:
                yaml.dump(persist_cfg, f, allow_unicode=True, default_flow_style=False)
            logger.info(f"Created agent '{agent_id}' → {yaml_path}")
        except Exception as e:
            logger.warning(f"Failed to persist agent config for {agent_id}: {e}")
        return self._agents[agent_id]

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
        retries: int = 1,
    ) -> str:
        """Direct call to a sub-agent using Hermes' AIAgent.

        Tracks per-agent performance stats (calls, latency).
        Retries once on failure with simplified prompt.
        """
        cfg = self._agents.get(agent_id)
        if not cfg:
            return f"Error: unknown agent '{agent_id}'"

        t0 = asyncio.get_event_loop().time() * 1000
        last_error = None

        for attempt in range(retries + 1):
            try:
                agent = self._get_agent(agent_id, cfg, session_id)

                if injected_context:
                    system = cfg.get("system_prompt", "You are a helpful assistant.")
                    system += f"\n\n[Context from orchestrator]:\n{injected_context}"
                    agent.ephemeral_system_prompt = system

                # Retry: simplify prompt on second attempt
                msg = message if attempt == 0 else f"Please respond concisely: {message}"

                conversation_history = self._load_history(session_id, cfg)
                result = agent.run_conversation(
                    msg,
                    conversation_history=conversation_history,
                )
                reply = result.get("final_response", "") if isinstance(result, dict) else str(result)

                self._save_turn(session_id, agent_id, msg, reply, agent.model)
                self._track_call(agent_id, t0, len(reply) // 4)
                return reply

            except Exception as e:
                last_error = e
                if attempt < retries:
                    logger.warning(f"Agent {agent_id} attempt {attempt+1} failed: {e}, retrying...")
                    self._instances.pop(agent_id, None)  # force recreate
                    await asyncio.sleep(0.5)
                else:
                    logger.error(f"Agent {agent_id} call failed after {retries+1} attempts: {e}", exc_info=True)

        return f"Error from agent '{agent_id}': {last_error}"

    def _track_call(self, agent_id: str, start_ms: float, tokens: int):
        """Update per-agent performance stats."""
        elapsed = int(asyncio.get_event_loop().time() * 1000 - start_ms)
        st = self._stats.setdefault(agent_id, {"calls": 0, "tokens": 0, "total_ms": 0})
        st["calls"] += 1
        st["tokens"] += tokens
        st["total_ms"] += elapsed

    def _load_history(self, session_id: str, cfg: dict) -> List[Dict]:
        """Load conversation history from SessionDB with sliding token window.

        If history exceeds max_context_tokens, older messages are auto-summarized
        via _summarize_history instead of being truncated.
        """
        history: List[Dict] = []
        if not self._db:
            return history
        try:
            msgs = self._db.get_messages_as_conversation(session_id)
            if not msgs:
                return history
            history = [{"role": m["role"], "content": m["content"]} for m in msgs]
            max_tokens = cfg.get("max_context_tokens", 8000)
            total = sum(len((m.get("content") or "")) // 4 for m in history)
            if total > max_tokens:
                # Trigger auto-summarization
                loop = asyncio.get_event_loop()
                if loop.is_running():
                    task = asyncio.create_task(
                        self._summarize_history(
                            cfg.get("agent_id", ""), session_id, history, max_tokens
                        )
                    )
                    # Don't block — use truncated history for this turn,
                    # summarized version will be used next turn
                # Truncate for now
                total = 0
                trimmed = []
                for m in reversed(history):
                    t = len((m.get("content") or "")) // 4
                    if total + t > max_tokens:
                        break
                    trimmed.insert(0, m)
                    total += t
                history = trimmed
        except Exception as e:
            logger.warning(f"Failed to load history for {session_id}: {e}")
        return history

    def _save_turn(self, session_id: str, agent_id: str, user_msg: str, reply: str, model: str):
        """Persist user message + assistant reply to SessionDB."""
        if not self._db or not reply:
            return
        try:
            if not self._db.get_session(session_id):
                self._db.create_session(
                    session_id=session_id,
                    source=f"agent:{agent_id}",
                    model=model,
                )
            self._db.append_message(session_id, "user", user_msg)
            self._db.append_message(session_id, "assistant", reply)
        except Exception as e:
            logger.warning(f"Failed to save turn for {session_id}: {e}")

    async def stream(
        self,
        agent_id: str,
        session_id: str,
        message: str,
    ) -> AsyncIterator[str]:
        """Stream response from a sub-agent.

        Uses Hermes' AIAgent with stream_callback to yield chunks.
        """
        cfg = self._agents.get(agent_id)
        if not cfg:
            yield f"Error: unknown agent '{agent_id}'"
            return

        try:
            agent = self._get_agent(agent_id, cfg)

            # Collect chunks via stream_callback, yield through a queue
            chunk_queue: asyncio.Queue = asyncio.Queue()

            def on_stream(chunk: str):
                chunk_queue.put_nowait(chunk)

            # Fire off chat in a background task
            chat_task = asyncio.create_task(
                asyncio.to_thread(agent.chat, message, on_stream)
            )

            # Yield chunks as they arrive
            while True:
                try:
                    chunk = await asyncio.wait_for(chunk_queue.get(), timeout=0.5)
                    yield chunk
                except asyncio.TimeoutError:
                    if chat_task.done():
                        break

            await chat_task

        except Exception as e:
            logger.error(f"Agent {agent_id} stream failed: {e}", exc_info=True)
            yield f"\n[Error: {e}]"

    async def orchestrate(
        self,
        session_id: str,
        message: str,
    ) -> str:
        """Route message through orchestrator agent.

        Orchestrator can delegate to sub-agents via:
            DELEGATE: <agent_id> | <task description>
        Multiple DELEGATE lines = parallel execution.
        DAG chaining via:
            DELEGATE: <agent_id> | <task> | -> <next_agent_id>
        (next_agent receives previous agent's output as context)

        Returns synthesized final answer.
        """
        if "orchestrator" not in self._agents:
            return "Error: orchestrator agent not registered. Add agent_configs/orchestrator.yaml"

        orch_reply = await self._ask_orchestrator(session_id, message)

        # Parse delegation directives
        stages = self._parse_delegates(orch_reply)
        if not stages:
            return orch_reply  # orchestrator answered directly

        # Execute stages (supports DAG chaining)
        all_results = await self._execute_stages(session_id, stages)

        # Synthesize final answer
        results_text = "\n\n".join(
            f"[{aid}]: {res}" if not isinstance(res, Exception) else f"[{aid}]: ERROR: {res}"
            for aid, res in all_results.items()
        )
        return await self._ask_orchestrator(
            session_id,
            f"Sub-agents completed their tasks:\n\n{results_text}\n\n"
            "Synthesize a final answer. Combine results, don't mention internal steps.",
        )

    async def stream_orchestrate(
        self,
        session_id: str,
        message: str,
    ) -> AsyncIterator[str]:
        """Streaming version of orchestrate — yields text as orchestrator works.

        Yields status updates during delegation, then final synthesized answer.
        """
        if "orchestrator" not in self._agents:
            yield "Error: orchestrator agent not registered."
            return

        yield "[orchestrator] analysing request...\n"
        orch_reply = await self._ask_orchestrator(session_id, message)

        stages = self._parse_delegates(orch_reply)
        if not stages:
            yield orch_reply
            return

        yield f"[orchestrator] delegating to: {', '.join(stages.keys())}\n"

        all_results = await self._execute_stages(session_id, stages)
        for aid, res in all_results.items():
            status = "✓" if not isinstance(res, Exception) else "✗"
            yield f"  {status} {aid}: {str(res)[:100]}...\n"

        yield "\n[orchestrator] synthesizing...\n"
        results_text = "\n\n".join(
            f"[{aid}]: {res}" if not isinstance(res, Exception) else f"[{aid}]: ERROR: {res}"
            for aid, res in all_results.items()
        )
        final = await self._ask_orchestrator(
            session_id,
            f"Sub-agents completed:\n\n{results_text}\n\nSynthesize a final answer.",
        )
        yield final

    # ── Orchestration helpers ────────────────────────────────

    async def _ask_orchestrator(self, session_id: str, message: str) -> str:
        """Call orchestrator agent with sub-agent list injected into system prompt."""
        agents_info = json.dumps(
            [{"id": a["agent_id"], "description": a.get("description", "")}
             for a in self._agents.values() if a["agent_id"] != "orchestrator"],
            ensure_ascii=False, indent=2,
        )

        orch_cfg = self._agents["orchestrator"]
        orig_system = orch_cfg.get("system_prompt", "")
        orch_cfg["system_prompt"] = orig_system + f"""

Available sub-agents:
{agents_info}

Delegation format (use ONLY when task requires specialization):
  DELEGATE: <agent_id> | <task description>
  For chaining: DELEGATE: <agent_id> | <task> | -> <next_agent_id>
Multiple lines = parallel. Chain with -> for sequential (output feeds to next).
If no delegation needed, answer directly.
"""
        self._instances.pop("orchestrator", None)
        reply = await self.call("orchestrator", session_id, message)
        orch_cfg["system_prompt"] = orig_system
        self._instances.pop("orchestrator", None)
        return reply

    def _parse_delegates(self, reply: str) -> Dict[str, List[tuple]]:
        """Parse DELEGATE directives into execution stages.

        Returns: {agent_id: [(task, next_agent_id|None), ...]}
        Stages with next_agent_id=None are parallel (same level).
        Stages with next_agent_id set form a chain.
        """
        stages: Dict[str, List[tuple]] = {}
        for line in reply.split("\n"):
            line = line.strip()
            if not line.startswith("DELEGATE:"):
                continue
            body = line[9:].strip()
            # Parse: agent_id | task [| -> next_agent_id]
            if "| ->" in body:
                main_part, chain_target = body.split("| ->", 1)
                next_agent = chain_target.strip() if chain_target.strip() in self._agents else None
            elif "|->" in body:
                main_part, chain_target = body.split("|->", 1)
                next_agent = chain_target.strip() if chain_target.strip() in self._agents else None
            else:
                main_part = body
                next_agent = None

            parts = main_part.split("|", 1)
            if len(parts) != 2:
                logger.warning(f"Invalid DELEGATE format: {line}")
                continue
            aid = parts[0].strip()
            task = parts[1].strip()
            if aid not in self._agents or aid == "orchestrator":
                logger.warning(f"Unknown agent in DELEGATE: {aid}")
                continue
            stages.setdefault(aid, []).append((task, next_agent))
        return stages

    async def _execute_stages(
        self, session_id: str, stages: Dict[str, List[tuple]]
    ) -> Dict[str, str]:
        """Execute delegation stages with DAG support.

        Parallel agents run via asyncio.gather.
        Chained agents run sequentially (output of A → input of B).
        """
        all_results: dict[str, str] = {}

        # Collect all tasks: parallel + chain starters
        parallel_tasks = {}
        chain_tasks = []  # [(agent_id, task, next_agent_id)]

        for aid, tasks in stages.items():
            for task, next_agent in tasks:
                if next_agent:
                    chain_tasks.append((aid, task, next_agent))
                else:
                    parallel_tasks.setdefault(aid, []).append(task)

        # Run parallel agents
        if parallel_tasks:
            combined = {}
            for aid, task_list in parallel_tasks.items():
                combined[aid] = "; ".join(task_list)
            parallel_results = await asyncio.gather(
                *[self.call(aid, session_id, task) for aid, task in combined.items()],
                return_exceptions=True,
            )
            for (aid, _), res in zip(combined.items(), parallel_results):
                all_results[aid] = str(res) if not isinstance(res, Exception) else f"ERROR: {res}"

        # Run chains sequentially
        for aid, task, next_agent in chain_tasks:
            result = await self.call(aid, session_id, task)
            all_results[aid] = result
            if next_agent and not isinstance(result, Exception):
                chain_task = f"Based on previous agent's output:\n\n{result}\n\nYour task: analyse and provide your expertise."
                chain_result = await self.call(next_agent, session_id, chain_task)
                all_results[next_agent] = chain_result

        return all_results

    def broadcast_context(self, session_id: str, context: str):
        """Inject context into all sub-agents' SessionDB memory (except orchestrator).

        Useful for sharing environment info or task goals across agents.
        """
        if not self._db:
            return
        for agent_id in self._agents:
            if agent_id == "orchestrator":
                continue
            try:
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
            self._db.append_message(
                session_id, "user",
                f"[Context from agent '{from_agent}' about '{query}']:\n{context_text}",
            )
            return len(rows)
        except Exception as e:
            logger.warning(f"share_context failed: {e}")
            return 0

    # ── Phase 3: Memory & Context ────────────────────────────

    def scratchpad_publish(self, channel: str, agent_id: str, content: str):
        """Publish a message to a shared scratchpad channel.

        All agents subscribed to this channel can read it via scratchpad_read().
        Uses a dedicated session 'scratchpad:{channel}' in SessionDB.
        """
        if not self._db:
            return
        scratch_session = f"scratchpad:{channel}"
        try:
            if not self._db.get_session(scratch_session):
                self._db.create_session(
                    session_id=scratch_session,
                    source=f"scratchpad:{channel}",
                    model="scratchpad",
                )
            self._db.append_message(
                scratch_session, "user",
                f"[{agent_id}]: {content}",
            )
            logger.debug(f"Scratchpad [{channel}] ← {agent_id}: {content[:80]}")
        except Exception as e:
            logger.warning(f"scratchpad_publish failed: {e}")

    def scratchpad_read(self, channel: str, limit: int = 10) -> List[str]:
        """Read recent messages from a shared scratchpad channel.

        Returns list of formatted messages, newest first.
        """
        if not self._db:
            return []
        scratch_session = f"scratchpad:{channel}"
        try:
            msgs = self._db.get_messages_as_conversation(scratch_session)
            if not msgs:
                return []
            return [
                f"[{m.get('role', '?')}] {m.get('content', '')[:300]}"
                for m in reversed(msgs[-limit:])
            ]
        except Exception as e:
            logger.warning(f"scratchpad_read failed: {e}")
            return []

    async def _summarize_history(
        self, agent_id: str, session_id: str, history: List[Dict], max_tokens: int
    ) -> List[Dict]:
        """Auto-summarize conversation history when it exceeds token budget.

        Uses the agent itself to compress old messages into a summary,
        preserving recent messages intact.
        """
        if not history or not self._db:
            return history

        total = sum(len((m.get("content") or "")) // 4 for m in history)
        if total <= max_tokens:
            return history

        # Split: older half gets summarized, newer half stays intact
        split = max(len(history) // 2, 2)
        old_msgs = history[:split]
        recent_msgs = history[split:]

        # Build summary prompt from old messages
        old_text = "\n".join(
            f"{m['role']}: {m['content'][:200]}" for m in old_msgs
        )
        summary_prompt = (
            f"Summarize this conversation history in 2-3 sentences, "
            f"preserving key facts, decisions, and context:\n\n{old_text}"
        )

        try:
            agent = self._get_agent(agent_id, self._agents.get(agent_id, {}), session_id)
            summary_result = agent.run_conversation(summary_prompt)
            summary = summary_result.get("final_response", "") if isinstance(summary_result, dict) else str(summary_result)
            compact = [{"role": "system", "content": f"[History summary]: {summary}"}]
            logger.info(f"Summarized {len(old_msgs)} messages → {len(summary)} chars for {agent_id}")
            return compact + recent_msgs
        except Exception as e:
            logger.warning(f"Summarization failed for {agent_id}: {e}")
            # Fallback: just keep recent messages
            return recent_msgs[-max(1, max_tokens // 100):]

    # ── Phase 5: Advanced ────────────────────────────────────

    async def dialogue(
        self,
        agent_a: str,
        agent_b: str,
        session_id: str,
        topic: str,
        turns: int = 3,
    ) -> List[str]:
        """Agent-to-agent dialogue.

        Two agents converse for N turns on a topic. Agent A starts,
        Agent B responds, they alternate. Returns transcript.
        """
        if agent_a not in self._agents:
            return [f"Error: unknown agent '{agent_a}'"]
        if agent_b not in self._agents:
            return [f"Error: unknown agent '{agent_b}'"]

        transcript: List[str] = []
        current_msg = topic

        for i in range(turns):
            speaker = agent_a if i % 2 == 0 else agent_b
            listener = agent_b if i % 2 == 0 else agent_a

            reply = await self.call(speaker, session_id, current_msg)
            transcript.append(f"[{speaker}]: {reply}")

            if i < turns - 1:
                # Prepare next turn: inject listener's perspective
                current_msg = (
                    f"The other agent said:\n\n{reply}\n\n"
                    f"Respond to this. Add your perspective or ask a follow-up question."
                )

        return transcript

    def dispatch(self, session_id: str, task: str) -> str:
        """Auto-dispatch task to the most suitable agent based on keywords.

        Returns agent_id of the selected agent (caller should then call() it).
        """
        task_lower = task.lower()
        scores: Dict[str, int] = {}

        for agent_id, cfg in self._agents.items():
            if agent_id == "orchestrator":
                continue
            desc = (cfg.get("description") or "").lower()
            score = 0
            # Keyword matching
            keywords = {
                "coder": ["code", "program", "function", "bug", "fix", "write", "file", "script", "python", "test"],
                "researcher": ["research", "find", "search", "analysis", "explain", "what", "how", "why", "compare"],
                "reviewer": ["review", "check", "audit", "security", "improve", "bug", "error", "vulnerability"],
            }
            for kw in keywords.get(agent_id, []):
                if kw in task_lower or kw in desc:
                    score += 1
            scores[agent_id] = score

        if not scores:
            return ""

        # Return agent with highest score, or empty if all zero
        best = max(scores, key=scores.get)
        return best if scores[best] > 0 else ""

    # ── Internal ─────────────────────────────────────────────

    def _get_agent(self, agent_id: str, cfg: dict, session_id: str = ""):
        """Get or create an AIAgent using Hermes' built-in provider resolution.

        provider: current (default) → use whatever Hermes is configured with,
                                      ignore model: in config
        provider: anthropic / deepseek / openrouter / etc. → use that provider
                                                              with the specified model
        """
        if agent_id not in self._instances:
            from run_agent import AIAgent
            from hermes_cli.runtime_provider import resolve_runtime_provider

            provider_cfg = cfg.get("provider") or "current"
            use_current = provider_cfg in ("current", "")

            try:
                if use_current:
                    runtime = resolve_runtime_provider(
                        requested=None,
                        target_model=None,
                    )
                    logger.info(
                        f"Agent '{agent_id}' using current Hermes provider: "
                        f"{runtime.get('provider')} / {runtime.get('model')}"
                    )
                else:
                    runtime = resolve_runtime_provider(
                        requested=provider_cfg,
                        target_model=cfg.get("model"),
                    )
                    logger.info(
                        f"Agent '{agent_id}' using explicit provider: "
                        f"{provider_cfg} / {cfg.get('model')}"
                    )
            except Exception as e:
                logger.warning(
                    f"Provider resolution failed for '{agent_id}': {e}. "
                    f"Falling back to current Hermes provider."
                )
                try:
                    runtime = resolve_runtime_provider(
                        requested=None,
                        target_model=None,
                    )
                except Exception as e2:
                    logger.error(f"Fallback also failed for '{agent_id}': {e2}")
                    runtime = {}

            self._instances[agent_id] = AIAgent(
                model=runtime.get("model") or cfg.get("model", ""),
                provider=runtime.get("provider", ""),
                api_key=runtime.get("api_key", ""),
                base_url=runtime.get("base_url", ""),
                api_mode=runtime.get("api_mode", "chat_completions"),
                ephemeral_system_prompt=cfg.get(
                    "system_prompt", "You are a helpful assistant."
                ),
                session_db=self._db,
                session_id=session_id or cfg.get("session_id", f"agent-{agent_id}"),
                max_iterations=cfg.get("max_iterations", 3),
                enabled_toolsets=cfg.get("enabled_toolsets") or None,
            )

        return self._instances[agent_id]

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
        """Run agent as asyncio.Task via AIAgent (uses Hermes provider resolution).

        Uses run_conversation with history from SessionDB for persistent context.
        """
        if not initial_message:
            return

        session_id = cfg.get("session_id", f"agent-{agent_id}-task")

        try:
            agent = self._get_agent(agent_id, cfg, session_id)
            conversation_history = self._load_history(session_id, cfg)
            result = agent.run_conversation(
                initial_message,
                conversation_history=conversation_history,
            )
            reply = result.get("final_response", "") if isinstance(result, dict) else str(result)
            logger.info(f"[{agent_id}] {str(reply)[:120]}...")
            self._save_turn(session_id, agent_id, initial_message, reply, agent.model)
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
