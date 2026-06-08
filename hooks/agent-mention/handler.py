"""
agent-mention hook — перехватывает @mention сообщения и роутит к подагентам.

Синтаксис:
    @coder напиши сортировку        → registry.call("coder", session, message)
    @orchestrate исследуй RAG       → registry.orchestrate(session, message)
    @agents                         → список агентов
    @agents-reload                  → hot-reload конфигов
    обычный текст                   → пропускает дальше (None)
"""
from __future__ import annotations
import asyncio
import re
import logging

logger = logging.getLogger("hermes.hooks.agent-mention")

_MENTION_RE = re.compile(r"^@([\w-]+)(?:\s+(.*))?$", re.DOTALL)


def _get_registry():
    from agent_registry import get_registry
    return get_registry()


async def _call_agent(agent_id: str, session_id: str, message: str) -> str:
    registry = _get_registry()
    if not registry.get(agent_id):
        available = [a["agent_id"] for a in registry.list()]
        return f"Unknown agent: {agent_id}\nAvailable: {', '.join(available)}"
    return await registry.call(agent_id, session_id, message)


async def _handle_mention(mention: str, text: str, session_id: str) -> str | None:
    mention_lower = mention.lower()

    if mention_lower in ("agents", "ag"):
        registry = _get_registry()
        agents = registry.list()
        if not agents:
            return "No sub-agents registered."
        lines = ["*Sub-agents:*"]
        for a in agents:
            calls = a.get("calls", 0)
            avg = a.get("avg_latency_ms", 0)
            desc = a.get("description", "")
            stat = f" ({calls} calls, {avg}ms avg)" if calls else ""
            lines.append(f"`@{a['agent_id']}`{stat} — {desc}")
        lines.append("\nUsage: `@coder <message>` or `@orchestrate <message>`")
        return "\n".join(lines)

    if mention_lower in ("agents-reload", "agr"):
        count = _get_registry().reload()
        return f"Reloaded {count} agents from agent_configs/"

    if mention_lower in ("orchestrate", "orch"):
        if not text:
            return "Usage: `@orchestrate <message>`"
        return await _get_registry().orchestrate(session_id, text)

    registry = _get_registry()
    if registry.get(mention_lower):
        if not text:
            return f"Usage: `@{mention_lower} <message>`"
        return await _call_agent(mention_lower, session_id, text)

    return None


def on_pre_gateway_dispatch(event, gateway=None, session_store=None, **kwargs):
    """Hook handler for pre_gateway_dispatch event."""
    text = (getattr(event, "text", "") or "").strip()
    if not text.startswith("@"):
        return None

    text = re.sub(r"^@\w+_bot\s+", "", text, count=1)
    if not text.startswith("@"):
        return None

    m = _MENTION_RE.match(text)
    if not m:
        return None

    mention = m.group(1)
    message = (m.group(2) or "").strip()
    session_id = getattr(event, "session_id", "gateway-agent")

    try:
        loop = asyncio.get_running_loop()
        import concurrent.futures
        with concurrent.futures.ThreadPoolExecutor() as pool:
            future = pool.submit(
                asyncio.run, _handle_mention(mention, message, session_id)
            )
            reply = future.result(timeout=120)
    except RuntimeError:
        reply = asyncio.run(_handle_mention(mention, message, session_id))
    except Exception as e:
        logger.error(f"agent-mention hook error: {e}", exc_info=True)
        reply = f"Agent error: {e}"

    if reply is None:
        return None

    return {"action": "skip", "reason": "agent-mention"}
