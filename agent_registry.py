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
    response = await registry.call("coder", "session-id", "write sort function")

    # Stream from a sub-agent
    async for chunk in registry.stream("coder", "session-id", "explain"):
        print(chunk, end="")

    # Orchestrate across sub-agents
    final = await registry.orchestrate("session-id", "complex request")

    # List all agents
    for agent in registry.list():
        print(agent["agent_id"], agent["model"], agent["status"])
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
from pathlib import Path
from typing import Any, AsyncIterator, Dict, List, Optional

import yaml

from memory import LongTermMemory, COLLECTION_NAME, IMPORTANCE_HIGH, IMPORTANCE_MEDIUM, HAS_CHROMA

logger = logging.getLogger(__name__)

DEFAULT_CONFIG_DIR = Path(__file__).parent / "agent_configs"

# ── Versioned agent config migrations ──────────────────────────
# Each migration is a dict with:
#   id          — unique identifier (date + short name)
#   description — human-readable one-liner
#   apply       — callable(cfg: dict) → bool
#                 Returns True if the migration made any change,
#                 False if the config was already up-to-date.
#
# Migrations are IDEMPOTENT — running them repeatedly on an
# already-migrated config is a no-op.
#
# When adding a NEW migration, append it to the END of this list
# and bump CURRENT_MIGRATION_VERSION below.
# NEVER reorder or delete existing entries — migration IDs are
# recorded inside agent YAML files and must remain valid forever.

CURRENT_MIGRATION_VERSION = "20260613"

MIGRATIONS: list[dict] = [
    {
        "id": "20260609_add_subtree_session",
        "description": "Добавление subtree_session_id для изоляции памяти",
        "apply": lambda cfg: _migrate_add_subtree_session(cfg),
    },
    {
        "id": "20260610_add_enabled_toolsets",
        "description": "Явное добавление enabled_toolsets = None (полный клон)",
        "apply": lambda cfg: _migrate_add_enabled_toolsets(cfg),
    },
    {
        "id": "20260611_add_critical_rules",
        "description": "Добавление critical_rules и rule_reminder_every",
        "apply": lambda cfg: _migrate_add_critical_rules(cfg),
    },
    {
        "id": "20260612_add_provider_fallback",
        "description": "Добавление fallback_models, priority, auto_select, LLM params",
        "apply": lambda cfg: _migrate_add_llm_params(cfg),
    },
    {
        "id": "20260613_upgrade_agent_toolsets",
        "description": (
            "Апгрейд enabled_toolsets: orchestrator→None, "
            "L1/L2→добавить delegation/messaging/terminal/file/web_search"
        ),
        "apply": lambda cfg: _migrate_upgrade_agent_toolsets(cfg),
    },
]


# ── Individual migration functions (module-level, reusable) ────

def _migrate_add_subtree_session(cfg: dict) -> bool:
    """Ensure subtree_session_id exists."""
    if "subtree_session_id" in cfg:
        return False
    import uuid
    agent_id = cfg.get("agent_id", "unknown")
    cfg["subtree_session_id"] = f"subtree-{agent_id}-{uuid.uuid4().hex[:8]}"
    return True


def _migrate_add_enabled_toolsets(cfg: dict) -> bool:
    """Add enabled_toolsets=None if the key is missing.

    Does NOT touch existing values — if the user explicitly set
    enabled_toolsets to [] or ["terminal"] we preserve that.
    """
    if "enabled_toolsets" in cfg:
        return False
    cfg["enabled_toolsets"] = None  # full clone
    return True


def _migrate_add_critical_rules(cfg: dict) -> bool:
    """Add critical_rules list and rule_reminder_every if missing."""
    changed = False
    if "critical_rules" not in cfg:
        cfg["critical_rules"] = []
        changed = True
    if "rule_reminder_every" not in cfg:
        cfg["rule_reminder_every"] = 0
        changed = True
    return changed


def _migrate_add_llm_params(cfg: dict) -> bool:
    """Add LLM tuning parameters that were added in Phase 6.

    Preserves any existing values the user may have set.
    """
    changed = False
    defaults = {
        "fallback_models": [],
        "priority": 2,
        "auto_select": "none",
        "reasoning_effort": "medium",
        "inherit_from_parent": True,
        "temperature": 0.7,
        "max_tokens": 8192,
        "top_p": 0.95,
    }
    for key, default in defaults.items():
        if key not in cfg:
            cfg[key] = default
            changed = True
    return changed


def _migrate_upgrade_agent_toolsets(cfg: dict) -> bool:
    """Upgrade enabled_toolsets for existing agents.

    IDEMPOTENT — running it repeatedly on an already-migrated config
    is a no-op (no duplicates, no unnecessary changes).

    Rules
    -----
    ┌───────────────────────┬──────────────────────────────────────┐
    │ Agent                 │ Action                               │
    ├───────────────────────┼──────────────────────────────────────┤
    │ orchestrator          │ enabled_toolsets = None (full clone) │
    │ L1/L2, toolsets=None  │ leave as None (already full clone)   │
    │ L1/L2, toolsets=list  │ merge curated toolsets, deduplicate  │
    │ L1/L2, toolsets=[]    │ add curated toolsets (was sandboxed)  │
    └───────────────────────┴──────────────────────────────────────┘

    Curated toolsets for sub-agents (L1+):
    ``["delegation", "messaging", "terminal", "file", "web_search"]``

    These are the minimum tools a sub-agent needs to be productive
    out-of-the-box — delegate tasks, send messages, work with files,
    run shell commands, and search the web.
    """
    # orchestrator always has full tool access
    agent_id = cfg.get("agent_id", "unknown")
    if agent_id == "orchestrator":
        if cfg.get("enabled_toolsets") is None:
            return False  # already full clone, no change needed
        cfg["enabled_toolsets"] = None
        return True

    # ── Sub-agents (level ≥ 1) ─────────────────────────────────
    CURATED = [
        "delegation",
        "messaging",
        "terminal",
        "file",
        "web_search",
    ]

    current = cfg.get("enabled_toolsets")

    # If already None → full clone, nothing to add
    if current is None:
        return False

    # If current is a list → merge, deduplicate
    if isinstance(current, list):
        # Preserve order: existing first, then curated (only new ones)
        merged = list(dict.fromkeys(current))  # deduplicate existing
        added_any = False
        for tool in CURATED:
            if tool not in merged:
                merged.append(tool)
                added_any = True
        if added_any:
            cfg["enabled_toolsets"] = merged
            return True
        return False

    # Defensive: non-list, non-None (malformed YAML) → curated set
    cfg["enabled_toolsets"] = list(CURATED)
    return True

# ── RuleChecker (lightweight, no LLM) ─────────────────────────

