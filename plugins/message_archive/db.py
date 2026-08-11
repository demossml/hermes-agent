"""
MessageArchiveDB — SQLite WAL, writer thread + queue, FTS5.

Schema: messages with platform, chat_id, thread_id, user_id, message_id,
ts_utc, msg_type, raw_text, extracted_text, extractor, file_path,
original_name, mime_type, metadata_json, project_id.

Usage:
    db = get_db()           # singleton
    db.enqueue(record)      # async write via queue
    rows = db.query(...)    # read
    db.close()              # shutdown
"""

from __future__ import annotations

import json
import logging
import queue
import sqlite3
import threading
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

# ── Schema ────────────────────────────────────────────────────

SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS messages (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    platform    TEXT    DEFAULT '',
    chat_id     TEXT    DEFAULT '',
    thread_id   TEXT    DEFAULT '',
    user_id     TEXT    DEFAULT '',
    username    TEXT    DEFAULT '',
    message_id  TEXT    DEFAULT '',
    ts_utc      TEXT    NOT NULL,
    msg_type    TEXT    DEFAULT 'text',
    raw_text    TEXT    DEFAULT '',
    extracted_text TEXT DEFAULT '',
    extractor   TEXT    DEFAULT '',
    file_path   TEXT    DEFAULT '',
    original_name TEXT  DEFAULT '',
    mime_type   TEXT    DEFAULT '',
    metadata_json TEXT  DEFAULT '{}',
    doc_category TEXT   DEFAULT '',
    telegram_file_id TEXT DEFAULT '',
    project_id  TEXT    DEFAULT ''
);

CREATE UNIQUE INDEX IF NOT EXISTS idx_msg_unique
    ON messages(platform, chat_id, message_id)
    WHERE message_id != '';

CREATE INDEX IF NOT EXISTS idx_messages_chat_id ON messages(chat_id);
CREATE INDEX IF NOT EXISTS idx_messages_ts_utc ON messages(ts_utc);
CREATE INDEX IF NOT EXISTS idx_messages_msg_type ON messages(msg_type);
CREATE INDEX IF NOT EXISTS idx_messages_project_id ON messages(project_id);

CREATE VIRTUAL TABLE IF NOT EXISTS messages_fts USING fts5(
    raw_text, extracted_text,
    content=messages, content_rowid=id
);

CREATE TRIGGER IF NOT EXISTS messages_ai AFTER INSERT ON messages BEGIN
    INSERT INTO messages_fts(rowid, raw_text, extracted_text)
    VALUES (new.id, new.raw_text, new.extracted_text);
END;

CREATE TRIGGER IF NOT EXISTS messages_ad AFTER DELETE ON messages BEGIN
    INSERT INTO messages_fts(messages_fts, rowid, raw_text, extracted_text)
    VALUES ('delete', old.id, old.raw_text, old.extracted_text);
END;

CREATE TRIGGER IF NOT EXISTS messages_au AFTER UPDATE ON messages BEGIN
    INSERT INTO messages_fts(messages_fts, rowid, raw_text, extracted_text)
    VALUES ('delete', old.id, old.raw_text, old.extracted_text);
    INSERT INTO messages_fts(rowid, raw_text, extracted_text)
    VALUES (new.id, new.raw_text, new.extracted_text);
