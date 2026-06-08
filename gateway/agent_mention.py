"""gateway/agent_mention.py — @mention routing for Hermes gateway.

Intercepts messages starting with @ and routes them to sub-agents.

Syntax:
    @coder write a sorting function     → direct sub-agent call
    @orchestrate explain RAG            → through orchestrator
    @agents                             → list all agents
    @agents-reload                      → hot-reload configs
    regular text                        → pass through (None)
"""
import re
import logging

logger = logging.getLogger("hermes.gateway.agent_mention")

# Match @word [rest of message]
_MENTION_RE = re.compile(r"^@([\w-]+)(?:\s+(.*))?$", re.DOTALL)


def _get_registry():
    from agent_registry import get_registry
    return get_registry()


async def handle_mention(text: str, session_id: str) -> str | None:
    """Process @mention and return reply string, or None to pass through."""
    # Strip Telegram @bot_username prefix if present
    # "@hermes_bot @coder sort" → "@coder sort"
    text = re.sub(r"^@\w+_bot\s+", "", text, count=1).strip()

    if not text.startswith("@"):
        return None

    m = _MENTION_RE.match(text)
    if not m:
        return None

    mention = m.group(1).lower()
    message = (m.group(2) or "").strip()
    registry = _get_registry()

    # ── @agents / @ag — list agents ──────────────────────────
    if mention in ("agents", "ag"):
        agents = registry.list()
        if not agents:
            return "No sub-agents registered.\nUse /agents-create to add agents."
        lines = ["*Sub-agents:*"]
        for a in agents:
            calls = a.get("calls", 0)
            avg = a.get("avg_latency_ms", 0)
            desc = a.get("description", "")
            stat = f" ({calls} calls, {avg}ms avg)" if calls else ""
            lines.append(f"`@{a['agent_id']}`{stat} — {desc}")
        lines.append("\nUsage: `@coder <message>` or `@orchestrate <message>`")
        return "\n".join(lines)

    # ── @agents-reload / @agr — hot-reload ───────────────────
    if mention in ("agents-reload", "agr"):
        count = registry.reload()
        return f"✅ Reloaded {count} agents from agent_configs/"

    # ── @orchestrate / @orch — through orchestrator ──────────
    if mention in ("orchestrate", "orch"):
        if not message:
            return "Usage: `@orchestrate <message>`"
        return await registry.orchestrate(session_id, message)

    # ── @<agent_id> — direct call ────────────────────────────
    if registry.get(mention):
        if not message:
            return f"Usage: `@{mention} <message>`"
        return await registry.call(mention, session_id, message)

    # Not our @mention — pass through to normal agent
    return None
