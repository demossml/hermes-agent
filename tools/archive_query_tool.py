"""Archive Query Tool — search archived messages."""

import json
import logging
import os as _os
from pathlib import Path
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)


def _get_db_path() -> Path:
    from hermes_constants import get_hermes_home
    return Path(str(get_hermes_home())) / "archive" / "messages.db"


def archive_query_tool(
    keyword: Optional[str] = None,
    chat_id: Optional[str] = None,
    since: Optional[str] = None,
    until: Optional[str] = None,
    msg_type: Optional[str] = None,
    project_id: Optional[str] = None,
    limit: int = 100,
    offset: int = 0,
) -> str:
    """Query archived messages.

    Args:
        keyword: FTS search term (optional)
        chat_id: Filter by chat (optional)
        since: ISO-8601 start (optional, inclusive)
        until: ISO-8601 end (optional, exclusive)
        msg_type: Filter by type — text, photo, voice, document_pdf, etc. (optional)
        project_id: Filter by project (optional)
        limit: Max results (default 100, max 2000)
        offset: Pagination offset (default 0)
    """
    db_path = _get_db_path()
    if not db_path.exists():
        return json.dumps({
            "count": 0,
            "messages": [],
            "hint": "archive.db not found. Enable message_archive.enabled in config.",
        }, ensure_ascii=False)

    import sqlite3

    limit = max(1, min(limit, 2000))
    offset = max(0, offset)

    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row

    try:
        wheres: List[str] = []
        params: List[Any] = []

        if since:
            wheres.append("timestamp >= ?")
            params.append(since)
        if until:
            wheres.append("timestamp < ?")
            params.append(until)
        if chat_id:
            wheres.append("chat_id = ?")
            params.append(str(chat_id))
        if msg_type:
            if "," in str(msg_type):
                types = [t.strip() for t in str(msg_type).split(",")]
                placeholders = ",".join(["?"] * len(types))
                wheres.append(f"msg_type IN ({placeholders})")
                params.extend(types)
            else:
                wheres.append("msg_type = ?")
                params.append(str(msg_type))
        if project_id:
            wheres.append("project_id = ?")
            params.append(str(project_id))

        if keyword and keyword.strip():
            # FTS search
            fts_wheres = list(wheres)
            fts_params = list(params)
            fts_wheres.append("messages_fts MATCH ?")
            fts_params.append(keyword.strip())

            fts_where = " AND ".join(fts_wheres) if fts_wheres else "1=1"
            fts_sql = f"""
                SELECT m.* FROM messages m
                JOIN messages_fts fts ON m.id = fts.rowid
                WHERE {fts_where}
                ORDER BY m.timestamp DESC
                LIMIT ? OFFSET ?
            """
            fts_params.extend([limit, offset])
            rows = conn.execute(fts_sql, fts_params).fetchall()
        else:
            where = " AND ".join(wheres) if wheres else "1=1"
            sql = f"""
                SELECT * FROM messages
                WHERE {where}
                ORDER BY timestamp DESC
                LIMIT ? OFFSET ?
            """
            params.extend([limit, offset])
            rows = conn.execute(sql, params).fetchall()

        messages = []
        for row in rows:
            msg = dict(row)
            # Parse JSON fields
            for field in ("media_urls", "media_types", "metadata"):
                if field in msg and isinstance(msg[field], str):
                    try:
                        msg[field] = json.loads(msg[field])
                    except Exception:
                        pass
            # Truncate large content
            if msg.get("full_message") and len(str(msg["full_message"])) > 500:
                msg["full_message"] = str(msg["full_message"])[:500] + "..."
            messages.append(msg)

        return json.dumps({
            "count": len(messages),
            "limit": limit,
            "offset": offset,
            "messages": messages,
        }, ensure_ascii=False, default=str)

    except Exception as e:
        logger.warning("archive_query failed: %s", e)
        return json.dumps({
            "count": 0,
            "messages": [],
            "error": str(e),
        }, ensure_ascii=False)
    finally:
        conn.close()


def check_archive_query_requirements() -> bool:
    """Available when message_archive plugin is present."""
    try:
        from plugins.message_archive import is_enabled
        return True  # Tool is available, even if archive is disabled
    except ImportError:
        return False


ARCHIVE_QUERY_SCHEMA = {
    "name": "archive_query",
    "description": (
        "Search archived messages by keyword (FTS), chat_id, date range "
        "(ISO-8601 since/until), message type (text, photo, voice, "
        "document_pdf, document_docx, document_xlsx), and project_id. "
        "Returns messages with metadata."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "keyword": {
                "type": "string",
                "description": "FTS search term. Searches content and full_message fields.",
            },
            "chat_id": {
                "type": "string",
                "description": "Filter by chat ID (e.g., -1001234567890 for Telegram).",
            },
            "since": {
                "type": "string",
                "description": "ISO-8601 start timestamp (inclusive), e.g. 2026-08-03T00:00:00Z.",
            },
            "until": {
                "type": "string",
                "description": "ISO-8601 end timestamp (exclusive), e.g. 2026-08-04T00:00:00Z.",
            },
            "msg_type": {
                "type": "string",
                "description": "Message type filter. Single: 'photo'. Multiple: 'document_pdf,document_docx'.",
            },
            "project_id": {
                "type": "string",
                "description": "Filter by project ID (optional, secretary mode defaults to no filter).",
            },
            "limit": {
                "type": "integer",
                "description": "Max results (default 100, max 2000).",
            },
            "offset": {
                "type": "integer",
                "description": "Pagination offset (default 0).",
            },
        },
    },
}


from tools.registry import registry, tool_error

registry.register(
    name="archive_query",
    toolset="archive",
    schema=ARCHIVE_QUERY_SCHEMA,
    handler=lambda args, **kw: archive_query_tool(
        keyword=args.get("keyword"),
        chat_id=args.get("chat_id"),
        since=args.get("since"),
        until=args.get("until"),
        msg_type=args.get("msg_type"),
        project_id=args.get("project_id"),
        limit=args.get("limit", 100),
        offset=args.get("offset", 0),
    ),
    check_fn=check_archive_query_requirements,
    emoji="📦",
)