class RuleChecker:
    """Lightweight rule violation detector for agent responses.

    Uses keyword and pattern matching — zero LLM calls, instant.
    Designed to be called after every agent response to enforce
    critical rules from YAML configs.

    Usage::

        checker = RuleChecker(rules=[
            "Only respond when addressed as Grisha",
            "Never write code without explicit request",
        ])
        violations = checker.check("Here is a function: def foo(): ...")
        # → ["Never write code without explicit request"]
    """

    def __init__(self, rules: list[str] | None = None):
        self._rules: list[str] = list(rules) if rules else []

    @property
    def has_rules(self) -> bool:
        return len(self._rules) > 0

    def check(self, response: str) -> list[str]:
        """Check response against all rules. Returns list of violated rule texts."""
        if not self._rules or not response:
            return []

        violations: list[str] = []
        reply_lower = response.lower()

        for rule in self._rules:
            r = rule.lower()

            # ── Gating rules: "only respond when called X" ──────
            if ("отвечай только" in r or "respond only" in r or
                "only respond" in r):
                # Extract name from rule text — try multiple strategies
                import re
                names = re.findall(
                    r'"([^"]+)"|«([^»]+)»|called\s+(\w+)|имени\s+(\w+)|as\s+["\u201c]?(\w+)["\u201d]?',
                    rule, re.IGNORECASE,
                )
                keywords = {w.lower() for group in names for w in group if w}
                # Fallback: last word if it looks like a name (capitalised, 3+ chars)
                if not keywords:
                    words = rule.split()
                    for w in reversed(words):
                        w = w.strip('.,;:!?"\u201c\u201d')
                        if w and w[0].isupper() and len(w) >= 3:
                            keywords.add(w.lower())
                            break
                if keywords and not any(k in reply_lower for k in keywords):
                    violations.append(rule)

            # ── Code-without-request rules ───────────────────────
            if ("не пиши код" in r or "don't write code" in r or
                "do not write code" in r or "never write code" in r):
                has_code = bool(
                    "```" in response or
                    "def " in response or
                    "class " in response or
                    "import " in response or
                    "function " in response
                )
                if has_code:
                    violations.append(rule)

            # ── Terminal/execute restrictions ────────────────────
            if ("не используй terminal" in r or "не используй execute" in r or
                "don't use terminal" in r or "never run commands" in r):
                if ("execute_code" in response or "subprocess" in response or
                    "terminal(" in response):
                    violations.append(rule)

            # ── Delegation enforcement ───────────────────────────
            if "delegate" in r and "всегда" in r:
                if ("```" in response or "def " in response):
                    if "delegate" not in reply_lower:
                        violations.append(rule)

        return list(dict.fromkeys(violations))  # deduplicate

    def parse_from_system_prompt(self, system_prompt: str) -> int:
        """Extract rules from a system_prompt containing [CRITICAL RULES] block.

        Returns number of rules extracted.
        """
        if not system_prompt:
            return 0

        import re
        # Match [CRITICAL RULES] ... [/CRITICAL RULES] or just [CRITICAL RULES] ... end
        match = re.search(
            r'\[CRITICAL RULES\](.*?)(?:\[/CRITICAL RULES\]|$)',
            system_prompt, re.DOTALL | re.IGNORECASE,
        )
        if not match:
            return 0

        rules_text = match.group(1).strip()
        extracted = []

        for line in rules_text.split("\n"):
            line = line.strip()
            # Strip list markers: "1.", "2.", "- ", "* ", "•"
            for prefix in ("- ", "* ", "• "):
                if line.startswith(prefix):
                    line = line[len(prefix):].strip()
                    break
            # Strip numbered prefix: "1.", "2. "
            import re as re2
            line = re2.sub(r'^\d+\.\s*', '', line).strip()

            if line and len(line) > 5:  # skip empty/short lines
                extracted.append(line)

        self._rules = extracted
        return len(extracted)


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
        self._longterm_memory: LongTermMemory | None = None  # set via init_longterm_memory()
        self._chat_history = None  # ChatHistoryDB singleton — lazy init

    @property
    def chat_history(self):
        """Lazy-init ChatHistoryDB singleton."""
        if self._chat_history is None:
            from memory.chat_history import ChatHistoryDB
            self._chat_history = ChatHistoryDB()
            self._chat_history.initialize_db()
        return self._chat_history


    def set_db(self, db):
        """Set SessionDB after init (avoids circular imports)."""
        self._db = db

    def init_longterm_memory(
        self, persist_dir: str | Path = "~/.hermes/longterm_memory"
    ) -> bool:
        """Initialise the long-term vector memory backend.

        Call once after creating the registry.  If ChromaDB is not
        installed the method logs a warning and returns ``False``,
        but the registry remains fully functional — long-term memory
        is an optional enhancement.

        Returns ``True`` if the backend was initialised successfully.
        """
        self._longterm_memory = LongTermMemory(persist_dir=persist_dir)
        if self._longterm_memory.enabled:
            logger.info(
                f"LongTermMemory ready: {self._longterm_memory.count()} "
                f"entries in {COLLECTION_NAME}"
            )
            return True
        else:
            logger.warning(
                "LongTermMemory is DISABLED (ChromaDB not available). "
                "Install with: pip install chromadb"
            )
            return False

    # ── Long-Term Memory helpers ────────────────────────────

    def _retrieve_longterm_context(
        self, agent_id: str, message: str, max_items: int = 3,
    ) -> str:
        """Search long-term memory and return relevant context.

        Called before each agent invocation to inject past insights
        into the conversation context.
        """
        if not self._longterm_memory or not self._longterm_memory.enabled:
            return ""

        cfg = self._agents.get(agent_id, {})
        subtree = cfg.get("subtree_session_id", f"subtree-{agent_id}")

        # Search subtree-specific memories
        results = self._longterm_memory.search(
            message,
            subtree_id=subtree,
            top_k=max_items,
            min_importance=IMPORTANCE_MEDIUM,
        )

        if not results:
            return ""

        lines = ["[LONG-TERM MEMORY — relevant past context]"]
        for r in results:
            doc = r["document"]
            meta = r.get("metadata", {})
            task = meta.get("task_type", "general")
            imp = meta.get("importance", 0)
            marker = "🔴" if imp >= IMPORTANCE_HIGH else "🟡"
            lines.append(f"{marker} [{task}] {doc[:400]}")
        lines.append("[END LONG-TERM MEMORY]")

        return "\n".join(lines)

    def get_insights(
        self, agent_id: str, top_k: int = 5,
    ) -> list[dict[str, Any]]:
        """Return the most important recent insights for an agent."""
        if not self._longterm_memory or not self._longterm_memory.enabled:
            return []

        cfg = self._agents.get(agent_id, {})
        subtree = cfg.get("subtree_session_id", f"subtree-{agent_id}")
        return self._longterm_memory.get_insights(
            agent_id, subtree_id=subtree, top_k=top_k,
        )

    def search_longterm_memory(
        self, query: str, agent_id: str | None = None,
        top_k: int = 5,
    ) -> list[dict[str, Any]]:
        """Search long-term memory by natural language query."""
        if not self._longterm_memory or not self._longterm_memory.enabled:
            return []

        subtree = None
        if agent_id:
            cfg = self._agents.get(agent_id, {})
            subtree = cfg.get("subtree_session_id")

        return self._longterm_memory.search(
            query, subtree_id=subtree, agent_id=agent_id, top_k=top_k,
        )

    def summarize_to_longterm(
        self, agent_id: str,
    ) -> dict[str, Any]:
        """Force summarisation of current session to long-term memory.

        Saves the recent subtree conversation as memories.
        Returns a report dict.
        """
        if not self._longterm_memory or not self._longterm_memory.enabled:
            return {"error": "Long-term memory is disabled", "stored": 0}

        cfg = self._agents.get(agent_id, {})
        subtree = cfg.get("subtree_session_id", f"subtree-{agent_id}")

        messages = self.get_subtree_memory(agent_id, limit=100)
        if not messages:
            return {"error": "No messages in subtree", "stored": 0}

        count = self._longterm_memory.summarize_session_to_longterm(
            messages, agent_id=agent_id, subtree_id=subtree,
        )

        # Also store critical rules into long-term memory
        rules = cfg.get("critical_rules", [])
        for rule in rules:
            self._longterm_memory.add_global_rule(rule, agent_id=agent_id)

        return {"agent_id": agent_id, "stored": count, "rules_stored": len(rules)}

    def _auto_summarize_to_longterm(
        self, agent_id: str,
    ) -> None:
        """Trigger automatic summarisation every N messages.

        Called from ``_save_turn()``.  Uses a simple counter tracked
        in ``self._stats[agent_id]`` to decide when to summarise.
        """
        if not self._longterm_memory or not self._longterm_memory.enabled:
            return

        cfg = self._agents.get(agent_id, {})
        # Summarise every 10 exchanges per agent
        threshold = cfg.get("ltm_summarise_every", 10)

        st = self._stats.setdefault(agent_id, {})
        turn_count = st.get("ltm_turn_count", 0) + 1
        st["ltm_turn_count"] = turn_count

        if turn_count % threshold != 0:
            return

        subtree = cfg.get("subtree_session_id", f"subtree-{agent_id}")
        messages = self.get_subtree_memory(agent_id, limit=50)
        if not messages:
            return

        count = self._longterm_memory.summarize_session_to_longterm(
            messages, agent_id=agent_id, subtree_id=subtree,
        )
        if count:
            logger.info(
                f"Agent '{agent_id}': auto-summarised {count} memories "
                f"to long-term storage (turn {turn_count})"
            )

    # ── Config management ────────────────────────────────────

    def load_config(self, config_path: str | Path) -> dict:
        """Load a single agent config from YAML file."""
        with open(config_path) as f:
            cfg = yaml.safe_load(f)
        if not cfg.get("agent_id"):
            cfg["agent_id"] = Path(config_path).stem

        # orchestrator always has full tool access
        agent_id = cfg.get("agent_id", "")
        if agent_id == "orchestrator":
            if not cfg.get("enabled_toolsets"):
                # Missing, None, empty list, empty string → full clone
                cfg["enabled_toolsets"] = None

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
        """Register an agent by id and config dict.

        Fills in defaults for every field that the YAML config or
        runtime caller may have omitted.  After this call the config
        dict is complete and the agent is live in ``self._agents``.

        enabled_toolsets semantics
        -------------------------
        The *only* field consumed by ``_get_agent()`` / ``AIAgent`` for
        tool selection is ``enabled_toolsets`` (NOT the legacy ``tools``
        field, which is kept for backward-compat but never read).

        ┌──────────────────────┬─────────────────────────────────────┐
        │ enabled_toolsets     │ Meaning                             │
        ├──────────────────────┼─────────────────────────────────────┤
        │ None (default)       │ FULL CLONE — AIAgent loads every    │
        │                      │ available tool.                     │
        │ [] (empty list)      │ Sandboxed — zero tools.             │
        │ ["terminal","file"]  │ Restricted to those toolsets only.  │
        └──────────────────────┴─────────────────────────────────────┘
        """
        # ── Identity ──────────────────────────────────────────────
        config.setdefault("agent_id", agent_id)
        config.setdefault("system_prompt", "You are a helpful assistant.")
        config.setdefault("description", "")

        # ── Tools ──────────────────────────────────────────────────
        # tools: legacy field kept for backward compatibility.
        #        Never read by _get_agent() — see enabled_toolsets.
        config.setdefault("tools", [])

        # enabled_toolsets: the canonical tool field.
        # None = full clone (all tools).  [] = sandboxed.
        config.setdefault("enabled_toolsets", None)

        # orchestrator always has full tool access
        if agent_id == "orchestrator":
            config["enabled_toolsets"] = None

        # ── LLM configuration ──────────────────────────────────────
        config.setdefault("provider", "current")
        config.setdefault("model", "claude-sonnet-4-20250514")
        config.setdefault("temperature", 0.7)
        config.setdefault("max_tokens", 8192)
        config.setdefault("top_p", 0.95)
        config.setdefault("fallback_models", [])
        config.setdefault("priority", 2)          # 1=critical, 2=normal, 3=low
        config.setdefault("auto_select", "none")  # cheapest|fastest|balanced|none
        config.setdefault("reasoning_effort", "medium")
        config.setdefault("inherit_from_parent", True)

        # ── Behaviour ──────────────────────────────────────────────
        config.setdefault("critical_rules", [])
        config.setdefault("rule_reminder_every", 0)
        config.setdefault("max_context_tokens", 8000)
        config.setdefault("max_iterations", 3)

        # ── Hierarchy ──────────────────────────────────────────────
        config.setdefault("level", 1)
        config.setdefault("parent_id", "orchestrator")
        config.setdefault("subtree_session_id", f"subtree-{agent_id}")

        # ── Migration tracking ─────────────────────────────────────
        # New agents start with all current migrations pre-applied
        # so they don't get re-migrated on the next reload().
        all_migration_ids = [m["id"] for m in MIGRATIONS]
        config.setdefault("applied_migrations", list(all_migration_ids))
        config.setdefault("migration_version", CURRENT_MIGRATION_VERSION)

        # ── Register ───────────────────────────────────────────────
        self._agents[agent_id] = config

        # Human-readable toolset description for the log line
        ets = config.get("enabled_toolsets")
        if ets is None:
            tools_desc = "ALL"
        elif len(ets) == 0:
            tools_desc = "none"
        else:
            tools_desc = ",".join(ets)

        logger.info(
            f"Registered agent: {agent_id} "
            f"(level={config['level']}, parent={config['parent_id']}, "
            f"provider={config['provider']}, "
            f"tools={tools_desc}, "
            f"max_iter={config['max_iterations']}, "
            f"rules={len(config['critical_rules'])})"
        )

    def unregister(self, agent_id: str):
        """Remove an agent from the registry."""
        self._agents.pop(agent_id, None)
        self._tasks.pop(agent_id, None)
        self._instances.pop(agent_id, None)

    def list(self) -> List[Dict]:
        """List all registered agents with status, stats, level, and hierarchy."""
        result = []
        for agent_id, cfg in self._agents.items():
            entry = dict(cfg)
            entry["status"] = (
                "running"
                if agent_id in self._tasks and not self._tasks[agent_id].done()
                else "stopped"
            )
            entry["sessions_count"] = self._count_sessions(agent_id)
            st = self._stats.get(agent_id, {})
            entry["calls"] = st.get("calls", 0)
            entry["tokens"] = st.get("tokens", 0)
            entry["violations"] = st.get("violations", 0)
            entry["last_violation"] = st.get("last_violation", "")
            if st.get("calls", 0) > 0:
                entry["avg_latency_ms"] = st.get("total_ms", 0) // st["calls"]
            else:
                entry["avg_latency_ms"] = 0
            result.append(entry)
        # Sort by level, then agent_id
        result.sort(key=lambda a: (a.get("level", 1), a["agent_id"]))
        return result

    def get(self, agent_id: str) -> dict | None:
        """Get agent config by id."""
        return self._agents.get(agent_id)

    def get_children(self, parent_id: str) -> List[Dict]:
        """Return all direct children of an agent."""
        return [
            cfg for cfg in self._agents.values()
            if cfg.get("parent_id") == parent_id
        ]

    def get_tree(self, root_id: str = "orchestrator", indent: int = 0) -> str:
        """Return ASCII tree of agent hierarchy with levels, stats, violations."""
        lines = []
        cfg = self._agents.get(root_id, {})
        if not cfg:
            return f"Agent '{root_id}' not found."
        prefix = "  " * indent + ("└─ " if indent > 0 else "")
        level = cfg.get("level", 0)
        calls = self._stats.get(root_id, {}).get("calls", 0)
        violations = self._stats.get(root_id, {}).get("violations", 0)
        subtree = cfg.get("subtree_session_id", "")
        subtree_short = f" [{subtree[:20]}...]" if subtree and len(subtree) > 23 else (f" [{subtree}]" if subtree else "")
        provider = cfg.get("provider", "?")
        model = cfg.get("model", "") or cfg.get("_fallback_model", "")
        model_short = model.split("/")[-1] if "/" in model else model
        provider_info = f" {provider}" + (f"/{model_short}" if model else "") + f" [t={cfg.get('temperature', 0.7)}]"
        lines.append(
            f"{prefix}{root_id} "
            f"[L{level}] "
            f"(calls: {calls}, viol: {violations})"
            f"{provider_info}"
            f"{subtree_short}"
        )
        for child in self.get_children(root_id):
            child_id = child["agent_id"]
            if child_id != root_id:
                lines.append(self.get_tree(child_id, indent + 1))
        return "\n".join(lines)

    def _is_descendant(self, ancestor_id: str, target_id: str) -> bool:
        """Check if target_id is a descendant of ancestor_id."""
        if ancestor_id == target_id:
            return True
        cfg = self._agents.get(target_id)
        if not cfg:
            return False
        parent = cfg.get("parent_id", "")
        while parent and parent in self._agents:
            if parent == ancestor_id:
                return True
            parent = self._agents[parent].get("parent_id", "")
        return False

    def _check_tool_permission(self, caller_id: str, target_id: str) -> None:
        """Check if caller can modify tools of target.

        Rules:
        - orchestrator (level 0) → any agent
        - sub-agent → only self and descendants (not siblings, not parent, not unrelated)
        """
        if caller_id == "orchestrator":
            return
        if not self._is_descendant(caller_id, target_id):
            caller_level = self._agents.get(caller_id, {}).get("level", 1)
            raise PermissionError(
                f"Agent '{caller_id}' (level {caller_level}) can only modify tools "
                f"for itself and its descendants. '{target_id}' is not a descendant."
            )

    def update_tools(
        self, agent_id: str, toolsets: List[str] | None, caller_id: str = "orchestrator"
    ) -> dict:
        """Update agent's enabled toolsets with permission check.

        Writes to enabled_toolsets (the field AIAgent actually reads),
        persists the change to YAML, and forces recreation of the cached
        AIAgent instance so the new toolset takes effect immediately.

        Rules:
        - orchestrator (level 0) → any agent
        - sub-agent → only self and descendants

        Args:
            agent_id:  Target agent to modify.
            toolsets:  List of toolset names to enable (e.g. ['terminal','file']).
                       None means "all tools" (full clone — no restriction).
                       Empty list [] means "no tools" (sandboxed agent).
            caller_id: Who is making the change (permission check).

        Returns:
            The updated agent config dict.

        Raises:
            PermissionError if caller lacks permission.
            KeyError if agent_id not found.
        """
        # ── Permission guard ────────────────────────────────────────
        self._check_tool_permission(caller_id, agent_id)
        if agent_id not in self._agents:
            raise KeyError(f"Agent '{agent_id}' not found.")

        # ── Apply the change ────────────────────────────────────────
        # Write to enabled_toolsets (the field AIAgent._get_agent reads),
        # NOT to cfg["tools"] (legacy field, never consumed by AIAgent).
        self._agents[agent_id]["enabled_toolsets"] = toolsets

        # Force recreation of cached AIAgent so the new toolset takes
        # effect on the very next call()/stream().
        self._instances.pop(agent_id, None)

        # Persist the updated config to agent_configs/{agent_id}.yaml
        # so the change survives restarts and hot-reloads.
        self._persist_agent_config(agent_id)

        tools_desc = (
            "ALL (full clone)" if toolsets is None
            else "none (sandboxed)" if len(toolsets) == 0
            else str(toolsets)
        )
        logger.info(
            f"Tools updated for '{agent_id}' by '{caller_id}': {tools_desc}"
        )

        return self._agents[agent_id]

    def _persist_agent_config(self, agent_id: str) -> bool:
        """Write the in-memory agent config to agent_configs/{agent_id}.yaml.

        Called automatically by every mutator — update_tools(),
        update_provider(), update_config_param(), propagate_to_subtree(),
        and create() — so changes survive restarts and hot-reloads.

        Strategy
        --------
        1. Read the EXISTING YAML file (if any) to preserve manual edits,
           custom key ordering, and comments that PyYAML can't round-trip.
        2. Deep-merge the in-memory config over the file contents so only
           changed keys are touched.
        3. Write back with ``yaml.dump(sort_keys=False)`` to keep the
           key order stable.

        Keys excluded from persistence
        ------------------------------
        Runtime-only keys like ``_fallback_model`` are NEVER written to
        YAML — they live only in ``self._agents`` and are rebuilt on
        next ``_get_agent()``.

        enabled_toolsets: ``None`` is omitted from YAML so that a
        reload correctly interprets "no key" as "full clone".

        Returns
        -------
        ``True`` if the file was written successfully, ``False`` on any
        I/O or YAML error (the in-memory config is unaffected).
        """
        cfg = self._agents.get(agent_id)
        if not cfg:
            logger.debug(
                f"_persist_agent_config: agent '{agent_id}' not in registry"
            )
            return False

        yaml_path = self._config_dir / f"{agent_id}.yaml"

        # ── Ensure the directory exists ─────────────────────────
        try:
            self._config_dir.mkdir(parents=True, exist_ok=True)
        except OSError as e:
            logger.error(
                f"Cannot create config directory {self._config_dir}: {e}"
            )
            return False

        # ── Load existing file (best-effort) ────────────────────
        existing: dict = {}
        try:
            if yaml_path.exists():
                with open(yaml_path, "r") as fh:
                    loaded = yaml.safe_load(fh)
                if isinstance(loaded, dict):
                    existing = loaded
                else:
                    logger.debug(
                        f"Existing {yaml_path} is not a mapping "
                        f"(got {type(loaded).__name__}), overwriting."
                    )
        except yaml.YAMLError as e:
            logger.debug(
                f"YAML parse error reading {yaml_path}: {e}. "
                f"Will overwrite with fresh config."
            )
        except OSError as e:
            logger.debug(
                f"Cannot read {yaml_path}: {e}. "
                f"Will create a new file."
            )

        # ── Build the persistable config ────────────────────────
        # Start from the existing file content (preserves hand-edited
        # keys & ordering), then overlay the in-memory values so that
        # programmatic changes take precedence.
        persist_cfg = dict(existing)

        # Identity & hierarchy
        for key in (
            "agent_id", "parent_id", "level", "subtree_session_id",
            "description", "system_prompt",
        ):
            if key in cfg:
                persist_cfg[key] = cfg[key]

        # LLM configuration
        for key in (
            "provider", "model", "temperature", "max_tokens", "top_p",
            "fallback_models", "priority", "auto_select",
            "reasoning_effort", "inherit_from_parent",
        ):
            if key in cfg:
                persist_cfg[key] = cfg[key]

        # Behaviour
        for key in (
            "critical_rules", "rule_reminder_every",
            "max_context_tokens", "max_iterations",
        ):
            if key in cfg:
                persist_cfg[key] = cfg[key]

        # Tools — None means "all tools" (full clone).
        # Omit the key entirely so that a fresh load_all() interprets
        # its absence as unrestricted access.
        if "enabled_toolsets" in cfg:
            if cfg["enabled_toolsets"] is not None:
                persist_cfg["enabled_toolsets"] = cfg["enabled_toolsets"]
            else:
                # Explicitly remove from persisted dict so reload
                # gets None (via register() default).
                persist_cfg.pop("enabled_toolsets", None)

        # Migration tracking — always persisted so the versioned
        # migration system knows which migrations have been applied.
        for key in ("applied_migrations", "migration_version"):
            if key in cfg:
                persist_cfg[key] = cfg[key]

        # ── Scrub runtime-only keys from persisted output ───────
        # These must NEVER leak to disk — they're regenerated each run.
        for runtime_key in (
            "_fallback_model", "session_id", "status",
            "sessions_count", "calls", "tokens", "violations",
            "last_violation", "avg_latency_ms",
        ):
            persist_cfg.pop(runtime_key, None)

        # ── Write ───────────────────────────────────────────────
        try:
            with open(yaml_path, "w") as fh:
                yaml.dump(
                    persist_cfg, fh,
                    Dumper=yaml.SafeDumper,
                    allow_unicode=True,
                    default_flow_style=False,
                    sort_keys=False,
                )
        except (OSError, yaml.YAMLError) as e:
            logger.error(
                f"Failed to persist agent config for '{agent_id}' "
                f"to {yaml_path}: {e}"
            )
            return False

        logger.debug(f"Persisted agent config: {yaml_path}")
        return True

    # ── LLM Config Management ────────────────────────────────

    def _check_config_permission(self, caller_id: str, target_id: str) -> None:
        """Check if caller can modify LLM config of target.

        - orchestrator (level 0) → any agent
        - sub-agent → self and descendants only
        """
        if caller_id == "orchestrator":
            return
        if not self._is_descendant(caller_id, target_id):
            raise PermissionError(
                f"Agent '{caller_id}' can only modify config for "
                f"itself and its descendants. '{target_id}' is not a descendant."
            )

    def update_provider(self, target_id: str, provider: str, model: str | None = None,
                        caller_id: str = "orchestrator"):
        """Change provider and optionally model for an agent."""
        self._check_config_permission(caller_id, target_id)
        cfg = self._agents[target_id]
        cfg["provider"] = provider
        if model:
            cfg["model"] = model
        self._instances.pop(target_id, None)
        self._persist_agent_config(target_id)
        logger.info(f"Provider for '{target_id}' set to {provider}/{cfg.get('model','?')} by '{caller_id}'")

    def update_config_param(self, target_id: str, param_name: str, value,
                            caller_id: str = "orchestrator"):
        """Update a single LLM parameter (temperature, max_tokens, top_p, etc.)."""
        self._check_config_permission(caller_id, target_id)
        valid_params = {"temperature", "max_tokens", "top_p", "priority",
                        "auto_select", "reasoning_effort", "inherit_from_parent",
                        "fallback_models"}
        if param_name not in valid_params:
            raise ValueError(f"Unknown param: {param_name}. Valid: {sorted(valid_params)}")
        self._agents[target_id][param_name] = value
        self._instances.pop(target_id, None)
        self._persist_agent_config(target_id)
        logger.info(f"Config '{param_name}' for '{target_id}' set to {value} by '{caller_id}'")

    def propagate_to_subtree(self, root_id: str, updates: dict,
                             caller_id: str = "orchestrator"):
        """Apply config updates to an agent and all its descendants."""
        self._check_config_permission(caller_id, root_id)
        count = 0
        for aid, cfg in self._agents.items():
            if aid == root_id or self._is_descendant(root_id, aid):
                cfg.update(updates)
                self._instances.pop(aid, None)
                self._persist_agent_config(aid)
                count += 1
        logger.info(f"Propagated config to {count} agents in subtree of '{root_id}'")
        return count

    def get_effective_config(self, agent_id: str) -> dict:
        """Return effective config with inheritance resolved."""
        cfg = dict(self._agents.get(agent_id, {}))
        if cfg.get("inherit_from_parent", True):
            parent_id = cfg.get("parent_id")
            if parent_id and parent_id in self._agents:
                parent = self.get_effective_config(parent_id)
                for key in ("provider", "model", "temperature", "max_tokens", "top_p",
                            "fallback_models", "priority", "auto_select", "reasoning_effort"):
                    if cfg.get(key) is None or cfg.get(key) == "":
                        cfg[key] = parent.get(key, cfg.get(key))
        return cfg

    def auto_select_model(self, agent_id: str) -> str | None:
        """Auto-select model based on auto_select strategy. Returns model name or None."""
        cfg = self._agents.get(agent_id, {})
        strategy = cfg.get("auto_select", "none")
        if strategy == "none":
            return cfg.get("model")

        fallbacks = cfg.get("fallback_models", [])
        if not fallbacks:
            return cfg.get("model")

        # Price tiers for known models (relative scale, 1=cheapest)
        PRICE_TIERS = {
            "deepseek-v4-pro": 1, "deepseek-chat": 1,
            "claude-3-5-haiku": 2, "claude-3-haiku": 2,
            "claude-3-5-sonnet": 4, "claude-sonnet-4": 4,
            "claude-3-opus": 6, "claude-opus-4": 8,
            "gpt-4o-mini": 2, "gpt-4o": 6,
        }
        SPEED_TIERS = {
            "deepseek-v4-pro": 3, "deepseek-chat": 3,
            "claude-3-5-haiku": 5, "claude-3-haiku": 5,
            "claude-3-5-sonnet": 3, "claude-sonnet-4": 2,
            "claude-3-opus": 1, "claude-opus-4": 1,
        }

        if strategy == "cheapest":
            return min(fallbacks, key=lambda m: PRICE_TIERS.get(m, 99))
        elif strategy == "fastest":
            return max(fallbacks, key=lambda m: SPEED_TIERS.get(m, 0))
        elif strategy == "balanced":
            scored = [(m, PRICE_TIERS.get(m, 99) + (6 - SPEED_TIERS.get(m, 0))) for m in fallbacks]
            return min(scored, key=lambda x: x[1])[0]
        return fallbacks[0]

    # ── Lifecycle ────────────────────────────────────────────

    def reload(self) -> int:
        """Hot-reload all agent configs from agent_configs/ directory.

        Clears cached AIAgent instances (they'll be recreated on next call).
        Preserves runtime-registered agents (created via create()).
        Returns count of loaded configs.
        """
        self._instances.clear()
        count = self.load_all()
        self._migrate_subtree_sessions()

        # Apply any pending config migrations to existing agents.
        # New agents (created via create()) already have all
        # migrations pre-applied via register() defaults.
        self.apply_all_migrations()

        return count

    def _migrate_subtree_sessions(self):
        """Ensure all agents have subtree_session_id.

        - orchestrator → "main-session"
        - direct children of orchestrator (level 1) → new subtree_session each
        - descendants inherit from their level-1 ancestor
        """
        import uuid

        # orchestrator
        if "orchestrator" in self._agents:
            self._agents["orchestrator"].setdefault("subtree_session_id", "main-session")

        # Level 1 agents (direct children of orchestrator) — each gets own branch
        for cfg in self._agents.values():
            if cfg.get("level") == 1 and cfg.get("parent_id") == "orchestrator":
                cfg.setdefault(
                    "subtree_session_id",
                    f"subtree-{cfg['agent_id']}-{uuid.uuid4().hex[:8]}"
                )

        # Level 2+ — inherit from their level-1 ancestor
        for cfg in self._agents.values():
            if cfg.get("level", 1) >= 2 and "subtree_session_id" not in cfg:
                parent = cfg.get("parent_id", "")
                # Walk up to find the level-1 ancestor
                while parent and parent in self._agents:
                    parent_cfg = self._agents[parent]
                    if parent_cfg.get("level") == 1:
                        cfg["subtree_session_id"] = parent_cfg.get(
                            "subtree_session_id",
                            f"subtree-{parent}-{uuid.uuid4().hex[:8]}"
                        )
                        break
                    parent = parent_cfg.get("parent_id", "")
                # Fallback
                if "subtree_session_id" not in cfg:
                    cfg["subtree_session_id"] = f"subtree-{cfg['agent_id']}-{uuid.uuid4().hex[:8]}"

    # ── Versioned migrations ─────────────────────────────────

    def migrate_agent_config(
        self, agent_id: str, dry_run: bool = False
    ) -> dict:
        """Apply pending migrations to a single agent config.

        Reads the agent's ``applied_migrations`` list from its YAML
        config (or from the in-memory ``self._agents`` dict) and
        applies every migration whose ID is not yet in that list.

        Each migration in :data:`MIGRATIONS` is idempotent — running
        it twice on an already-migrated config is a no-op.

        Returns a report dict::

            {
                "agent_id": "coder",
                "migrations_applied": ["20260610_add_enabled_toolsets", ...],
                "already_applied": ["20260609_add_subtree_session", ...],
                "dry_run": False,
                "error": None,
            }
        """
        cfg = self._agents.get(agent_id)
        if not cfg:
            return {
                "agent_id": agent_id,
                "error": f"Agent '{agent_id}' not in registry",
                "migrations_applied": [],
                "already_applied": [],
                "dry_run": dry_run,
            }

        # ── Determine which migrations are already applied ─────
        applied_ids: list[str] = list(cfg.get("applied_migrations", []))
        # First run with no tracking → apply everything
        first_run = "applied_migrations" not in cfg

        pending = [m for m in MIGRATIONS if m["id"] not in applied_ids]
        already = [m["id"] for m in MIGRATIONS if m["id"] in applied_ids]

        if not pending and not first_run:
            logger.debug(
                f"Agent '{agent_id}': all {len(MIGRATIONS)} migrations "
                f"already applied (v{cfg.get('migration_version', '?')})"
            )
            return {
                "agent_id": agent_id,
                "migrations_applied": [],
                "already_applied": already,
                "dry_run": dry_run,
                "error": None,
            }

        # ── Apply pending migrations ───────────────────────────
        applied_now: list[str] = []
        for migration in pending:
            mig_id = migration["id"]
            try:
                made_change = migration["apply"](cfg)
                if made_change or first_run:
                    # Record the migration ID even if apply() returned
                    # False on a first-run — this ensures the ID is
                    # tracked for future runs.
                    applied_ids.append(mig_id)
                    applied_now.append(mig_id)
                    logger.info(
                        f"Agent '{agent_id}': applied migration "
                        f"'{mig_id}' — {migration['description']}"
                        f"{' [DRY-RUN]' if dry_run else ''}"
                    )
                else:
                    logger.debug(
                        f"Agent '{agent_id}': migration '{mig_id}' "
                        f"no-op (already up-to-date)"
                    )
            except Exception as e:
                logger.error(
                    f"Agent '{agent_id}': migration '{mig_id}' "
                    f"FAILED: {e}"
                )
                return {
                    "agent_id": agent_id,
                    "error": f"Migration '{mig_id}' failed: {e}",
                    "migrations_applied": applied_now,
                    "already_applied": already,
                    "dry_run": dry_run,
                }

        # ── Finalise ───────────────────────────────────────────
        cfg["applied_migrations"] = applied_ids
        cfg["migration_version"] = CURRENT_MIGRATION_VERSION

        if not dry_run:
            self._persist_agent_config(agent_id)
            logger.info(
                f"Agent '{agent_id}': migrated to "
                f"v{CURRENT_MIGRATION_VERSION} "
                f"({len(applied_now)} new migrations)"
            )

        return {
            "agent_id": agent_id,
            "migrations_applied": applied_now,
            "already_applied": already,
            "dry_run": dry_run,
            "error": None,
        }

    def apply_all_migrations(self, dry_run: bool = False) -> List[dict]:
        """Apply pending migrations to ALL registered agents.

        Iterates over every agent in the registry, runs
        :meth:`migrate_agent_config` for each, and returns a
        combined report.

        Safe to call multiple times — already-applied migrations
        are skipped.
        """
        reports: list[dict] = []
        for agent_id in sorted(self._agents):
            report = self.migrate_agent_config(agent_id, dry_run=dry_run)
            reports.append(report)
            if report.get("error"):
                logger.warning(
                    f"Migration failed for '{agent_id}': "
                    f"{report['error']}"
                )

        total_new = sum(len(r["migrations_applied"]) for r in reports)
        total_already = sum(len(r["already_applied"]) for r in reports)
        logger.info(
            f"apply_all_migrations: {len(reports)} agents, "
            f"{total_new} new migrations applied, "
            f"{total_already} already up-to-date"
            f"{' [DRY-RUN]' if dry_run else ''}"
        )
        return reports

    def _check_create_permission(self, caller_id: str, parent_id: str) -> None:
        """Check if caller can create an agent under the given parent.

        Rules:
        - caller.level == 0 (orchestrator): allowed under any parent
        - caller.level >= 1: allowed ONLY if parent_id == caller_id

        Raises PermissionError on violation.
        """
        if caller_id == "orchestrator" or caller_id not in self._agents:
            return  # orchestrator can create anywhere

        caller_cfg = self._agents[caller_id]
        caller_level = caller_cfg.get("level", 1)

        if parent_id != caller_id:
            raise PermissionError(
                f"Agent '{caller_id}' (level {caller_level}) can only create "
                f"agents with parent_id='{caller_id}', not '{parent_id}'"
            )

    def create(self, agent_id: str, config: dict, caller_id: str = "orchestrator",
               full_clone: bool = True) -> dict:
        """Create and register a new agent at runtime.

        Permission rules:
        - caller.level == 0 → allowed under any parent_id
        - caller.level ≥ 1 → allowed ONLY if parent_id == caller_id

        Toolset behaviour (enabled_toolsets):

        When ``full_clone=True`` (default) and ``enabled_toolsets`` is
        NOT explicitly provided by the caller, the agent receives a
        curated "full-stack" toolset so it is productive out-of-the-box:
        ``["delegation", "messaging", "terminal", "file", "web_search",
        "skills", "browser"]``.

        ┌─────────────────────────────┬──────────────────────────────────┐
        │ enabled_toolsets in config  │ Result                           │
        ├─────────────────────────────┼──────────────────────────────────┤
        │ Explicit list               │ Used as-is (including [])        │
        │ (e.g. ["terminal","file"]) │                                  │
        │ Explicit None               │ None (ALL tools — unrestricted)  │
        │ Key absent + full_clone=T   │ Curated full-stack default       │
        │ Key absent + full_clone=F   │ None (ALL tools — legacy)        │
        └─────────────────────────────┴──────────────────────────────────┘

        Other auto-computed fields:
        - level = parent_level + 1 (unless explicit)
        - subtree_session_id = new branch ID (under orchestrator)
          or inherit from parent (under sub-agent)
        - LLM config (provider, model, temperature, …) inherited
          from parent unless explicitly overridden

        Saves config to agent_configs/{agent_id}.yaml so it
        survives restarts and hot-reloads.

        Also runs ``migrate_agent_config()`` automatically after
        registration so the new agent is always at the latest
        migration version.
        """
        import uuid

        parent_id = config.get("parent_id", caller_id)

        # ── Permission check ──────────────────────────────────────
        self._check_create_permission(caller_id, parent_id)

        # ── Hierarchy: level ──────────────────────────────────────
        if "level" not in config and parent_id in self._agents:
            parent_level = self._agents[parent_id].get("level", 0)
            config["level"] = parent_level + 1

        # ── Memory isolation: subtree_session_id ───────────────────
        # Each top-level branch (child of orchestrator) gets its own
        # isolated memory namespace.  Descendants of a sub-agent
        # inherit the parent's subtree_session_id so they share
        # memory with their branch.
        if "subtree_session_id" in config:
            subtree_session_id = config["subtree_session_id"]
        elif parent_id != "orchestrator" and parent_id in self._agents:
            # Inherit parent's session — same memory branch
            subtree_session_id = self._agents[parent_id].get(
                "subtree_session_id",
                f"subtree-{parent_id}-{uuid.uuid4().hex[:8]}",
            )
        else:
            # Fresh branch under orchestrator
            subtree_session_id = f"subtree-{agent_id}-{uuid.uuid4().hex[:8]}"

        # ── Identity ──────────────────────────────────────────────
        config["agent_id"] = agent_id
        config["parent_id"] = parent_id
        config["subtree_session_id"] = subtree_session_id

        # ── Tools: full-stack clone by default ─────────────────────
        # When full_clone=True (default) and the caller didn't supply
        # an explicit toolset, the new agent gets a curated "full-stack"
        # toolset so it's immediately productive.
        #
        # If the caller explicitly provided enabled_toolsets (including
        # None or []) we always respect that — no override.
        FULL_CLONE_TOOLSETS = [
            "delegation",    # can delegate to other sub-agents
            "messaging",     # can send messages cross-platform
            "terminal",      # shell access
            "file",          # read/write files
            "web_search",    # web search
            "skills",        # skill management
            "browser",       # browser automation
        ]
        if "enabled_toolsets" in config:
            # Caller explicitly set something — honour it (even None or [])
            pass
        elif full_clone:
            config["enabled_toolsets"] = list(FULL_CLONE_TOOLSETS)
        else:
            config["enabled_toolsets"] = None   # ALL tools (legacy)

        # ── LLM inheritance from parent ────────────────────────────
        # Child agents inherit provider, model, temperature, etc.
        # from their parent unless explicitly overridden in config.
        # Controlled by inherit_from_parent (default True).
        if config.get("inherit_from_parent", True) and parent_id in self._agents:
            parent = self._agents[parent_id]
            for key in (
                "provider", "model", "temperature", "max_tokens",
                "top_p", "fallback_models", "priority", "auto_select",
                "reasoning_effort",
            ):
                if key not in config:
                    config[key] = parent.get(key, config.get(key))

        # ── Register & persist ────────────────────────────────────
        self.register(agent_id, config)

        # ── Auto-migrate to latest version ─────────────────────────
        # Ensures the new agent starts with all migrations applied,
        # regardless of what register() set via defaults.
        migration_report = self.migrate_agent_config(agent_id)

        yaml_path = self._config_dir / f"{agent_id}.yaml"
        try:
            self._persist_agent_config(agent_id)

            # Human-readable toolset description for the log line
            ets = config.get("enabled_toolsets")
            if ets is None:
                tools_desc = "ALL (full clone)"
            elif len(ets) == 0:
                tools_desc = "none (sandboxed)"
            else:
                tools_desc = ", ".join(ets)

            logger.info(
                f"Created agent '{agent_id}' "
                f"(level={config.get('level')}, parent={parent_id}, "
                f"provider={config.get('provider')}/{config.get('model', '?')}, "
                f"tools={tools_desc}, "
                f"full_clone={full_clone}, "
                f"session={subtree_session_id}) → {yaml_path}"
            )
            if migration_report.get("migrations_applied"):
                logger.info(
                    f"Agent '{agent_id}': auto-migrated — "
                    f"{migration_report['migrations_applied']}"
                )
        except Exception as e:
            logger.warning(
                f"Failed to persist agent config for '{agent_id}': {e}"
            )

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
        caller_id: str = "orchestrator",
    ) -> str:
        """Direct call to a sub-agent using Hermes' AIAgent.

        Isolation rules:
        - orchestrator can call any agent (level 0 → any)
        - sub-agent can only call its own children (caller.level < target.level)
        - sub-agent CANNOT call sibling agents (same level = horizontal call)

        Tracks per-agent performance stats (calls, latency).
        Retries once on failure with simplified prompt.
        """
        cfg = self._agents.get(agent_id)
        if not cfg:
            return f"Error: unknown agent '{agent_id}'"

        # ── Isolation check ──────────────────────────────────────────────
        isolation_error = self._check_isolation(caller_id, agent_id, cfg)
        if isolation_error:
            return isolation_error

        t0 = asyncio.get_event_loop().time() * 1000
        last_error = None

        # Build fallback model list
        fallbacks = [cfg.get("model", "")]
        fallbacks += cfg.get("fallback_models", [])
        fallbacks = [m for m in fallbacks if m]  # filter empty

        for attempt in range(retries + 1):
            # ── Smart fallback: try next model on failure ──────────────────
            if attempt > 0 and attempt < len(fallbacks):
                fallback_model = fallbacks[attempt]
                logger.warning(
                    f"Agent '{agent_id}' falling back to model '{fallback_model}' "
                    f"(attempt {attempt+1}/{len(fallbacks)})"
                )
                cfg["_fallback_model"] = fallback_model
                self._instances.pop(agent_id, None)  # recreate with new model

            try:
                agent = self._get_agent(agent_id, cfg, session_id)

                if injected_context:
                    system = cfg.get("system_prompt", "You are a helpful assistant.")
                    system += f"\n\n[Context from orchestrator]:\n{injected_context}"
                    agent.ephemeral_system_prompt = system

                msg = message if attempt == 0 else f"Please respond concisely: {message}"

                # ── Long-term memory retrieval ────────────────────────
                if attempt == 0:
                    ltm_context = self._retrieve_longterm_context(
                        agent_id, message,
                    )
                    if ltm_context:
                        msg = ltm_context + "\n\n" + msg

                # ── Rule reminder ──────────────────────────────────────
                reminder_every = cfg.get("rule_reminder_every", 0)
                if reminder_every > 0:
                    conversation_history = self._load_history(agent_id, session_id)
                    msg_count = len([m for m in conversation_history if m.get("role") == "user"])
                    if msg_count > 0 and msg_count % reminder_every == 0:
                        msg = "[REMINDER: All critical rules still apply. Follow them strictly.]\n\n" + msg
                else:
                    conversation_history = self._load_history(agent_id, session_id)
                # ────────────────────────────────────────────────────────

                result = agent.run_conversation(
                    msg,
                    conversation_history=conversation_history,
                )
                reply = result.get("final_response", "") if isinstance(result, dict) else str(result)

                # ── RuleChecker: self-correction loop ─────────────────
                cfg_for_check = cfg
                rules = cfg_for_check.get("critical_rules", [])
                if rules:
                    corrected = False
                    for correction_attempt in range(2):
                        violations = self._check_violations(agent_id, reply)
                        if not violations:
                            if corrected:
                                reply = f"[✅ Исправлено после {correction_attempt+1} попытки самокоррекции]\n\n{reply}"
                            break
                        corrected = True
                        # Increment violation counter
                        self._stats.setdefault(agent_id, {}).setdefault("violations", 0)
                        self._stats[agent_id]["violations"] += 1
                        self._stats[agent_id]["last_violation"] = violations[0][:60]
                        logger.warning(
                            f"Agent '{agent_id}' violated rules "
                            f"(attempt {correction_attempt+1}/2): {violations}"
                        )
                        # Send correction
                        correction_msg = (
                            f"[VIOLATION] You violated critical rules: {', '.join(violations)}. "
                            f"Re-read [CRITICAL RULES] and answer again."
                        )
                        result = agent.run_conversation(
                            correction_msg,
                            conversation_history=conversation_history,
                        )
                        reply = result.get("final_response", "") if isinstance(result, dict) else str(result)
                    else:
                        # After 2 failed attempts — mark as violation
                        reply = f"[⚠ Нарушение правил: {', '.join(violations[:2])}]\n\n{reply}"
                # ───────────────────────────────────────────────────────

                self._save_turn(agent_id, session_id, msg, reply)
                self._track_call(agent_id, t0, len(reply) // 4)
                return reply

            except Exception as e:
                last_error = e
                if attempt < retries:
                    logger.warning(f"Agent {agent_id} attempt {attempt+1} failed: {e}, retrying...")
                    self._instances.pop(agent_id, None)
                    await asyncio.sleep(0.5)
                else:
                    logger.error(f"Agent {agent_id} call failed after {retries+1} attempts: {e}", exc_info=True)

        return f"Error from agent '{agent_id}': {last_error}"

    def _check_violations(self, agent_id: str, reply: str) -> "list[str]":
        """Check agent reply against its critical_rules. Returns list of violated rules.

        Uses keyword matching for speed (no LLM call).
        """
        cfg = self._agents.get(agent_id, {})
        rules = cfg.get("critical_rules", [])
        if not rules or not reply:
            return []

        # Keyword patterns that indicate rule violations
        # Each rule is checked with simple substring matching
        violations = []
        reply_lower = reply.lower()

        for rule in rules:
            r = rule.lower()
            # Rule: "НЕ пиши код" → check for code blocks
            if ("не пиши код" in r or "не пиши код" in r) and ("```" in reply or "def " in reply):
                violations.append(rule)
            # Rule: "DELEGATE" → check for missing delegate when code present
            if "delegate" in r and ("```" in reply or "def " in reply) and "delegate:" not in reply_lower:
                violations.append(rule)
            # Rule: "НЕ используй terminal" → check for execute/terminal
            if "не используй terminal" in r or "не используй execute" in r:
                if "execute_code" in reply or "subprocess" in reply or "terminal(" in reply:
                    violations.append(rule)

        return list(set(violations))  # deduplicate

    def _check_isolation(self, caller_id: str, agent_id: str, target_cfg: dict) -> str | None:
        if caller_id == "orchestrator" or caller_id not in self._agents:
            return None  # orchestrator can call anyone

        if self._is_descendant(caller_id, agent_id):
            return None  # descendant — allowed

        caller_level = self._agents[caller_id].get("level", 1)
        target_level = target_cfg.get("level", 1)
        target_parent = target_cfg.get("parent_id", "orchestrator")

        error_msg = (
            f"[ISOLATION VIOLATION] Agent '{caller_id}' (level {caller_level}) "
            f"can only call its descendants. "
            f"'{agent_id}' (level {target_level}, parent='{target_parent}') "
            f"is not a descendant."
        )
        logger.error(error_msg)
        return f"Error: {error_msg}"

    def _track_call(self, agent_id: str, start_ms: float, tokens: int):
        """Update per-agent performance stats."""
        elapsed = int(asyncio.get_event_loop().time() * 1000 - start_ms)
        st = self._stats.setdefault(agent_id, {"calls": 0, "tokens": 0, "total_ms": 0})
        st["calls"] += 1
        st["tokens"] += tokens
        st["total_ms"] += elapsed

    def get_subtree_memory(self, agent_id: str, limit: int = 50) -> List[Dict]:
        """Read the shared memory of an agent's entire branch.

        Returns the conversation history from the agent's subtree_session_id.
        Only orchestrator can read any branch's memory.
        Sub-agents can only read their own branch (implicit — they use their own subtree).

        Returns list of {role, content, timestamp} dicts.
        """
        cfg = self._agents.get(agent_id)
        if not cfg:
            return []
        subtree = cfg.get("subtree_session_id", f"subtree-{agent_id}")
        if not self._db:
            return []

        try:
            msgs = self._db.get_messages_as_conversation(subtree)
            if not msgs:
                return []
            result = [
                {"role": m.get("role", "?"), "content": m.get("content", ""),
                 "timestamp": m.get("created_at", m.get("timestamp", ""))}
                for m in msgs[-limit:]
            ]
            return result
        except Exception as e:
            logger.warning(f"Failed to read subtree memory for {agent_id}: {e}")
            return []

    @staticmethod
    def get_agent_session_id(session_id: str, agent_id: str) -> str:
        """Return session_id as-is (subtree_session_id handles isolation)."""
        return session_id

    def _load_history(self, agent_id: str, session_id: str) -> List[Dict]:
        """Load conversation history using subtree_session_id for branch isolation.

        All agents in the same branch (e.g., coder + its children) share one
        subtree_session_id. Different branches are completely isolated.
        """
        history: List[Dict] = []
        if not self._db:
            return history
        try:
            cfg = self._agents.get(agent_id, {})
            subtree = cfg.get("subtree_session_id", f"subtree-{agent_id}")
            msgs = self._db.get_messages_as_conversation(subtree)
            if not msgs:
                return history
            history = [{"role": m["role"], "content": m["content"]} for m in msgs]
            max_tokens = cfg.get("max_context_tokens", 8000)
            total = sum(len((m.get("content") or "")) // 4 for m in history)
            if total > max_tokens:
                loop = asyncio.get_event_loop()
                if loop.is_running():
                    asyncio.create_task(
                        self._summarize_history(agent_id, session_id, history, max_tokens)
                    )
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
            logger.warning(f"Failed to load history for {agent_id}: {e}")
        return history

    def _save_turn(self, agent_id: str, session_id: str, user_msg: str, reply: str):
        """Persist turn to agent's subtree_session_id for branch-level isolation."""
        if not self._db or not reply:
            return
        try:
            cfg = self._agents.get(agent_id, {})
            subtree = cfg.get("subtree_session_id", f"subtree-{agent_id}")
            if not self._db.get_session(subtree):
                self._db.create_session(
                    session_id=subtree,
                    source=f"agent:{agent_id}",
                    model=cfg.get("model", ""),
                )
            self._db.append_message(subtree, "user", user_msg)
            self._db.append_message(subtree, "assistant", reply)

            # Auto-summarise to long-term memory every N turns
            self._auto_summarize_to_longterm(agent_id)
        except Exception as e:
            logger.warning(f"Failed to save turn for {agent_id}: {e}")

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

        Adaptive: Phase 0 classifies task complexity with ultra-cheap prompt.
        Simple tasks → direct answer (1 call, ~300 tokens).
        Complex tasks → full delegation pipeline (3-4 calls).

        Phase 1 asks for delegation plan (DELEGATE lines or NONE).
        Phase 2: if delegation happened, gather results and synthesize.
        """
        if "orchestrator" not in self._agents:
            return "Error: orchestrator agent not registered. Add agent_configs/orchestrator.yaml"

        # ── Phase 0: classify complexity (cheap) ──────────────────────
        is_complex = await self._classify_complexity(message)
        if not is_complex:
            # Simple task — direct answer, 1 API call
            return await self._ask_orchestrator_direct(session_id, message)

        plan = await self._ask_orchestrator(session_id, message)

        # Parse delegation directives
        stages = self._parse_delegates(plan)
        if not stages:
            if "NONE" in plan.upper():
                return await self._ask_orchestrator_direct(session_id, message)
            return plan

        all_results = await self._execute_stages(session_id, stages)

        results_text = "\n\n".join(
            f"[{aid}]: {res}" if not isinstance(res, Exception) else f"[{aid}]: ERROR: {res}"
            for aid, res in all_results.items()
        )
        return await self._ask_orchestrator_direct(
            session_id,
            f"Sub-agents completed their tasks:\n\n{results_text}\n\n"
            "Synthesize a final answer. Combine results, don't mention internal steps.",
        )

    async def _classify_complexity(self, message: str) -> bool:
        """Ultra-cheap classification: ~100 input + 1 output token.

        Asks the model: SIMPLE (one-step, one-domain, no code) or COMPLEX?
        Returns True for COMPLEX, False for SIMPLE.
        """
        # Use orchestrator's model for classification
        cfg = self._agents.get("orchestrator", {})
        provider = self._get_agent("orchestrator", cfg, "classify")

        classify_prompt = (
            "Classify this task: reply ONLY \"SIMPLE\" or \"COMPLEX\".\n"
            "SIMPLE = one domain, no code, answerable in one step.\n"
            "COMPLEX = multiple steps, requires code, research, or 2+ domains.\n\n"
            f"Task: {message[:300]}"
        )

        try:
            result = provider.run_conversation(classify_prompt)
            reply = result.get("final_response", "") if isinstance(result, dict) else str(result)
            is_complex = "COMPLEX" in reply.upper() and "SIMPLE" not in reply.upper()
            logger.debug(
                f"Complexity classifier: '{message[:60]}...' → "
                f"{'COMPLEX' if is_complex else 'SIMPLE'} "
                f"(raw: {reply[:50]})"
            )
            return is_complex
        except Exception as e:
            logger.warning(f"Classifier failed, defaulting to COMPLEX: {e}")
            return True  # Safe default: if classifier fails, delegate

    async def _ask_orchestrator_direct(self, session_id: str, message: str) -> str:
        """Ask orchestrator to answer directly (no delegation format)."""
        orch_cfg = self._agents["orchestrator"]
        orig_system = orch_cfg.get("system_prompt", "")
        orch_cfg["system_prompt"] = orig_system + "\n\nAnswer the user's request directly. Be concise."
        self._instances.pop("orchestrator", None)
        reply = await self.call("orchestrator", session_id, message)
        orch_cfg["system_prompt"] = orig_system
        self._instances.pop("orchestrator", None)
        return reply

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
        """Call orchestrator agent with sub-agent list injected into system prompt.

        Two-phase: first ask for delegation plan, then synthesize after results.
        """
        agents_info = json.dumps(
            [{"id": a["agent_id"], "description": a.get("description", "")}
             for a in self._agents.values() if a["agent_id"] != "orchestrator"],
            ensure_ascii=False, indent=2,
        )

        orch_cfg = self._agents["orchestrator"]
        orig_system = orch_cfg.get("system_prompt", "")

        # Phase 1: delegation plan ONLY — no code, no answers
        orch_cfg["system_prompt"] = orig_system + f"""

Available sub-agents:
{agents_info}

CRITICAL: You must ONLY output delegation directives. DO NOT write code. DO NOT answer questions.
If delegation is needed, output EXACTLY:
DELEGATE: <agent_id> | <task description>
(one per line, multiple lines = parallel)

If NO delegation is needed, output EXACTLY:
NONE

Output NOTHING else. No explanations. No markdown. Just DELEGATE lines or NONE.
"""
        self._instances.pop("orchestrator", None)
        plan = await self.call("orchestrator", session_id, message)
        orch_cfg["system_prompt"] = orig_system
        self._instances.pop("orchestrator", None)
        return plan

    def _parse_delegates(self, reply: str) -> Dict[str, dict]:
        """Parse DELEGATE directives from orchestrator response.

        Format:
            DELEGATE: coder | write sort function | -> reviewer
            DELEGATE: researcher | explain RAG

        Returns: {agent_id: {"task": str, "next_agent": str|None}}
        """
        stages: Dict[str, dict] = {}
        for line in reply.split("\n"):
            line = line.strip()
            if not line.startswith("DELEGATE:"):
                continue
            body = line[9:].strip()
            # Parse three parts: agent_id | task | -> next_agent (optional)
            parts = [p.strip() for p in body.split("|")]
            if len(parts) < 2:
                logger.warning(f"Invalid DELEGATE format: {line}")
                continue
            agent_id = parts[0]
            task = parts[1]
            next_agent = None
            if len(parts) >= 3:
                third = parts[2]
                if third.startswith("->"):
                    next_agent = third[2:].strip()
                    if next_agent not in self._agents:
                        logger.warning(f"Unknown next_agent in DELEGATE: {next_agent}")
                        next_agent = None
            if agent_id not in self._agents or agent_id == "orchestrator":
                logger.warning(f"Unknown agent in DELEGATE: {agent_id}")
                continue
            stages[agent_id] = {"task": task, "next_agent": next_agent}
        return stages

    async def _execute_stages(
        self, session_id: str, stages: Dict[str, dict]
    ) -> Dict[str, str]:
        """Execute delegation stages with DAG support.

        For chains like DELEGATE: coder | task | -> reviewer:
        - coder receives the original task
        - reviewer receives coder's OUTPUT as context in their task

        Error guard: if a stage fails, downstream agents are skipped.
        """
        all_results: dict[str, str] = {}
        # Separate into parallel (no next_agent) and chains (with next_agent)
        chain_tasks = []  # [(agent_id, task, next_agent_id)]

        for agent_id, stage in stages.items():
            task = stage["task"]
            next_agent = stage.get("next_agent")
            if next_agent:
                chain_tasks.append((agent_id, task, next_agent))
            else:
                # Parallel — no downstream dependency
                all_results[agent_id] = task  # placeholder, resolved below

        # Run parallel agents
        parallel = {aid: t for aid, t in all_results.items() if isinstance(t, str)}
        if parallel:
            parallel_results = await asyncio.gather(
                *[self.call(aid, session_id, task) for aid, task in parallel.items()],
                return_exceptions=True,
            )
            for (aid, _), res in zip(parallel.items(), parallel_results):
                all_results[aid] = str(res) if not isinstance(res, Exception) else f"ERROR: {res}"

        # Run chains sequentially — downstream gets upstream OUTPUT
        for aid, task, next_agent in chain_tasks:
            result = await self.call(aid, session_id, task)
            all_results[aid] = result

            # Skip downstream if upstream failed
            if isinstance(result, str) and (result.startswith("Error:") or result.startswith("ERROR:")):
                all_results[f"{aid}→{next_agent}"] = f"[{next_agent} skipped: {aid} failed]"
                continue

            if next_agent and next_agent in self._agents:
                # Pass upstream OUTPUT as context to downstream agent
                downstream_task = (
                    f"{task}\n\n"
                    f"---\n"
                    f"Output from [{aid}]:\n\n"
                    f"{result}"
                )
                downstream_result = await self.call(next_agent, session_id, downstream_task)
                all_results[f"{aid}→{next_agent}"] = downstream_result

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

            # Preserve critical rules in the compressed context
            cfg = self._agents.get(agent_id, {})
            rules = cfg.get("critical_rules", [])
            rules_block = ""
            if rules:
                rules_brief = "; ".join(r[:60] for r in rules[:5])
                rules_block = f"\n[RULES STILL APPLY: {rules_brief}]"

            compact = [{"role": "system", "content": f"[History summary]: {summary}{rules_block}"}]
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

    @staticmethod
    def _build_system_prompt(cfg: dict) -> str:
        """Build system prompt with critical rules block appended.

        Format:
            {system_prompt}

            [CRITICAL RULES]
            1. rule one
            2. rule two

        Critical rules persist across sessions and are non-negotiable.
        """
        prompt = cfg.get("system_prompt", "You are a helpful assistant.")
        rules = cfg.get("critical_rules", [])
        if rules:
            prompt += "\n\n[CRITICAL RULES]\n"
            for i, rule in enumerate(rules, 1):
                prompt += f"{i}. {rule}\n"
            prompt += "\nThese rules persist across sessions. Follow them ALWAYS."
        return prompt

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
                    # Use _fallback_model if set (smart fallback), else config model
                    effective_model = cfg.get("_fallback_model") or cfg.get("model")
                    runtime = resolve_runtime_provider(
                        requested=provider_cfg,
                        target_model=effective_model,
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

            # ── Resolve enabled_toolsets ──────────────────────────
            # orchestrator always has full tool access
            if agent_id == "orchestrator":
                enabled_toolsets = None
            else:
                # cfg["enabled_toolsets"] is the canonical tool field.
                #
                #   None       → pass None to AIAgent → ALL tools (full clone)
                #   []         → pass []   to AIAgent → zero tools (sandboxed)
                #   ["t1","t2"]→ pass list to AIAgent → restricted set
                #
                # IMPORTANT: we CANNOT use `cfg.get(...) or None` because
                # empty list [] is falsy in Python and would be collapsed
                # to None, turning an explicit "no tools" request into
                # "all tools".  Always use an explicit `is None` check.
                raw_toolsets = cfg.get("enabled_toolsets")
                if raw_toolsets is None:
                    enabled_toolsets = None         # full clone
                elif isinstance(raw_toolsets, list):
                    enabled_toolsets = raw_toolsets  # [] or ["terminal",...]
                else:
                    # Defensive: non-list, non-None garbage from a
                    # malformed YAML → treat as full clone with warning.
                    logger.warning(
                        f"Agent '{agent_id}': enabled_toolsets is "
                        f"{type(raw_toolsets).__name__} (expected list or None). "
                        f"Falling back to full clone."
                    )
                    enabled_toolsets = None

            self._instances[agent_id] = AIAgent(
                model=runtime.get("model") or cfg.get("model", ""),
                provider=runtime.get("provider", ""),
                api_key=runtime.get("api_key", ""),
                base_url=runtime.get("base_url", ""),
                api_mode=runtime.get("api_mode", "chat_completions"),
                ephemeral_system_prompt=self._build_system_prompt(cfg),
                session_db=self._db,
                session_id=session_id or cfg.get("session_id", f"agent-{agent_id}"),
                max_iterations=cfg.get("max_iterations", 3),
                enabled_toolsets=enabled_toolsets,
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
            conversation_history = self._load_history(agent_id, session_id)
            result = agent.run_conversation(
                initial_message,
                conversation_history=conversation_history,
            )
            reply = result.get("final_response", "") if isinstance(result, dict) else str(result)
            logger.info(f"[{agent_id}] {str(reply)[:120]}...")
            self._save_turn(agent_id, session_id, initial_message, reply)
        except asyncio.CancelledError:
            logger.info(f"Agent {agent_id} task cancelled")
            raise
        except Exception as e:
            logger.error(f"Agent {agent_id} task error: {e}", exc_info=True)

    def propagate_toolset_to_agents(
        self, toolset_names: List[str],
    ) -> dict:
        """Propagate newly-enabled toolsets to all existing sub-agents.

        When the user runs ``hermes tools enable delegation`` (or any
        other toolset), this function automatically adds those toolsets
        to every L1+ agent that has a restricted (non-None)
        ``enabled_toolsets`` list.

        Agents with ``enabled_toolsets: None`` (full clone — all tools)
        are skipped because they already have everything.

        Idempotent — running it twice won't create duplicates.

        Returns a report::

            {
                "agents_updated": 3,
                "agents_skipped": 1,       # full-clone agents
                "toolsets_propagated": ["delegation"],
                "errors": [],
            }
        """
        report: dict = {
            "agents_updated": 0,
            "agents_skipped": 0,
            "toolsets_propagated": list(toolset_names),
            "errors": [],
        }

        for agent_id, cfg in self._agents.items():
            # orchestrator always has full tool access — skip
            if agent_id == "orchestrator":
                report["agents_skipped"] += 1
                continue

            ets = cfg.get("enabled_toolsets")

            # None = full clone, already has everything
            if ets is None:
                report["agents_skipped"] += 1
                continue

            # [] or ["terminal", ...] → add missing toolsets
            if isinstance(ets, list):
                added_any = False
                for ts in toolset_names:
                    if ts not in ets:
                        ets.append(ts)
                        added_any = True
                if added_any:
                    cfg["enabled_toolsets"] = ets
                    self._instances.pop(agent_id, None)  # clear cache
                    try:
                        self._persist_agent_config(agent_id)
                        report["agents_updated"] += 1
                        logger.info(
                            f"Propagated {toolset_names} to agent "
                            f"'{agent_id}' → {ets}"
                        )
                    except Exception as e:
                        report["errors"].append(f"{agent_id}: {e}")
                        logger.error(
                            f"Failed to persist {agent_id} after "
                            f"toolset propagation: {e}"
                        )
                else:
                    report["agents_skipped"] += 1
            else:
                # Malformed — skip
                report["agents_skipped"] += 1

        logger.info(
            f"propagate_toolset_to_agents: {toolset_names} → "
            f"{report['agents_updated']} updated, "
            f"{report['agents_skipped']} skipped"
        )
        return report

    # ── Health check (doctor) ────────────────────────────────

    def doctor(self) -> dict:
        """Run a health check on the multi-agent system.

        Returns a dict with status of each subsystem::

            {
                "status": "healthy",
                "agents": 5,
                "orchestrator": True,
                "longterm_memory": False,
                "chromadb_installed": False,
                "active_subagents": 0,
                "total_calls": 0,
                "total_violations": 0,
            }
        """
        report: dict = {
            "status": "healthy",
            "agents": len(self._agents),
            "orchestrator": "orchestrator" in self._agents,
            "longterm_memory": (
                self._longterm_memory is not None
                and self._longterm_memory.enabled
            ),
            "chromadb_installed": HAS_CHROMA if hasattr(self, '_longterm_memory') and self._longterm_memory else False,
            "active_subagents": sum(
                1 for aid, t in self._tasks.items() if not t.done()
            ),
            "total_calls": sum(
                s.get("calls", 0) for s in self._stats.values()
            ),
            "total_violations": sum(
                s.get("violations", 0) for s in self._stats.values()
            ),
        }

        # Determine overall status
        issues = []
        if not report["orchestrator"]:
            issues.append("No orchestrator registered")
            report["status"] = "degraded"
        if report["agents"] == 0:
            issues.append("No agents registered")
            report["status"] = "degraded"

        report["issues"] = issues

        # Human-readable summary
        lines = [
            f"Multi-Agent Doctor",
            f"  Status:    {report['status'].upper()}",
            f"  Agents:    {report['agents']} registered",
            f"  Orchestrator: {'✅' if report['orchestrator'] else '❌'}",
            f"  LTM:       {'✅ enabled' if report['longterm_memory'] else '⚠ disabled (pip install chromadb)'}",
            f"  Active:    {report['active_subagents']} running",
            f"  Calls:     {report['total_calls']} total",
            f"  Violations:{report['total_violations']} total",
        ]
        if issues:
            lines.append(f"  Issues:    {', '.join(issues)}")

        report["summary"] = "\n".join(lines)
        return report

    # ── Fast routing (keyword-based, no LLM) ─────────────────

    # Keyword → agent mapping for route_fast()
    ROUTE_KEYWORDS: dict[str, str] = {
        "напиши код": "coder",
        "напиши функцию": "coder",
        "напиши": "coder",
        "write code": "coder",
        "implement": "coder",
        "bug": "coder",
        "fix": "coder",
        "refactor": "coder",
        "найди": "researcher",
        "поищи": "researcher",
        "research": "researcher",
        "find": "researcher",
        "search": "researcher",
        "проверь": "reviewer",
        "review": "reviewer",
        "check": "reviewer",
        "audit": "reviewer",
        "объясни": "summarizer",
        "кратко": "summarizer",
        "summarize": "summarizer",
        "summary": "summarizer",
        "переведи": "summarizer",
        "translate": "summarizer",
    }

    def route_fast(self, message: str) -> str | None:
        """Route a task to the best agent using keyword matching.

        Zero LLM cost.  Returns agent_id on match, None if no match
        (caller should fall back to LLM orchestration).

        Usage::

            agent_id = registry.route_fast("напиши сортировку")
            if agent_id:
                return await registry.call(agent_id, sid, msg)
            # Fall back to full orchestration
            return await registry.orchestrate(sid, msg)
        """
        msg_lower = message.lower()

        for keyword, agent_id in self.ROUTE_KEYWORDS.items():
            if keyword in msg_lower:
                # Verify agent exists
                if agent_id in self._agents:
                    logger.debug(
                        f"route_fast: '{message[:60]}...' → {agent_id} "
                        f"(keyword: '{keyword}')"
                    )
                    return agent_id

        return None

    # ── Code task detection ────────────────────────────────────

    # Patterns that indicate a code-related request
    _CODE_PATTERNS: list[tuple[str, str]] = [
        # (keyword, language hint)
        ("напиши функцию", "python"),
        ("напиши код", "python"),
        ("напиши скрипт", "python"),
        ("напиши класс", "python"),
        ("напиши модуль", "python"),
        ("write a function", "python"),
        ("write code", "python"),
        ("write a script", "python"),
        ("write a class", "python"),
        ("создай функцию", "python"),
        ("создай класс", "python"),
        ("создай скрипт", "python"),
        ("create a function", "python"),
        ("create a class", "python"),
        ("реализуй алгоритм", "python"),
        ("implement algorithm", "python"),
        ("implement a function", "python"),
        ("реализуй функцию", "python"),
        ("исправь код", "python"),
        ("исправь ошибку", "python"),
        ("почини код", "python"),
        ("fix the code", "python"),
        ("fix this code", "python"),
        ("fix the bug", "python"),
        ("debug", "python"),
        ("отрефактори", "python"),
        ("рефакторинг", "python"),
        ("refactor", "python"),
        ("добавь фичу", "python"),
        ("добавь функцию", "python"),
        ("add a feature", "python"),
        ("add feature", "python"),
        ("оптимизируй", "python"),
        ("optimize", "python"),
        ("напиши тест", "python"),
        ("напиши тесты", "python"),
        ("write a test", "python"),
        ("write tests", "python"),
        ("javascript", "javascript"),
        ("typescript", "typescript"),
        ("напиши на js", "javascript"),
        ("write in js", "javascript"),
        ("html", "html"),
        ("css", "css"),
        ("sql", "sql"),
        ("bash", "bash"),
        ("shell script", "bash"),
        ("regex", "regex"),
        ("regular expression", "regex"),
        ("python", "python"),
        ("rust", "rust"),
        ("golang", "go"),
        ("go lang", "go"),
        ("java", "java"),
        ("c++", "cpp"),
        ("c#", "csharp"),
    ]

    _CODE_INDICATORS: list[str] = [
        "```",
        "def ",
        "class ",
        "import ",
        "from ",
        "function ",
        "const ",
        "let ",
        "var ",
        "return ",
        "async ",
        "await ",
        "print(",
    ]

    @classmethod
    def is_code_task(cls, message: str) -> bool:
        """Quick check: is this a code-related request?"""
        result = cls.classify_code_task(message)
        return result["is_code_task"]

    @classmethod
    def classify_code_task(cls, message: str) -> dict:
        """Classify a user message as code-related or not.

        Returns::

            {
                "is_code_task": bool,
                "task_description": str,
                "language": str | None,
                "matched_pattern": str | None,
                "confidence": float (0-1),
            }
        """
        msg_lower = message.lower()

        # Check explicit code patterns
        matched_pattern = None
        detected_lang = None

        for pattern, lang in cls._CODE_PATTERNS:
            if pattern in msg_lower:
                matched_pattern = pattern
                detected_lang = lang
                break

        # Check code indicators in the message (user pasted code)
        has_code_indicators = sum(
            1 for ind in cls._CODE_INDICATORS if ind in message
        )

        is_code = matched_pattern is not None or has_code_indicators >= 3

        # Build task description
        if matched_pattern:
            task_desc = message.strip()
        elif has_code_indicators >= 3:
            task_desc = f"Code fix/improvement: {message[:200]}"
        else:
            task_desc = message.strip()

        # Confidence: explicit pattern = high, indicators only = medium
        if matched_pattern:
            confidence = 0.9
        elif has_code_indicators >= 5:
            confidence = 0.7
        elif has_code_indicators >= 3:
            confidence = 0.5
        else:
            confidence = 0.0

        # Language detection from common indicators
        if not detected_lang:
            if "javascript" in msg_lower or "const " in message or "let " in message:
                detected_lang = "javascript"
            elif "typescript" in msg_lower:
                detected_lang = "typescript"
            elif "def " in message or "import " in message:
                detected_lang = "python"
            elif "function " in message and ":" not in message.split("function ", 1)[1][:20]:
                detected_lang = "javascript"
            elif "<?php" in msg_lower:
                detected_lang = "php"
            elif "<html" in msg_lower or "<div" in msg_lower:
                detected_lang = "html"

        return {
            "is_code_task": is_code,
            "task_description": task_desc,
            "language": detected_lang,
            "matched_pattern": matched_pattern,
            "confidence": confidence,
        }

    async def start_code_workflow(
        self,
        task_description: str,
        language: str | None = None,
        session_id: str = "",
    ) -> str:
        """Create isolated coder + tester agents and run a code task.

        1. Creates ``coder-{id}`` with full task context
        2. Creates ``tester-{id}`` with strict review instructions
        3. Coder writes the code → Tester reviews it
        4. Returns the final result with review feedback

        Both agents have fully isolated memory (separate
        ``subtree_session_id``).  They see only their own branch.

        Args:
            task_description: The user's code request
            language: Optional language hint (python, js, etc.)
            session_id: Session for result tracking

        Returns:
            Combined result from coder + tester.
        """
        import uuid

        task_id = uuid.uuid4().hex[:8]
        coder_id = f"coder-{task_id}"
        tester_id = f"tester-{task_id}"
        lang = language or ""
        lang_hint = f"\nLanguage: {lang}" if lang else ""

        # ── Create coder agent ──────────────────────────────
        if coder_id not in self._agents:
            self.create(coder_id, {
                "system_prompt": (
                    "You are a CODE WRITING agent. Your ONLY job is to write "
                    "clean, correct, well-documented code. Follow the task "
                    "description exactly. Do NOT explain your code unless "
                    "asked. Do NOT test your own code — a separate tester "
                    "agent will do that. Output code only."
                ),
                "parent_id": "orchestrator",
                "description": f"Dynamic coder for task {task_id}",
                "max_iterations": 8,
                "critical_rules": [
                    "Write code ONLY. No explanations.",
                    "Always add docstrings and type hints.",
                    "Handle edge cases and errors.",
                ],
                "rule_reminder_every": 0,
            })
            logger.info(
                f"start_code_workflow: created {coder_id} "
                f"(subtree={self._agents[coder_id].get('subtree_session_id','?')[:20]}...)"
            )

        # ── Create tester agent ─────────────────────────────
        if tester_id not in self._agents:
            self.create(tester_id, {
                "system_prompt": (
                    "You are a STRICT code tester and reviewer. Your job:\n"
                    "1. Read the code carefully\n"
                    "2. Test it mentally — does it solve the problem?\n"
                    "3. Find bugs, edge cases, style issues, performance problems\n"
                    "4. Report: ✅ PASS or ❌ FAIL with specific issues\n"
                    "5. If FAIL: suggest specific fixes\n\n"
                    "Be thorough. Be precise. Be constructive."
                ),
                "parent_id": "orchestrator",
                "description": f"Dynamic tester for task {task_id}",
                "max_iterations": 5,
                "critical_rules": [
                    "Be strict and thorough in your review.",
                    "Report specific issues, not vague complaints.",
                    "Suggest concrete fixes for every issue found.",
                ],
                "rule_reminder_every": 0,
            })
            logger.info(
                f"start_code_workflow: created {tester_id} "
                f"(subtree={self._agents[tester_id].get('subtree_session_id','?')[:20]}...)"
            )

        # ── Phase 1: Coder writes ───────────────────────────
        logger.info(f"start_code_workflow: {coder_id} writing code...")
        coder_msg = f"Code task{lang_hint}:\n\n{task_description}"
        code_result = await self.call(
            coder_id, session_id or task_id, coder_msg,
            caller_id="orchestrator",
        )

        # ── Phase 2: Tester reviews ─────────────────────────
        logger.info(f"start_code_workflow: {tester_id} reviewing code...")
        tester_msg = (
            f"Review this code. Be strict.\n\n"
            f"Original task:\n{task_description}\n\n"
            f"Code to review:\n```\n{code_result[:3000]}\n```"
        )
        review_result = await self.call(
            tester_id, session_id or task_id, tester_msg,
            caller_id="orchestrator",
        )

        # ── Phase 3: Coder fixes if needed ──────────────────
        if "❌" in review_result or "FAIL" in review_result.upper():
            logger.info(
                f"start_code_workflow: {coder_id} fixing issues..."
            )
            fix_msg = (
                f"Your code was reviewed. Fix ALL issues listed below.\n\n"
                f"Review feedback:\n{review_result}\n\n"
                f"Original task:\n{task_description}\n\n"
                f"Rewrite the code with all fixes applied. Output code only."
            )
            fixed_code = await self.call(
                coder_id, session_id or task_id, fix_msg,
                caller_id="orchestrator",
            )
            final = (
                f"## Code (revised after review)\n\n{fixed_code}\n\n"
                f"## Review\n\n{review_result}"
            )
        else:
            final = (
                f"## Code\n\n{code_result}\n\n"
                f"## Review\n\n{review_result}"
            )

        logger.info(
            f"start_code_workflow: task {task_id} complete. "
            f"Agents: {coder_id}, {tester_id}"
        )
        return final


# ── Singleton ────────────────────────────────────────────────

_registry: AgentRegistry | None = None


def get_registry(db=None) -> AgentRegistry:
    """Get or create the global agent registry."""
    global _registry
    if _registry is None:
        _registry = AgentRegistry(db)
        _registry.load_all()
        # Initialise long-term memory (best-effort, non-blocking).
        # When ChromaDB is not installed, LTM is silently disabled
        # and all agents work normally — no crash, no error.
        _registry.init_longterm_memory()
        if not _registry._longterm_memory or not _registry._longterm_memory.enabled:
            logger.debug(
                "Long-Term Memory is disabled (ChromaDB not installed). "
                "Install with: pip install chromadb"
            )
    elif db is not None and _registry._db is None:
        _registry.set_db(db)
    return _registry
