"""
Chat History Integration — non-blocking message saving for gateways.

Call ``save_message_async`` from any gateway message handler to
persist chat messages to DuckDB without blocking the event loop.
"""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timezone
from typing import Any

logger = logging.getLogger(__name__)


async def save_message_async(
    *,
    platform: str,
    chat_id: str,
    message_id: str,
    text: str = "",
    chat_title: str = "",
    sender_id: str = "",
    sender_name: str = "",
    sender_username: str = "",
    reply_to_id: str = "",
    message_type: str = "text",
    has_media: bool = False,
    links: list[str] | None = None,
    timestamp: datetime | None = None,
) -> None:
    """Persist a chat message to DuckDB, non-blocking.

    Runs in a thread executor so it never blocks the gateway's
    event loop.  Failures are logged and swallowed — message
    processing continues regardless.
    """
    try:
        await asyncio.to_thread(
            _save_sync,
            platform=platform,
            chat_id=chat_id,
            message_id=message_id,
            text=text,
            chat_title=chat_title,
            sender_id=sender_id,
            sender_name=sender_name,
            sender_username=sender_username,
            reply_to_id=reply_to_id,
            message_type=message_type,
            has_media=has_media,
            links=links,
            timestamp=timestamp,
        )
    except Exception as e:
        logger.debug("chat_history save skipped: %s", e)


def _save_sync(**kwargs: Any) -> None:
    """Synchronous save — runs in thread executor."""
    try:
        from memory.chat_history import ChatHistoryDB

        db = ChatHistoryDB()
        if not db.ready:
            db.initialize_db()
        if not db.ready:
            return

        db.save_message(kwargs)
    except ImportError:
        pass  # DuckDB not installed
    except Exception as e:
        logger.debug("chat_history save failed: %s", e)


async def cleanup_old_messages(days: int = 30) -> dict[str, Any]:
    """Delete messages older than N days from all chats.

    Designed to be called from a cron job or scheduled task.
    Returns a summary dict with counts per chat.

    Args:
        days: Delete messages older than this many days.

    Returns:
        {"deleted_total": 123, "chats_cleaned": 5, "errors": []}
    """
    report: dict[str, Any] = {
        "deleted_total": 0,
        "chats_cleaned": 0,
        "errors": [],
    }

    try:
        from memory.chat_history import ChatHistoryDB

        db = ChatHistoryDB()
        if not db.ready:
            db.initialize_db()
        if not db.ready:
            return report

        # Get all active chats
        chats = await asyncio.to_thread(db.get_active_chats, limit=1000)

        for chat in chats:
            platform = chat.get("platform", "")
            chat_id = chat.get("chat_id", "")
            if not platform or not chat_id:
                continue

            try:
                deleted = await asyncio.to_thread(
                    db.delete_old, platform, str(chat_id), days,
                )
                if deleted:
                    report["deleted_total"] += deleted
                    report["chats_cleaned"] += 1
            except Exception as e:
                report["errors"].append(f"{platform}:{chat_id}: {e}")

        if report["deleted_total"]:
            logger.info(
                "chat_history cleanup: deleted %d messages from %d chats",
                report["deleted_total"], report["chats_cleaned"],
            )
    except ImportError:
        pass
    except Exception as e:
        logger.warning("chat_history cleanup failed: %s", e)
        report["errors"].append(str(e))

    return report
