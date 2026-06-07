"""
SubAgentManager — isolated memory and context windows for sub-agents.

Each sub-agent:
- Has its own persistent session in SessionDB (agent_id column)
- Has its own conversation history, trimmed to max_context_tokens
- Can be called directly (@agent_id) or delegated to (tool call)
- Preserves memory across calls (history persists in SessionDB)
"""

import logging
from pathlib import Path
from typing import Any
from datetime import datetime

logger = logging.getLogger(__name__)


class SubAgentManager:
    """Manages sub-agents with isolated memory and context windows.

    Architecture:
        User → Main Agent → delegate_to_agent(coder, "напиши код")
                              → SubAgentManager.delegate()
                                → loads agent_id history from SessionDB
                                → trims to max_context_tokens
                                → calls AnthropicProvider
                                → saves response to history
                                → returns text

    Memory isolation:
        Each sub-agent has one persistent session per agent_id.
        History is stored in SessionDB messages table, keyed by agent_id.
        Between calls, the sub-agent remembers everything up to
        max_context_tokens.
    """

    def __init__(self, db=None, config_dir: Path | None = None):
        self._db = db
        self._config_dir = config_dir
        self._agents: dict[str, dict] = {}        # agent_id -> config
        self._providers: dict[str, Any] = {}        # agent_id -> AnthropicProvider
        self._sessions: dict[str, str] = {}         # agent_id -> session_id

    def set_db(self, db):
        self._db = db

    # ── Config ────────────────────────────────────────────────

    def load_agents(self, directory: str | Path):
        """Load agent configs from YAML directory."""
        import yaml
        directory = Path(directory)
        for f in sorted(directory.glob("*.yaml")):
            with open(f) as fh:
                cfg = yaml.safe_load(fh)
            aid = cfg.get("agent_id", f.stem)
            cfg.setdefault("agent_id", aid)
            cfg.setdefault("model", "claude-sonnet-4-20250514")
            cfg.setdefault("system_prompt", "You are a helpful assistant.")
            cfg.setdefault("max_context_tokens", 8000)
            cfg.setdefault("max_response_tokens", 4096)
            self._agents[aid] = cfg
            logger.info(f"Loaded agent: {aid} ({cfg['model']}, max={cfg['max_context_tokens']} tok)")

    def create_agent(
        self,
        agent_id: str,
        model: str,
        system_prompt: str,
        max_context_tokens: int = 8000,
        max_response_tokens: int = 4096,
        description: str = "",
    ) -> dict:
        """Create a new sub-agent dynamically. Persists config to YAML.

        Called by the create_subagent tool when the main agent wants
        to spawn a new sub-agent on the fly.

        Returns dict with status and created agent info.
        """
        if not agent_id or not agent_id.strip():
            return {"error": "agent_id is required"}
        if not model or not model.strip():
            return {"error": "model is required"}
        if not system_prompt or not system_prompt.strip():
            return {"error": "system_prompt is required"}

        agent_id = agent_id.strip().lower().replace(" ", "-")

        if agent_id in self._agents:
            return {
                "error": f"Agent '{agent_id}' already exists",
                "existing_config": {
                    "model": self._agents[agent_id]["model"],
                    "max_tokens": self._agents[agent_id]["max_context_tokens"],
                },
            }

        # Build config
        cfg = {
            "agent_id": agent_id,
            "model": model.strip(),
            "system_prompt": system_prompt.strip(),
            "max_context_tokens": max_context_tokens,
            "max_response_tokens": max_response_tokens,
        }
        if description:
            cfg["description"] = description.strip()

        # Register in memory
        self._agents[agent_id] = cfg

        # Persist to YAML file
        self._save_agent_config(agent_id, cfg)

        # Pre-create session in DB
        if self._db:
            self.get_or_create_session(agent_id)

        logger.info(
            f"Created agent: {agent_id} ({model}, "
            f"{max_context_tokens} tok context)"
        )

        return {
            "status": "created",
            "agent_id": agent_id,
            "model": model,
            "max_context_tokens": max_context_tokens,
            "message": (
                f"Sub-agent '{agent_id}' created. "
                f"Use delegate_to_agent('{agent_id}', ...) to call it."
            ),
        }

    def _save_agent_config(self, agent_id: str, cfg: dict):
        """Persist agent config to YAML file."""
        import yaml
        config_path = self._config_dir / f"{agent_id}.yaml" if self._config_dir else None
        if not config_path:
            return
        config_path.parent.mkdir(parents=True, exist_ok=True)
        with open(config_path, "w") as f:
            yaml.dump(cfg, f, allow_unicode=True, default_flow_style=False)

    def remove_agent(self, agent_id: str) -> dict:
        """Remove a dynamically created sub-agent."""
        if agent_id not in self._agents:
            return {"error": f"Unknown agent: {agent_id}"}

        # Don't remove built-in agents (loaded from YAML)
        config_path = self._config_dir / f"{agent_id}.yaml" if self._config_dir else None
        is_dynamic = config_path and config_path.exists()

        del self._agents[agent_id]
        self._providers.pop(agent_id, None)
        self._sessions.pop(agent_id, None)

        if is_dynamic:
            config_path.unlink(missing_ok=True)

        logger.info(f"Removed agent: {agent_id}")
        return {"status": "removed", "agent_id": agent_id}

    def list_agents(self) -> list[dict]:
        """Return agent summaries for the orchestrator's system prompt."""
        return [
            {
                "agent_id": aid,
                "model": cfg["model"],
                "description": self._extract_description(cfg),
                "max_tokens": cfg["max_context_tokens"],
            }
            for aid, cfg in self._agents.items()
        ]

    def _extract_description(self, cfg: dict) -> str:
        """Extract first meaningful line from system_prompt as description."""
        sp = cfg.get("system_prompt", "")
        for line in sp.split("\n"):
            line = line.strip()
            if line and not line.startswith("#") and len(line) > 10:
                return line[:100]
        return cfg.get("agent_id", "agent")

    # ── Sessions ──────────────────────────────────────────────

    def get_or_create_session(self, agent_id: str) -> str:
        """Get or create a persistent session for a sub-agent.

        One session per agent_id — the sub-agent's memory persists
        across all calls. This is the key to isolated memory:
        every agent_id gets its own conversation thread.
        """
        if agent_id in self._sessions:
            return self._sessions[agent_id]

        if self._db:
            # Check for existing session
            with self._db._lock:
                cursor = self._db._conn.execute(
                    "SELECT id FROM sessions WHERE agent_id = ? "
                    "ORDER BY started_at DESC LIMIT 1",
                    (agent_id,),
                )
                row = cursor.fetchone()
                if row:
                    self._sessions[agent_id] = row[0]
                    return row[0]

        # Create new session
        import uuid
        session_id = f"agent-{agent_id}-{uuid.uuid4().hex[:8]}"
        if self._db:
            self._db.add_session(
                session_id=session_id,
                source="subagent",
                model=self._agents.get(agent_id, {}).get("model", ""),
                agent_id=agent_id,
                started_at=datetime.now().timestamp(),
            )
        self._sessions[agent_id] = session_id
        return session_id

    # ── History ───────────────────────────────────────────────

    def get_history(self, agent_id: str) -> list[dict]:
        """Get conversation history for a sub-agent, trimmed to max_context_tokens."""
        cfg = self._agents.get(agent_id, {})
        max_tokens = cfg.get("max_context_tokens", 8000)
        system_prompt = cfg.get("system_prompt", "")

        # System prompt consumes tokens too
        available = max_tokens - self._count_tokens(system_prompt)

        if not self._db:
            return []

        session_id = self.get_or_create_session(agent_id)
        try:
            msgs = self._db.get_messages_as_conversation(session_id)
        except Exception:
            return []

        history = [
            {"role": m.get("role", "user"), "content": m.get("content", "")}
            for m in (msgs or [])
            if m.get("role") in ("user", "assistant")
        ]

        return self._trim_history(history, available)

    def save_message(self, agent_id: str, role: str, content: str):
        """Persist a message to the sub-agent's session."""
        if not self._db:
            return
        session_id = self.get_or_create_session(agent_id)
        try:
            self._db.add_message(
                session_id=session_id,
                role=role,
                content=content,
            )
        except Exception as e:
            logger.warning(f"Failed to save {role} for {agent_id}: {e}")

    # ── Delegation (main agent calls sub-agent) ───────────────

    async def delegate(self, agent_id: str, message: str) -> str:
        """Delegate a task to a sub-agent. Called by the main agent.

        Flow:
        1. Load sub-agent's isolated history from SessionDB
        2. Trim to fit max_context_tokens
        3. Call sub-agent's LLM
        4. Save response to sub-agent's history
        5. Return response to main agent
        """
        if agent_id not in self._agents:
            return f"Error: unknown agent '{agent_id}'. Available: {list(self._agents)}"

        cfg = self._agents[agent_id]
        provider = self._get_provider(agent_id, cfg)

        # Load ISOLATED history — only this agent's messages
        history = self.get_history(agent_id)
        history.append({"role": "user", "content": message})

        # Save user message
        self.save_message(agent_id, "user", message)

        try:
            response = await provider.complete(
                messages=history,
                system=cfg.get("system_prompt", ""),
            )
            text = response.text

            # Save assistant response to sub-agent's memory
            self.save_message(agent_id, "assistant", text)

            return text
        except Exception as e:
            logger.error(f"Delegate to {agent_id} failed: {e}")
            return f"Error calling {agent_id}: {e}"

    # ── Direct call (user → sub-agent bypassing main) ─────────

    async def direct_call(self, agent_id: str, message: str, user_id: str = "") -> str:
        """Direct call from user to sub-agent (bypasses main agent).

        Used when user sends @agent_id in gateway.
        """
        return await self.delegate(agent_id, message)

    # ── Internal ──────────────────────────────────────────────

    def _get_provider(self, agent_id: str, cfg: dict):
        """Get or create an AnthropicProvider for a sub-agent."""
        if agent_id not in self._providers:
            from providers.anthropic_provider import AnthropicProvider

            self._providers[agent_id] = AnthropicProvider(
                model=cfg["model"],
                max_tokens=cfg.get("max_response_tokens", 4096),
                thinking_budget=cfg.get("thinking_budget"),
            )
        return self._providers[agent_id]

    def _trim_history(self, messages: list[dict], max_tokens: int) -> list[dict]:
        """Trim conversation history to fit within max_tokens.

        Keeps the most recent messages. Always keeps the system-like
        first message if present. Drops oldest user/assistant pairs first.
        """
        if not messages:
            return []

        total = sum(self._count_tokens(m.get("content", "")) for m in messages)
        if total <= max_tokens:
            return messages

        # Keep first message (often contains important context/instructions)
        # and as many recent messages as fit.
        trimmed = messages[:1] if len(messages) > 1 else messages[:]
        remaining = messages[1:]

        # Take from end (most recent)
        result = list(trimmed)
        current_tokens = sum(self._count_tokens(m["content"]) for m in result)

        for msg in reversed(remaining):
            tok = self._count_tokens(msg.get("content", ""))
            if current_tokens + tok <= max_tokens:
                # Insert before the "take from end" gap
                result.insert(len(trimmed), msg)
                current_tokens += tok
            else:
                break

        # Re-sort by original order
        result.sort(key=lambda m: messages.index(m) if m in messages else 0)
        return result

    def _count_tokens(self, text: str) -> int:
        """Count tokens in text.

        Uses tiktoken if available (accurate for Claude/GPT),
        falls back to char/4 estimate.
        """
        if not text:
            return 0
        try:
            import tiktoken
            enc = tiktoken.get_encoding("cl100k_base")
            return len(enc.encode(text))
        except ImportError:
            pass
        # Rough estimate: ~4 chars per token for English, ~2 for Cyrillic
        return max(1, len(text) // 3)


# ── Singleton ────────────────────────────────────────────────

_manager: SubAgentManager | None = None


def get_subagent_manager(db=None, config_dir: Path | None = None) -> SubAgentManager:
    """Get or create the global sub-agent manager."""
    global _manager
    if _manager is None:
        if config_dir is None:
            config_dir = Path(__file__).parent / "agent_configs"
        _manager = SubAgentManager(db, config_dir)
        if config_dir.exists():
            _manager.load_agents(config_dir)
    if db is not None and _manager._db is None:
        _manager.set_db(db)
    return _manager
