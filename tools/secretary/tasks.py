"""
Secretary tasks — simple personal todo list in SQLite.

DB: {HERMES_HOME}/secretary_tasks.db
API: add_task, list_tasks, mark_done
"""

from __future__ import annotations

import logging
import os
import sqlite3
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

logger = logging.getLogger(__name__)

_DB_FILENAME = "secretary_tasks.db"
_lock = threading.Lock()
_tls = threading.local()

_SCHEMA = """
CREATE TABLE IF NOT EXISTS tasks (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    telegram_id TEXT NOT NULL,
    text TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'open',
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
"""


def _hermes_home() -> Path:
    try:
        from hermes_constants import get_hermes_home
        return Path(get_hermes_home())
    except ImportError:
        return Path(os.environ.get("HERMES_HOME", str(Path.home() / ".hermes")))


def _db_path() -> Path:
    return _hermes_home() / _DB_FILENAME


def _get_conn() -> sqlite3.Connection:
    conn = getattr(_tls, "conn", None)
    if conn is None:
        _db_path().parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(str(_db_path()), check_same_thread=False)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute(_SCHEMA)
        conn.commit()
        _tls.conn = conn
    return conn


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def add_task(telegram_id: str, text: str) -> dict[str, Any]:
    tid = str(telegram_id).strip()
    if not tid or not text.strip():
        raise ValueError("telegram_id and text required")
    now = _now_iso()
    with _lock:
        conn = _get_conn()
        cur = conn.execute(
            "INSERT INTO tasks (telegram_id, text, status, created_at, updated_at) VALUES (?, ?, 'open', ?, ?)",
            (tid, text.strip(), now, now),
        )
        conn.commit()
    return {"id": cur.lastrowid, "text": text.strip(), "status": "open"}


def list_tasks(telegram_id: str, limit: int = 10) -> list[dict[str, Any]]:
    tid = str(telegram_id).strip()
    if not tid:
        return []
    with _lock:
        conn = _get_conn()
        rows = conn.execute(
            "SELECT * FROM tasks WHERE telegram_id = ? AND status = 'open' ORDER BY created_at DESC LIMIT ?",
            (tid, limit),
        ).fetchall()
    return [dict(r) for r in rows]


def mark_done(telegram_id: str, task_id: int) -> bool:
    tid = str(telegram_id).strip()
    now = _now_iso()
    with _lock:
        conn = _get_conn()
        cur = conn.execute(
            "UPDATE tasks SET status = 'done', updated_at = ? WHERE id = ? AND telegram_id = ?",
            (now, task_id, tid),
        )
        conn.commit()
        return cur.rowcount > 0
