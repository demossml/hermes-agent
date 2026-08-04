"""
Tool allowlists and prompt-guidance dispatch per mode.

MODE_TOOLSETS         — which toolsets are available in each mode
filter_tools()        — filter an available_tools list by mode (toolset level)
filter_individual_tools() — remove blocked individual tools from agent.valid_tool_names
get_guidance()        — return the prompt block for a mode

Config gates (modes.<mode>.<key> in config.yaml):
  modes.dev.allow_archive_query    — default False (exclude archive_query in dev)
  modes.secretary.allow_terminal   — default False (exclude terminal in secretary)
"""

from __future__ import annotations

from typing import Optional

from modes.prompts import DEV_GUIDANCE, SECRETARY_GUIDANCE

# ── Toolset allowlists ────────────────────────────────────────

MODE_TOOLSETS: dict[str, list[str]] = {
    "dev": [
        "terminal",
        "file",
        "delegation",
        "todo",
        "memory",
        "session_search",
        "skills",
        "web",
        "search",
        "browser",
        "vision",
        "code_execution",
        "clarify",
        "cronjob",
        "messaging",
        "github",
        "coding",
        "debugging",
    ],
    "secretary": [
        "todo",
        "memory",
        "session_search",
        "skills",
        "web",
        "search",
        "clarify",
        "cronjob",
        "messaging",
    ],
}

# ── Individual blocked tools per mode ─────────────────────────
#
# Applied AFTER toolset filtering via filter_individual_tools().
# If a toolset passes the allowlist but contains a tool that the
# mode must never expose, list it here.  The agent build step
# removes it from valid_tool_names and the model schema.

_BLOCKED_DEV_DEFAULT: frozenset[str] = frozenset({
    "archive_query",
})

_BLOCKED_SECRETARY_DEFAULT: frozenset[str] = frozenset({
    "terminal",
})


# ── Config gates ──────────────────────────────────────────────


def _is_archive_query_allowed_in_dev() -> bool:
    """modes.dev.allow_archive_query (default: False)."""
    try:
        from hermes_cli.config import cfg_get, load_config
        cfg = load_config()
        return bool(cfg_get(cfg, "modes", "dev", "allow_archive_query", default=False))
    except Exception:
        return False


def _is_terminal_allowed_in_secretary() -> bool:
    """modes.secretary.allow_terminal (default: False)."""
    try:
        from hermes_cli.config import cfg_get, load_config
        cfg = load_config()
        return bool(cfg_get(cfg, "modes", "secretary", "allow_terminal", default=False))
    except Exception:
        return False


def _get_blocked_tools(mode: str) -> frozenset:
    """Return the set of individual tool names blocked for this mode."""
    mode_name = str(mode or "").strip().lower()
    if mode_name == "dev":
        blocked = set(_BLOCKED_DEV_DEFAULT)
        if _is_archive_query_allowed_in_dev():
            blocked.discard("archive_query")
        return frozenset(blocked)
    if mode_name == "secretary":
        blocked = set(_BLOCKED_SECRETARY_DEFAULT)
        if _is_terminal_allowed_in_secretary():
            blocked.discard("terminal")
        return frozenset(blocked)
    return frozenset()


_ALWAYS_TOOLSETS = frozenset({
    "skills",
    "memory",
    "session_search",
    "clarify",
    "todo",
})


def filter_tools(mode: Optional[str], available_toolsets: frozenset) -> frozenset:
    """Return the intersection of mode-allowlisted toolsets with available_toolsets."""
    if not mode:
        return available_toolsets
    mode_name = str(mode).strip().lower()
    allowed = MODE_TOOLSETS.get(mode_name)
    if allowed is None:
        return available_toolsets
    allowed_set = frozenset(allowed) | _ALWAYS_TOOLSETS
    return available_toolsets & allowed_set


def filter_individual_tools(
    mode: Optional[str],
    valid_tool_names: frozenset,
) -> frozenset:
    """Remove blocked individual tools from valid_tool_names."""
    if not mode:
        return valid_tool_names
    blocked = _get_blocked_tools(mode)
    if not blocked:
        return valid_tool_names
    return valid_tool_names - blocked


def get_guidance(mode: Optional[str]) -> str:
    """Return the system-prompt guidance block for a mode."""
    if not mode:
        return ""
    mode_name = str(mode).strip().lower()
    if mode_name == "dev":
        return DEV_GUIDANCE
    elif mode_name == "secretary":
        return SECRETARY_GUIDANCE
    return ""
