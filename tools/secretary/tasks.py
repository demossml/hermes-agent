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
    updated_at TEXT NOT NULL,
    due_at TEXT NOT NULL DEFAULT '',
    recurrence TEXT NOT NULL DEFAULT '',
    next_run TEXT NOT NULL DEFAULT ''
);
"""

_MIGRATE_COLUMNS = [
    ("due_at", "TEXT NOT NULL DEFAULT ''"),
    ("recurrence", "TEXT NOT NULL DEFAULT ''"),
    ("next_run", "TEXT NOT NULL DEFAULT ''"),
]

RECURRENCES = ("daily", "weekly", "monthly")

_RECURRENCE_DELTA_DAYS = {"daily": 1, "weekly": 7, "monthly": 30}


def _migrate(conn: sqlite3.Connection) -> None:
    """Add L6 columns to an existing tasks table."""
    existing = {r[1] for r in conn.execute("PRAGMA table_info(tasks)").fetchall()}
    for name, decl in _MIGRATE_COLUMNS:
        if name not in existing:
            conn.execute(f"ALTER TABLE tasks ADD COLUMN {name} {decl}")
    conn.commit()


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
        _migrate(conn)
        _tls.conn = conn
    return conn


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def add_task(telegram_id: str, text: str, due_at: str = "") -> dict[str, Any]:
    tid = str(telegram_id).strip()
    if not tid or not text.strip():
        raise ValueError("telegram_id and text required")
    now = _now_iso()
    with _lock:
        conn = _get_conn()
        cur = conn.execute(
            "INSERT INTO tasks (telegram_id, text, status, created_at, updated_at, due_at) "
            "VALUES (?, ?, 'open', ?, ?, ?)",
            (tid, text.strip(), now, now, due_at or ""),
        )
        conn.commit()
    return {"id": cur.lastrowid, "text": text.strip(), "status": "open", "due_at": due_at or ""}


def add_schedule_task(telegram_id: str, text: str, recurrence: str) -> dict[str, Any]:
    """Add a recurring task. recurrence ∈ {daily, weekly, monthly}."""
    tid = str(telegram_id).strip()
    rec = (recurrence or "").strip().lower()
    if not tid or not text.strip() or rec not in RECURRENCES:
        raise ValueError("telegram_id, text and valid recurrence required")
    now = _now_iso()
    next_run = _next_run_iso(rec, now)
    with _lock:
        conn = _get_conn()
        cur = conn.execute(
            "INSERT INTO tasks (telegram_id, text, status, created_at, updated_at, recurrence, next_run) "
            "VALUES (?, ?, 'open', ?, ?, ?, ?)",
            (tid, text.strip(), now, now, rec, next_run),
        )
        conn.commit()
    return {"id": cur.lastrowid, "text": text.strip(), "recurrence": rec, "next_run": next_run}


def _next_run_iso(recurrence: str, from_iso: str) -> str:
    from datetime import timedelta
    days = _RECURRENCE_DELTA_DAYS.get(recurrence, 1)
    try:
        base = datetime.fromisoformat(from_iso)
    except ValueError:
        base = datetime.now(timezone.utc)
    return (base + timedelta(days=days)).isoformat()


def list_tasks(telegram_id: str, limit: int = 10) -> list[dict[str, Any]]:
    """List OPEN one-off tasks (no recurrence)."""
    tid = str(telegram_id).strip()
    if not tid:
        return []
    with _lock:
        conn = _get_conn()
        rows = conn.execute(
            "SELECT * FROM tasks WHERE telegram_id = ? AND status = 'open' AND recurrence = '' "
            "ORDER BY created_at DESC LIMIT ?",
            (tid, limit),
        ).fetchall()
    return [dict(r) for r in rows]


def list_schedule(telegram_id: str, limit: int = 20) -> list[dict[str, Any]]:
    """List OPEN recurring tasks."""
    tid = str(telegram_id).strip()
    if not tid:
        return []
    with _lock:
        conn = _get_conn()
        rows = conn.execute(
            "SELECT * FROM tasks WHERE telegram_id = ? AND status = 'open' AND recurrence != '' "
            "ORDER BY next_run ASC LIMIT ?",
            (tid, limit),
        ).fetchall()
    return [dict(r) for r in rows]


def advance_schedule(telegram_id: str, task_id: int) -> bool:
    """Bump a recurring task's next_run by its recurrence period."""
    tid = str(telegram_id).strip()
    with _lock:
        conn = _get_conn()
        row = conn.execute(
            "SELECT recurrence, next_run FROM tasks WHERE id = ? AND telegram_id = ? AND recurrence != ''",
            (task_id, tid),
        ).fetchone()
        if not row:
            return False
        rec = row["recurrence"]
        cur_run = row["next_run"] or _now_iso()
        new_run = _next_run_iso(rec, cur_run)
        cur = conn.execute(
            "UPDATE tasks SET next_run = ?, updated_at = ? WHERE id = ? AND telegram_id = ?",
            (new_run, _now_iso(), task_id, tid),
        )
        conn.commit()
        return cur.rowcount > 0


def remove_schedule(telegram_id: str, task_id: int) -> bool:
    """Remove a recurring task (mark done)."""
    return mark_done(telegram_id, task_id)


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
