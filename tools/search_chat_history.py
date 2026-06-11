"""
Search Chat History tool — semantic + keyword search across stored messages.

Uses ``ChatHistoryDB`` from ``memory.chat_history``.  Requires DuckDB.
Without sentence-transformers, falls back to keyword-only search.
"""

from __future__ import annotations

import json
import logging

from tools.registry import registry

logger = logging.getLogger(__name__)


def _search_chat_history(
    *,
    chat_id: str = "",
    query: str = "",
    hours: int = 24,
    has_link: bool | None = None,
    sender_id: str = "",
    limit: int = 15,
    use_semantic: bool = True,
    task_id: str = "",
) -> str:
    """Search stored chat messages with optional semantic matching.

    Args:
        chat_id:      Chat identifier (required, e.g. "-100123456")
        query:        Search query. If empty, returns recent messages.
        hours:        Time window in hours (default 24, max 720).
        has_link:     Filter: True = only with links, False = without.
        sender_id:    Filter by sender ID.
        limit:        Max results (1-100, default 15).
        use_semantic: Use hybrid search when sentence-transformers
                      is installed (default True).

    Returns:
        JSON array of matching messages with: sender_name,
        sender_username, text, links, timestamp, chat_title,
        score (if semantic), hybrid_score (if hybrid).
    """
    try:
        from memory.chat_history import ChatHistoryDB
    except ImportError:
        return json.dumps({
            "error": "ChatHistoryDB not available. Install duckdb: pip install duckdb",
        })

    if not chat_id:
        return json.dumps({"error": "chat_id is required"})

    hours = max(1, min(hours, 720))
    limit = max(1, min(limit, 100))

    db = ChatHistoryDB()
    if not db.initialize_db():
        return json.dumps({
            "error": "Chat history DB unavailable (DuckDB not installed?)",
        })

    # ── Build results ──────────────────────────────────────
    results: list[dict] = []

    # Determine platform from chat_id prefix or default
    platform = "telegram"
    if chat_id.startswith("discord:"):
        platform = "discord"
        chat_id = chat_id.split(":", 1)[1]
    elif chat_id.startswith("whatsapp:"):
        platform = "whatsapp"
        chat_id = chat_id.split(":", 1)[1]
    elif chat_id.startswith("telegram:"):
        chat_id = chat_id.split(":", 1)[1]

    try:
        if query and use_semantic:
            # Try hybrid search first
            raw = db.hybrid_search(query, chat_id=chat_id, limit=limit)
            results = _filter_results(raw, hours, has_link, sender_id)
        elif query:
            # Keyword-only search
            raw = db.search(platform, chat_id, query, limit=limit * 3)
            results = _filter_results(raw, hours, has_link, sender_id)
            results = results[:limit]
        else:
            # No query — just recent messages
            raw = db.get_recent(platform, chat_id, limit=limit)
            results = _filter_results(raw, hours, has_link, sender_id)
    except Exception as e:
        logger.warning("search_chat_history query failed: %s", e)

    # ── Format response ────────────────────────────────────
    response = {
        "chat_id": chat_id,
        "platform": platform,
        "query": query or "(recent)",
        "hours": hours,
        "count": len(results),
        "messages": [
            {
                "sender_name": m.get("sender_name", ""),
                "sender_username": m.get("sender_username", ""),
                "text": m.get("text", ""),
                "links": _parse_links(m.get("links")),
                "timestamp": m.get("timestamp", ""),
                "chat_title": m.get("chat_title", ""),
                "score": m.get("score"),
                "hybrid_score": m.get("hybrid_score"),
            }
            for m in results
        ],
    }

    return json.dumps(response, ensure_ascii=False, indent=2, default=str)


def _filter_results(
    results: list[dict],
    hours: int,
    has_link: bool | None,
    sender_id: str,
) -> list[dict]:
    """Apply post-query filters."""
    from datetime import datetime, timedelta, timezone

    cutoff = datetime.now(timezone.utc) - timedelta(hours=hours)

    filtered = []
    for m in results:
        # Time filter
        ts_str = m.get("timestamp", "")
        if ts_str:
            try:
                ts = datetime.fromisoformat(ts_str.replace("Z", "+00:00"))
                if ts < cutoff:
                    continue
            except (ValueError, TypeError):
                pass

        # Link filter
        if has_link is not None:
            if bool(m.get("has_link")) != has_link:
                continue

        # Sender filter
        if sender_id and m.get("sender_id", "") != sender_id:
            continue

        filtered.append(m)

    return filtered


