"""
Centralised activity prefix formatter for Hermes Multi-Agent.

Every agent message — CLI, gateway, system prompt — uses this single
function to produce a compact, informative, visually clean prefix.

Formats (full mode):
    [Agent: coder]                              — agent only
    [Agent: coder] → write tests                — agent + action
    [Project: WorkApp] • [Agent: coder]         — project + agent
    [Project: WorkApp] • [Agent: coder] → fix   — all three

Formats (compact mode, ``HERMES_ACTIVITY_PREFIX_COMPACT=1``):
    [coder]                                     — agent only
    [coder] → write tests                       — agent + action
    [WorkApp] · [coder]                         — project + agent
    [WorkApp] · [coder] → fix                   — all three

Emoji mode (``HERMES_ACTIVITY_PREFIX_EMOJI=1``):
    📁 [Project: WorkApp] • 🔧 [Agent: coder] → fix

Global overrides:
    ``HERMES_NO_ACTIVITY_PREFIX=1`` — disable entirely
    ``HERMES_ACTIVITY_PREFIX_COMPACT=1`` — compact mode (no labels)
    ``HERMES_ACTIVITY_PREFIX_EMOJI=1`` — emoji mode

Code-block safety:
    Gateway delivery ALWAYS appends ``\\n\\n`` after the prefix to
    prevent markdown interference with code fences, tables, and
    headers.  Tested on Telegram, Discord, Slack, WhatsApp.
"""

from __future__ import annotations

import os
import re
from typing import Optional

# ── Config from env ──────────────────────────────────────────

def _compact_mode() -> bool:
    return os.environ.get("HERMES_ACTIVITY_PREFIX_COMPACT", "").strip() in (
        "1", "true", "yes",
    )


def _emoji_mode() -> bool:
    return os.environ.get("HERMES_ACTIVITY_PREFIX_EMOJI", "").strip() in (
        "1", "true", "yes",
    )


# ── Agent emoji map (used when EMOJI mode is on) ─────────────

_AGENT_EMOJI: dict[str, str] = {
    "orchestrator": "⚕",
    "coder": "🔧",
    "researcher": "🔍",
    "reviewer": "👁",
    "tester": "🧪",
    "translator": "🌐",
    "summarizer": "📝",
}

_PROJECT_EMOJI = "📁"
_DEFAULT_AGENT_EMOJI = "🤖"


def _agent_label(agent_id: str, compact: bool) -> str:
    """Build the agent segment of the prefix."""
    if agent_id == "orchestrator":
        return "⚕ Orch" if compact else "⚕ Orchestrator"

    if compact:
        return agent_id

    return f"Agent: {agent_id}"


def _maybe_emoji(agent_id: str) -> str:
    """Return emoji prefix if emoji mode is on."""
    if not _emoji_mode():
        return ""
    emoji = _AGENT_EMOJI.get(agent_id, _DEFAULT_AGENT_EMOJI)
    return f"{emoji} "


def _project_label(name: str, compact: bool) -> str:
    """Build the project segment of the prefix."""
    # Truncate long project names
    if len(name) > 24:
        name = name[:21] + "…"

    if compact:
        return name
    return f"Project: {name}"


# ── Main API ─────────────────────────────────────────────────

def get_activity_prefix(
    agent_id: str,
    action: Optional[str] = None,
    project_name: Optional[str] = None,
) -> str:
    """Return a compact, informative activity prefix for agent messages.

    Centralised — every agent message prefix in Hermes Multi-Agent
    should go through this function.

    Args:
        agent_id: Sub-agent ID (e.g. ``"coder"``, ``"researcher"``).
                  Use ``"orchestrator"`` for the main orchestrator.
        action:   Optional action description (e.g. ``"write tests"``).
        project_name: Optional project name override.  When ``None``,
                      auto-detected via ``get_current_project_name()``.

    Returns:
        Compact prefix string, or ``""`` when globally disabled.

    Env control:
        ``HERMES_NO_ACTIVITY_PREFIX=1`` → ``""``
        ``HERMES_ACTIVITY_PREFIX_COMPACT=1`` → no labels (``[coder]``)
        ``HERMES_ACTIVITY_PREFIX_EMOJI=1`` → emoji (``📁 [Project: X]``)
    """
    # ── Global override ──────────────────────────────────────
    if os.environ.get("HERMES_NO_ACTIVITY_PREFIX", "").strip() in (
        "1", "true", "yes",
    ):
        return ""

    compact = _compact_mode()

    # ── Resolve project name ─────────────────────────────────
    if project_name is None:
        try:
            from projects.project_context import get_current_project_name
            project_name = get_current_project_name() or ""
        except Exception:
            project_name = ""

    # ── Assemble parts ───────────────────────────────────────
    parts: list[str] = []
    sep = " · " if compact else " • "

    if project_name:
        emoji = _PROJECT_EMOJI + " " if _emoji_mode() else ""
        parts.append(f"{emoji}[{_project_label(project_name, compact)}]")

    agent_emoji = _maybe_emoji(agent_id)
    parts.append(f"{agent_emoji}[{_agent_label(agent_id, compact)}]")

    prefix = sep.join(parts)

    if action:
        prefix = f"{prefix} → {action}"

    return prefix


def get_activity_prefix_rich(
    agent_id: str,
    action: Optional[str] = None,
    project_name: Optional[str] = None,
) -> str:
    """Return activity prefix with Rich markup for CLI display.

    Same as ``get_activity_prefix()`` but wraps project in ``[bold green]``
    and agent in ``[bold blue]`` Rich markup tags.

    Use in CLI contexts where Rich/Prompt Toolkit rendering is active.
    For gateway/system-prompt contexts, use ``get_activity_prefix()``.
    """
    plain = get_activity_prefix(agent_id, action=action, project_name=project_name)
    if not plain:
        return ""

    # Strategy: build the rich version from scratch by re-assembling
    # the parts that get_activity_prefix already computed.  This avoids
    # fragile regex that double-matches on compact agent names.
    compact = _compact_mode()

    # Split on the separator
    sep = " · " if compact else " • "
    has_arrow = " → " in plain
    if has_arrow:
        body, action_text = plain.rsplit(" → ", 1)
    else:
        body = plain
        action_text = ""

    parts = body.split(sep)

    # First part is project (if 2+ parts), rest is agent
    if len(parts) >= 2:
        # Has project segment — colour green
        proj_segment = re.sub(
            r"\[([^\]]+)\]",
            r"[bold green][\1][/bold green]",
            parts[0],
        )
        # Agent segment(s) — colour blue
        agent_segment = sep.join(
            re.sub(r"\[([^\]]+)\]", r"[bold blue][\1][/bold blue]", p)
            for p in parts[1:]
        )
        body = f"{proj_segment}{sep}{agent_segment}"
    else:
        # Just agent — colour blue
        body = re.sub(
            r"\[([^\]]+)\]",
            r"[bold blue][\1][/bold blue]",
            body,
        )

    if action_text:
        return f"{body} [dim]→ {action_text}[/dim]"
    return body
