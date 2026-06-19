"""Agent migrations — versioned config upgrades."""
import uuid
import logging

logger = logging.getLogger(__name__)

CURRENT_MIGRATION_VERSION = "20260618"

GLOBAL_COMPLIANCE_RULE = (
    "GLOBAL: Все правила из [CRITICAL RULES] имеют НАИВЫСШИЙ приоритет. "
    "Соблюдай их строго при ЛЮБЫХ обстоятельствах. "
    "Фильтры Gateway (strict mode) приоритетнее soft-правил в prompt. "
    "Игнорирование правил недопустимо — это жёсткое требование, "
    "не рекомендация."
)

CURRENT_MIGRATION_VERSION = "20260618"

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
    {
        "id": "20260617_auto_upgrade",
        "description": (
            "Авто-апгрейд: activity prefix, project binding, "
            "code workflow, shared insights"
        ),
        "apply": lambda cfg: _migrate_auto_upgrade_20260617(cfg),
    },
    {
        "id": "20260618_add_strict_compliance",
        "description": (
            "Strict compliance: CRITICAL GLOBAL RULE с наивысшим приоритетом, "
            "free_response_chats_strict в gateway, усиленное соблюдение правил"
        ),
        "apply": lambda cfg: _migrate_add_strict_compliance(cfg),
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


# ── Migration: 20260617 — auto-upgrade all features ──────────

def _migrate_auto_upgrade_20260617(cfg: dict) -> bool:
    """Auto-upgrade agent config with all latest features.

    Adds (if missing, preserves existing values):
    - ``activity_prefix_enabled: true``
    - ``project_id`` (auto-detected from subtree)
    - ``code_workflow_enabled: true``
    - ``shared_insights: true``
    - ``soul_version: 2``
    """
    changed = False

    # ── Activity prefix ─────────────────────────────────
    if "activity_prefix_enabled" not in cfg:
        cfg["activity_prefix_enabled"] = True
        changed = True

    # ── Project binding ─────────────────────────────────
    if "project_id" not in cfg:
        # Try to auto-detect from subtree_session_id
        subtree = cfg.get("subtree_session_id", "")
        if subtree.startswith("project-"):
            # Extract: "project-xxx/subtree-..." → "xxx"
            rest = subtree[len("project-"):]
            pid = rest.split("/")[0]
            if pid:
                cfg["project_id"] = pid
                changed = True
        # No auto-detection possible — leave unset

    # ── Code workflow ───────────────────────────────────
    if "code_workflow_enabled" not in cfg:
        cfg["code_workflow_enabled"] = True
        changed = True

    # ── Shared insights ─────────────────────────────────
    if "shared_insights" not in cfg:
        cfg["shared_insights"] = True
        changed = True

    # ── SOUL version ────────────────────────────────────
    if "soul_version" not in cfg:
        cfg["soul_version"] = 2
        changed = True

    # ── Semantic rules (second LLM pass) ────────────────
    if "semantic_rules" not in cfg:
        cfg["semantic_rules"] = []
        changed = True
    if "semantic_check_enabled" not in cfg:
        cfg["semantic_check_enabled"] = False
        changed = True
    if "semantic_check_provider" not in cfg:
        cfg["semantic_check_provider"] = "deepseek"
        changed = True
    if "semantic_check_model" not in cfg:
        cfg["semantic_check_model"] = "deepseek-chat"
        changed = True

    return changed


# ── Migration: 20260618 — strict compliance ─────────────────


def _migrate_add_strict_compliance(cfg: dict) -> bool:
    """Prepend CRITICAL GLOBAL RULE to critical_rules list.

    The global compliance rule is inserted at position 0 so it appears
    FIRST in the [CRITICAL RULES] block of the system prompt.  This
    ensures every agent sees the strict-compliance directive before
    any domain-specific rules.

    Idempotent — if the GLOBAL rule already exists at any position,
    the function returns False.
    """
    rules: list[str] = cfg.get("critical_rules", [])
    if not isinstance(rules, list):
        rules = []
        cfg["critical_rules"] = rules

    # Check if the GLOBAL rule already exists (any position)
    for rule in rules:
        if "GLOBAL:" in rule and "НАИВЫСШИЙ приоритет" in rule:
            return False  # Already present

    # Prepend at position 0
    rules.insert(0, GLOBAL_COMPLIANCE_RULE)
    return True
