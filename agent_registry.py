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

    # List all agents
    for agent in registry.list():
        print(agent["agent_id"], agent["model"], agent["status"])
"""

import asyncio
import json
import logging
import os
from pathlib import Path
from typing import Any

import yaml

logger = logging.getLogger(__name__)

DEFAULT_CONFIG_DIR = Path(__file__).parent / "agent_configs"


class AgentRegistry:
    """Registry and orchestrator for sub-agents.

    Each agent is a YAML config + a record in SessionDB.
    Does NOT rewrite the agent loop — wraps it via asyncio.Task.
    """

    def __init__(self, db=None, config_dir: Path | None = None):
        self._db = db  # SessionDB — set later if None
        self._config_dir = config_dir or DEFAULT_CONFIG_DIR
        self._agents: dict[str, dict] = {}     # agent_id -> config
        self._tasks: dict[str, asyncio.Task] = {}  # agent_id -> running task
        self._providers: dict[str, Any] = {}   # agent_id -> AnthropicProvider

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
        stream: bool = False,
    ) -> str:
        """Direct call to a sub-agent — returns its text response.

        Uses the agent's AnthropicProvider with its configured model,
        system prompt, and conversation history from SessionDB.
        """
        cfg = self._agents.get(agent_id)
        if not cfg:
            return f"Error: unknown agent '{agent_id}'"

        provider = self._get_provider(agent_id, cfg)

        # Load conversation history from SessionDB
        history = []
        if self._db:
            try:
                msgs = self._db.get_messages_as_conversation(session_id)
                history = [
                    {"role": m.get("role", "user"), "content": m.get("content", "")}
                    for m in (msgs or [])
                ]
            except Exception as e:
                logger.warning(f"Failed to load history for {agent_id}/{session_id}: {e}")

        # Add current message
        history.append({"role": "user", "content": message})

        try:
            response = await provider.complete(
                messages=history,
                system=cfg.get("system_prompt", ""),
                tools=None,  # Tools handled by the agent loop, not here
                stream=stream,
            )
            return response.text
        except Exception as e:
            logger.error(f"Agent {agent_id} call failed: {e}")
            return f"Error: {e}"

    # ── Internal ─────────────────────────────────────────────

    def _get_provider(self, agent_id: str, cfg: dict):
        """Get or create an AnthropicProvider for an agent."""
        if agent_id not in self._providers:
            from providers.anthropic_provider import AnthropicProvider

            self._providers[agent_id] = AnthropicProvider(
                model=cfg["model"],
                max_tokens=cfg.get("max_tokens", 8096),
                thinking_budget=cfg.get("thinking_budget"),
            )
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
        """Run an agent's conversation loop as an asyncio.Task.

        This is a simplified loop — for full tool_use integration,
        use the main agent loop wrapped with the provider.
        """
        provider = self._get_provider(agent_id, cfg)
        messages: list[dict] = []

        if initial_message:
            messages.append({"role": "user", "content": initial_message})

        # Simple chat loop (no tool_use iteration here — that's for
        # the full agent loop integration via run_agent.py)
        while True:
            try:
                if not messages:
                    await asyncio.sleep(1)
                    continue

                response = await provider.complete(
                    messages=messages,
                    system=cfg.get("system_prompt", ""),
                    tools=cfg.get("tools"),
                )

                messages.append({"role": "assistant", "content": response.text})
                logger.info(f"[{agent_id}] {response.text[:100]}...")

                # Store in SessionDB
                if self._db:
                    try:
                        session_id = cfg.get("session_id", f"agent-{agent_id}")
                        self._db.add_message(session_id, "user", messages[-2]["content"])
                        self._db.add_message(session_id, "assistant", response.text)
                    except Exception as e:
                        logger.warning(f"Failed to store message: {e}")

                await asyncio.sleep(0.1)

            except asyncio.CancelledError:
                logger.info(f"Agent {agent_id} cancelled")
                break
            except Exception as e:
                logger.error(f"Agent {agent_id} error: {e}")
                await asyncio.sleep(5)


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