END;
"""


# ── Data model ────────────────────────────────────────────────


@dataclass
class ArchiveRecord:
    """A single message to be archived."""
    platform: str = ""
    chat_id: str = ""
    thread_id: str = ""
    user_id: str = ""
    username: str = ""
    message_id: str = ""
    ts_utc: str = ""
    msg_type: str = "text"
    raw_text: str = ""
    extracted_text: str = ""
    extractor: str = ""
    file_path: str = ""
    original_name: str = ""
    mime_type: str = ""
    metadata: dict = field(default_factory=dict)
    doc_category: str = ""
    telegram_file_id: str = ""
    project_id: str = ""


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


# ── Database ──────────────────────────────────────────────────


class MessageArchiveDB:
    """SQLite WAL database with background writer thread."""

    def __init__(self, db_path: Path):
        self._path = db_path
        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._queue: queue.Queue = queue.Queue()
        self._running = True
        self._conn: Optional[sqlite3.Connection] = None
        self._lock = threading.Lock()

        # Open and init schema
        self._conn = sqlite3.connect(str(db_path), check_same_thread=False)
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.execute("PRAGMA synchronous=NORMAL")
        self._conn.execute("PRAGMA foreign_keys=OFF")
        self._conn.executescript(SCHEMA_SQL)
        self._conn.commit()

        # Start writer thread
        self._thread = threading.Thread(
            target=self._writer_loop, name="archive-writer", daemon=True,
        )
        self._thread.start()
        logger.info("MessageArchiveDB: opened %s (WAL)", db_path)

    def _writer_loop(self) -> None:
        """Consume queue, INSERT OR IGNORE each record."""
        batch: List[ArchiveRecord] = []
        last_flush = time.monotonic()
        while self._running:
            try:
                record = self._queue.get(timeout=1.0)
                batch.append(record)
            except queue.Empty:
                pass

            now = time.monotonic()
            if batch and (len(batch) >= 10 or now - last_flush >= 2.0):
                self._flush_batch(batch)
                batch.clear()
                last_flush = now

        # Drain on shutdown
        if batch:
            self._flush_batch(batch)

    def _flush_batch(self, batch: List[ArchiveRecord]) -> None:
        if not self._conn:
            return
        try:
            with self._lock:
                self._conn.executemany(
                    """INSERT OR IGNORE INTO messages
                       (platform, chat_id, thread_id, user_id, username,
                        message_id, ts_utc, msg_type, raw_text, extracted_text,
                        extractor, file_path, original_name, mime_type,
                        metadata_json, doc_category, telegram_file_id, project_id)
                       VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                    [
                        (
                            r.platform, r.chat_id, r.thread_id, r.user_id,
                            r.username, r.message_id, r.ts_utc, r.msg_type,
                            r.raw_text, r.extracted_text, r.extractor,
                            r.file_path, r.original_name, r.mime_type,
                            json.dumps(r.metadata, ensure_ascii=False),
                            r.doc_category,
                            r.telegram_file_id,
                            r.project_id,
                        )
                        for r in batch
                    ],
                )
                self._conn.commit()
        except Exception as e:
            logger.warning("MessageArchiveDB: batch flush failed: %s", e)

    def enqueue(self, record: ArchiveRecord) -> None:
        """Non-blocking: put record on writer queue."""
        if self._running:
            self._queue.put(record)

    def query(
        self,
        chat_id: Optional[str] = None,
        msg_types: Optional[List[str]] = None,
        since: Optional[str] = None,
        until: Optional[str] = None,
        keyword: Optional[str] = None,
        project_id: Optional[str] = None,
        limit: int = 100,
        offset: int = 0,
    ) -> List[Dict[str, Any]]:
        """Query messages. Uses FTS when keyword provided."""
        limit = max(1, min(limit, 2000))
        offset = max(0, offset)

        if not self._conn:
            return []

        wheres: List[str] = []
        params: List[Any] = []

        if chat_id:
            wheres.append("m.chat_id = ?")
            params.append(str(chat_id))
        if msg_types:
            placeholders = ",".join(["?"] * len(msg_types))
            wheres.append(f"m.msg_type IN ({placeholders})")
            params.extend(msg_types)
        if since:
            wheres.append("m.ts_utc >= ?")
            params.append(since)
        if until:
            wheres.append("m.ts_utc < ?")
            params.append(until)
        if project_id:
            wheres.append("m.project_id = ?")
            params.append(str(project_id))

        with self._lock:
            if keyword and keyword.strip():
                fts_wheres = list(wheres)
                fts_params = list(params)
                fts_wheres.append("messages_fts MATCH ?")
                fts_params.append(keyword.strip())
                where = " AND ".join(fts_wheres) if fts_wheres else "1=1"
                sql = f"""
                    SELECT m.* FROM messages m
                    JOIN messages_fts fts ON m.id = fts.rowid
                    WHERE {where}
                    ORDER BY m.ts_utc DESC
                    LIMIT ? OFFSET ?
                """
                fts_params.extend([limit, offset])
                rows = self._conn.execute(sql, fts_params).fetchall()
            else:
                where = " AND ".join(wheres) if wheres else "1=1"
                sql = f"""
                    SELECT m.* FROM messages m
                    WHERE {where}
                    ORDER BY m.ts_utc DESC
                    LIMIT ? OFFSET ?
                """
                params.extend([limit, offset])
                rows = self._conn.execute(sql, params).fetchall()

        # Get column names from cursor description
        col_names = [d[0] for d in self._conn.execute(
            "SELECT * FROM messages LIMIT 0"
        ).description] if rows else []
        return [dict(zip(col_names, row)) for row in rows] if rows else []

    def count(self, **kwargs) -> int:
        """Quick count with same filters as query."""
        rows = self.query(limit=1, offset=0, **kwargs)
        if not rows:
            return 0
        with self._lock:
            wheres = ["1=1"]
            params = []
            if kwargs.get("chat_id"):
                wheres.append("chat_id = ?")
                params.append(kwargs["chat_id"])
            row = self._conn.execute(
                f"SELECT COUNT(*) FROM messages WHERE {' AND '.join(wheres)}",
                params,
            ).fetchone()
            return row[0] if row else 0

    def close(self) -> None:
        """Shutdown writer thread and close connection."""
        self._running = False
        if self._thread.is_alive():
            self._thread.join(timeout=5.0)
        with self._lock:
            if self._conn:
                self._conn.close()
                self._conn = None
        logger.info("MessageArchiveDB: closed")


# ── Singleton ─────────────────────────────────────────────────

_db: Optional[MessageArchiveDB] = None
_db_lock = threading.Lock()


def get_db(db_path: Optional[Path] = None) -> MessageArchiveDB:
    """Return singleton MessageArchiveDB, creating if needed."""
    global _db
    if _db is not None:
        return _db
    with _db_lock:
        if _db is not None:
            return _db
        if db_path is None:
            from hermes_constants import get_hermes_home
            db_path = Path(str(get_hermes_home())) / "archive" / "messages.db"
        _db = MessageArchiveDB(db_path)
        return _db
