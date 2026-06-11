"""
Chat History Database — DuckDB-backed persistent chat storage.

Database: ``~/.hermes/data/chat_history.duckdb``
Table: ``group_messages``

Usage::

    from memory.chat_history import ChatHistoryDB

    db = ChatHistoryDB()
    db.initialize_db()
    db.insert_message(platform="telegram", chat_id="-100123", ...)
    msgs = db.get_recent("telegram", "-100123", limit=50)
"""

from __future__ import annotations

import json
import logging
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)


class ChatHistoryDB:
    """DuckDB-backed persistent chat message store.

    Creates ``~/.hermes/data/chat_history.duckdb`` on first use.
    All methods are safe to call before ``initialize_db()`` — they
    return empty defaults when the database is unavailable.

    Parameters
    ----------
    db_path : str or Path
        Path to the DuckDB file.
    """

    def __init__(self, db_path: str | Path | None = None):
        self._db_path = Path(db_path or Path.home() / ".hermes" / "data" / "chat_history.duckdb")
        self._conn = None

    # ── Initialization ──────────────────────────────────────

    def initialize_db(self) -> bool:
        """Create the database, table, and indexes.

        Idempotent — safe to call multiple times.  Returns True on
        success, False if DuckDB is not installed.
        """
        try:
            import duckdb
        except ImportError:
            logger.debug("DuckDB not installed — chat history disabled")
            return False

        try:
            self._db_path.parent.mkdir(parents=True, exist_ok=True)
            self._conn = duckdb.connect(str(self._db_path))

            # Sequence for auto-increment IDs
            self._conn.execute(
                "CREATE SEQUENCE IF NOT EXISTS seq_group_messages_id START 1"
            )

            # Main table
            self._conn.execute("""
                CREATE TABLE IF NOT EXISTS group_messages (
                    id                BIGINT PRIMARY KEY
                                       DEFAULT nextval('seq_group_messages_id'),
                    platform          TEXT NOT NULL,
                    chat_id           TEXT NOT NULL,
                    chat_title        TEXT,
                    message_id        TEXT NOT NULL,
                    sender_id         TEXT,
                    sender_name       TEXT,
                    sender_username   TEXT,
                    text              TEXT,
                    has_link          BOOLEAN DEFAULT FALSE,
                    links             TEXT,
                    reply_to_id       TEXT,
                    message_type      TEXT,
                    has_media         BOOLEAN DEFAULT FALSE,
                    timestamp         TIMESTAMP,
                    created_at        TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
            """)

            # ── Indexes ────────────────────────────────────
            self._conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_chat "
                "ON group_messages (chat_id, timestamp)"
            )
            self._conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_timestamp "
                "ON group_messages (timestamp)"
            )
            self._conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_sender "
                "ON group_messages (sender_id, chat_id)"
            )

            logger.debug(
                "Chat history DB ready: %s", self._db_path
            )
            return True

        except Exception as e:
            logger.warning("Failed to initialize chat history DB: %s", e)
            self._conn = None
            return False

    @property
    def ready(self) -> bool:
        """True if the database is initialized and connected."""
        return self._conn is not None

    def get_connection(self):
        """Return the DuckDB connection, auto-initializing if needed.

        Returns None if DuckDB is not installed or initialization fails.
        """
        if not self.ready:
            self.initialize_db()
        return self._conn

    # ── Write ───────────────────────────────────────────────

    def save_message(self, message: dict[str, Any]) -> int | None:
        """Save a single message from a dict.

        The dict may contain any subset of the group_messages columns.
        Missing keys use defaults.  Extra keys are ignored.

        Args:
            message: Dict with keys matching group_messages columns.
                     Common keys: platform, chat_id, message_id, text,
                     sender_name, timestamp, etc.

        Returns:
            Row ID on success, None on failure.
        """
        if not self.ready:
            return None

        try:
            text = message.get("text") or ""
            links = message.get("links")

            # Auto-detect links from text
            has_link = False
            links_json = None
            if links:
                has_link = True
                if isinstance(links, list):
                    links_json = json.dumps(links, ensure_ascii=False)
                else:
                    links_json = str(links)
            elif text:
                found = self._extract_links(text)
                if found:
                    has_link = True
                    links_json = json.dumps(found, ensure_ascii=False)

            ts = message.get("timestamp") or datetime.now(timezone.utc)

            self._conn.execute(
                """INSERT INTO group_messages
                   (platform, chat_id, chat_title, message_id,
                    sender_id, sender_name, sender_username,
                    text, has_link, links, reply_to_id,
                    message_type, has_media, timestamp)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                [
                    message.get("platform", ""),
                    message.get("chat_id", ""),
                    message.get("chat_title"),
                    message.get("message_id", ""),
                    message.get("sender_id"),
                    message.get("sender_name"),
                    message.get("sender_username"),
                    text,
                    has_link,
                    links_json,
                    message.get("reply_to_id"),
                    message.get("message_type"),
                    bool(message.get("has_media", False)),
                    ts,
                ],
            )

            row = self._conn.execute(
                "SELECT currval('seq_group_messages_id')"
            ).fetchone()
            return int(row[0]) if row else 0

        except Exception as e:
            logger.warning("save_message failed: %s", e)
            return None

    def batch_save_messages(
        self, messages: list[dict[str, Any]]
    ) -> int:
        """Save multiple messages efficiently in a single transaction.

        Args:
            messages: List of message dicts (same format as save_message).

        Returns:
            Number of messages successfully saved.
        """
        if not self.ready:
            return 0

        if not messages:
            return 0

        saved = 0
        try:
            # Use a single transaction for performance
            self._conn.execute("BEGIN TRANSACTION")
            for msg in messages:
                row_id = self.save_message(msg)
                if row_id is not None:
                    saved += 1
            self._conn.execute("COMMIT")
            logger.debug(
                "batch_save: %d/%d messages saved", saved, len(messages)
            )
        except Exception as e:
            logger.warning("batch_save_messages failed: %s", e)
            try:
                self._conn.execute("ROLLBACK")
            except Exception:
                pass

        return saved

    @staticmethod
    def _extract_links(text: str) -> list[str]:
        """Extract URLs from text using regex.

        Matches http/https links. Returns list of unique URLs.
        """
        if not text:
            return []
        found = re.findall(r"https?://[^\s<>\"')\]]+", text)
        # Deduplicate while preserving order
        return list(dict.fromkeys(found))

    def insert_message(
        self,
        *,
        platform: str,
        chat_id: str,
        message_id: str,
        text: str = "",
        chat_title: str | None = None,
        sender_id: str | None = None,
        sender_name: str | None = None,
        sender_username: str | None = None,
        reply_to_id: str | None = None,
        message_type: str | None = None,
        has_media: bool = False,
        links: list[str] | None = None,
        timestamp: datetime | None = None,
    ) -> int | None:
        """Store an inbound message. Returns row ID or None."""
        if not self.ready:
            return None

        try:
            # Auto-detect links
            has_link = False
            links_json = None
            if links:
                has_link = True
                links_json = json.dumps(links, ensure_ascii=False)
            elif text:
                found = re.findall(r"https?://\S+", text)
                if found:
                    has_link = True
                    links_json = json.dumps(found, ensure_ascii=False)

            ts = timestamp or datetime.now(timezone.utc)

            self._conn.execute(
                """INSERT INTO group_messages
                   (platform, chat_id, chat_title, message_id,
                    sender_id, sender_name, sender_username,
                    text, has_link, links, reply_to_id,
                    message_type, has_media, timestamp)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                [platform, chat_id, chat_title, message_id,
                 sender_id, sender_name, sender_username,
                 text, has_link, links_json, reply_to_id,
                 message_type, has_media, ts],
            )

            row = self._conn.execute(
                "SELECT currval('seq_group_messages_id')"
            ).fetchone()
            return int(row[0]) if row else 0

        except Exception as e:
            logger.warning("insert_message failed: %s", e)
            return None

    # ── Read ────────────────────────────────────────────────

    def get_recent(
        self,
        platform: str,
        chat_id: str,
        limit: int = 50,
        offset: int = 0,
    ) -> list[dict[str, Any]]:
        """Get recent messages, newest first."""
        if not self.ready:
            return []

        try:
            rows = self._conn.execute(
                """SELECT id, platform, chat_id, chat_title, message_id,
                          sender_id, sender_name, sender_username, text,
                          has_link, links, reply_to_id, message_type,
                          has_media, timestamp, created_at
                FROM group_messages
                WHERE platform = ? AND chat_id = ?
                ORDER BY timestamp DESC
                LIMIT ? OFFSET ?""",
                [platform, chat_id, limit, offset],
            ).fetchall()
            return [self._row_to_dict(r) for r in rows]
        except Exception as e:
            logger.warning("get_recent failed: %s", e)
            return []

    def search(
        self,
        platform: str,
        chat_id: str,
        query: str,
        limit: int = 20,
    ) -> list[dict[str, Any]]:
        """Case-insensitive text search in messages."""
        if not self.ready:
            return []

        try:
            rows = self._conn.execute(
                """SELECT id, platform, chat_id, chat_title, message_id,
                          sender_id, sender_name, sender_username, text,
                          has_link, links, reply_to_id, message_type,
                          has_media, timestamp, created_at
                FROM group_messages
                WHERE platform = ? AND chat_id = ?
                  AND text ILIKE '%' || ? || '%'
                ORDER BY timestamp DESC
                LIMIT ?""",
                [platform, chat_id, query, limit],
            ).fetchall()
            return [self._row_to_dict(r) for r in rows]
        except Exception as e:
            logger.warning("search failed: %s", e)
            return []

    def count(self, platform: str, chat_id: str) -> int:
        """Total messages in a chat."""
        if not self.ready:
            return 0

        try:
            row = self._conn.execute(
                "SELECT COUNT(*) FROM group_messages "
                "WHERE platform = ? AND chat_id = ?",
                [platform, chat_id],
            ).fetchone()
            return int(row[0]) if row else 0
        except Exception:
            return 0

    def get_active_chats(
        self, platform: str | None = None, limit: int = 20,
    ) -> list[dict[str, Any]]:
        """Most active chats with message counts."""
        if not self.ready:
            return []

        try:
            if platform:
                rows = self._conn.execute(
                    """SELECT platform, chat_id,
                              MAX(chat_title) as chat_title,
                              COUNT(*) as message_count,
                              MAX(timestamp) as last_message_at
                    FROM group_messages
                    WHERE platform = ?
                    GROUP BY platform, chat_id
                    ORDER BY message_count DESC
                    LIMIT ?""",
                    [platform, limit],
                ).fetchall()
            else:
                rows = self._conn.execute(
                    """SELECT platform, chat_id,
                              MAX(chat_title) as chat_title,
                              COUNT(*) as message_count,
                              MAX(timestamp) as last_message_at
                    FROM group_messages
                    GROUP BY platform, chat_id
                    ORDER BY message_count DESC
                    LIMIT ?""",
                    [limit],
                ).fetchall()

            return [
                {"platform": r[0], "chat_id": r[1], "chat_title": r[2],
                 "message_count": r[3], "last_message_at": str(r[4]) if r[4] else ""}
                for r in rows
            ]
        except Exception as e:
            logger.warning("get_active_chats failed: %s", e)
            return []

    def get_sender_stats(
        self, platform: str, chat_id: str, limit: int = 20,
    ) -> list[dict[str, Any]]:
        """Messages per sender in a chat."""
        if not self.ready:
            return []

        try:
            rows = self._conn.execute(
                """SELECT sender_id,
                          MAX(sender_name) as sender_name,
                          MAX(sender_username) as sender_username,
                          COUNT(*) as message_count
                FROM group_messages
                WHERE platform = ? AND chat_id = ?
                  AND sender_id IS NOT NULL
                GROUP BY sender_id
                ORDER BY message_count DESC
                LIMIT ?""",
                [platform, chat_id, limit],
            ).fetchall()

            return [
                {"sender_id": r[0], "sender_name": r[1],
                 "sender_username": r[2], "message_count": r[3]}
                for r in rows
            ]
        except Exception as e:
            logger.warning("get_sender_stats failed: %s", e)
            return []

    def delete_old(
        self, platform: str, chat_id: str, older_than_days: int = 90,
    ) -> int:
        """Delete messages older than N days. Returns count deleted."""
        if not self.ready:
            return 0

        try:
            from datetime import timedelta
            cutoff = datetime.now(timezone.utc) - timedelta(days=older_than_days)

            before = self._conn.execute(
                "SELECT COUNT(*) FROM group_messages "
                "WHERE platform = ? AND chat_id = ? AND timestamp < ?",
                [platform, chat_id, cutoff],
            ).fetchone()[0]

            self._conn.execute(
                "DELETE FROM group_messages "
                "WHERE platform = ? AND chat_id = ? AND timestamp < ?",
                [platform, chat_id, cutoff],
            )

            deleted = int(before) if before else 0
            if deleted:
                logger.info(
                    "Deleted %d messages older than %d days from %s:%s",
                    deleted, older_than_days, platform, chat_id,
                )
            return deleted
        except Exception as e:
            logger.warning("delete_old failed: %s", e)
            return 0

    # ── Helpers ─────────────────────────────────────────────

    @staticmethod
    def _row_to_dict(row) -> dict[str, Any]:
        return {
            "id": row[0],
            "platform": row[1],
            "chat_id": row[2],
            "chat_title": row[3],
            "message_id": row[4],
            "sender_id": row[5],
            "sender_name": row[6],
            "sender_username": row[7],
            "text": row[8],
            "has_link": bool(row[9]) if row[9] is not None else False,
            "links": row[10],
            "reply_to_id": row[11],
            "message_type": row[12],
            "has_media": bool(row[13]) if row[13] is not None else False,
            "timestamp": str(row[14]) if row[14] else "",
            "created_at": str(row[15]) if row[15] else "",
        }