def _parse_links(links_raw) -> list[str]:
    """Parse links from stored JSON or return empty list."""
    if not links_raw:
        return []
    if isinstance(links_raw, list):
        return links_raw
    try:
        return json.loads(links_raw)
    except (json.JSONDecodeError, TypeError):
        return []


# ── Register tool ────────────────────────────────────────────

registry.register(
    name="search_chat_history",
    toolset="memory",
    schema={
        "name": "search_chat_history",
        "description": (
            "Search stored chat message history with semantic or keyword "
            "matching.  Requires DuckDB.  Returns matching messages with "
            "sender, text, links, and relevance scores."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "chat_id": {
                    "type": "string",
                    "description": (
                        "Chat identifier (required). "
                        "e.g. '-100123456' for Telegram, "
                        "'discord:123' for Discord."
                    ),
                },
                "query": {
                    "type": "string",
                    "description": (
                        "Search query. Empty = return recent messages."
                    ),
                },
                "hours": {
                    "type": "integer",
                    "description": "Time window in hours (default 24, max 720).",
                    "default": 24,
                },
                "has_link": {
                    "type": "boolean",
                    "description": "Filter: True = only messages with links.",
                },
                "sender_id": {
                    "type": "string",
                    "description": "Filter by sender ID.",
                },
                "limit": {
                    "type": "integer",
                    "description": "Max results (1-100, default 15).",
                    "default": 15,
                },
                "use_semantic": {
                    "type": "boolean",
                    "description": (
                        "Use hybrid semantic search when available "
                        "(default True)."
                    ),
                    "default": True,
                },
            },
            "required": ["chat_id"],
        },
    },
    handler=lambda args, **kw: _search_chat_history(
        chat_id=args.get("chat_id", ""),
        query=args.get("query", ""),
        hours=args.get("hours", 24),
        has_link=args.get("has_link"),
        sender_id=args.get("sender_id", ""),
        limit=args.get("limit", 15),
        use_semantic=args.get("use_semantic", True),
        task_id=kw.get("task_id", ""),
    ),
    requires_env=[],
)


# ── Tool 2: get_recent_chat_context ───────────────────────────


def _get_recent_chat_context(
    *,
    chat_id: str = "",
    limit: int = 20,
    task_id: str = "",
) -> str:
    """Return recent messages from a chat as context for the agent.

    Args:
        chat_id: Chat identifier (required).
        limit:   Max messages (1-50, default 20).

    Returns:
        JSON array of recent messages formatted as context.
    """
    if not chat_id:
        return json.dumps({"error": "chat_id is required"})

    limit = max(1, min(limit, 50))

    try:
        from memory.chat_history import ChatHistoryDB

        db = ChatHistoryDB()
        db.initialize_db()

        platform = "telegram"
        clean_id = chat_id
        if ":" in chat_id:
            platform, clean_id = chat_id.split(":", 1)

        messages = db.get_recent(platform, clean_id, limit=limit)

        if not messages:
            return json.dumps({
                "chat_id": chat_id,
                "count": 0,
                "context": "No recent messages found.",
            })

        # Build a readable context block
        lines = [f"[RECENT CHAT CONTEXT — last {len(messages)} messages]"]
        for m in reversed(messages):  # chronological order
            sender = m.get("sender_name") or m.get("sender_username") or "unknown"
            text = (m.get("text") or "")[:300]
            lines.append(f"{sender}: {text}")

        context = "\n".join(lines)

        return json.dumps({
            "chat_id": chat_id,
            "count": len(messages),
            "context": context,
        }, ensure_ascii=False, indent=2)

    except Exception as e:
        return json.dumps({"error": str(e)})


registry.register(
    name="get_recent_chat_context",
    toolset="memory",
    schema={
        "name": "get_recent_chat_context",
        "description": (
            "Get recent messages from a chat as context. "
            "Returns the last N messages in chronological order, "
            "formatted for inclusion in the agent's context."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "chat_id": {
                    "type": "string",
                    "description": "Chat identifier (required).",
                },
                "limit": {
                    "type": "integer",
                    "description": "Max messages (1-50, default 20).",
                    "default": 20,
                },
            },
            "required": ["chat_id"],
        },
    },
    handler=lambda args, **kw: _get_recent_chat_context(
        chat_id=args.get("chat_id", ""),
        limit=args.get("limit", 20),
        task_id=kw.get("task_id", ""),
    ),
    requires_env=[],
)
