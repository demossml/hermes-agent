"""
Secretary User Store — per-Telegram-user preferences in SQLite.

DB path:   {control HERMES_HOME}/secretary_users.db
Purpose:   Lightweight user profile storage for Telegram secretary UX.
           NOT a Hermes profile — does not touch agent runtime or HERMES_HOME.

Schema:
  telegram_id    TEXT PRIMARY KEY  — Telegram user ID as string
  display_name   TEXT              — user's preferred name
  timezone       TEXT DEFAULT 'Europe/Moscow'
  digest_enabled INTEGER DEFAULT 1
  digest_hour    INTEGER DEFAULT 9  — 0–23, hour for daily digest
  onboarding_step TEXT             — NULL | 'welcome' | 'name' | 'tz' | 'email_skip' | 'digest' | 'done'
  prefs_json     TEXT DEFAULT '{}' — arbitrary JSON blob
  updated_at     TEXT              — ISO-8601 timestamp

API:
  get_user(telegram_id) -> dict | None
  upsert_user(telegram_id, **fields) -> dict
  is_onboarded(telegram_id) -> bool
  set_onboarding_step(telegram_id, step) -> None
"""

from __future__ import annotations

import json
import logging
import os
import sqlite3
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

logger = logging.getLogger(__name__)

# ── DB location ───────────────────────────────────────────────

_DB_FILENAME = "secretary_users.db"

_lock = threading.Lock()

# Thread-local connections — one per thread, auto-closed on thread exit.
# This avoids "SQLite objects created in a thread can only be used in that same thread"
# while keeping connection reuse per-thread for performance.
_tls = threading.local()


def _hermes_home() -> Path:
    """Control (default) HERMES_HOME — user prefs live here, not in secretary profiles."""
    try:
        from hermes_constants import get_hermes_home  # type: ignore[import-untyped]
        return Path(get_hermes_home())
    except ImportError:
        return Path(os.environ.get("HERMES_HOME", str(Path.home() / ".hermes")))


def _db_path() -> Path:
    return _hermes_home() / _DB_FILENAME


# ── Schema ────────────────────────────────────────────────────

_SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS secretary_users (
    telegram_id    TEXT PRIMARY KEY,
    display_name   TEXT,
    timezone       TEXT NOT NULL DEFAULT 'Europe/Moscow',
    digest_enabled INTEGER NOT NULL DEFAULT 1,
    digest_hour    INTEGER NOT NULL DEFAULT 9,
    onboarding_step TEXT,
    prefs_json     TEXT NOT NULL DEFAULT '{}',
    last_digest_date TEXT,
    updated_at     TEXT NOT NULL
);
"""

# Migration: add columns that may not exist in older DBs
_MIGRATIONS = [
    "ALTER TABLE secretary_users ADD COLUMN last_digest_date TEXT",
]

# Valid onboarding steps in order
_ONBOARDING_STEPS = frozenset({
    None, "welcome", "name", "tz", "email_skip", "digest", "done",
})

# Fields that can be upserted (excludes telegram_id — that's the key)
_UPSERTABLE_FIELDS = frozenset({
    "display_name",
    "timezone",
    "digest_enabled",
    "digest_hour",
    "onboarding_step",
    "prefs_json",
    "last_digest_date",
})


# ── Connection management ─────────────────────────────────────

def _get_conn() -> sqlite3.Connection:
    """Return a thread-local connection, creating + migrating if needed."""
    conn = getattr(_tls, "conn", None)
    if conn is None:
        db_dir = _db_path().parent
        db_dir.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(str(_db_path()), check_same_thread=False)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA foreign_keys=ON")
        conn.execute(_SCHEMA_SQL)
        # Run migrations for columns added after initial schema
        for mig in _MIGRATIONS:
            try:
                conn.execute(mig)
            except sqlite3.OperationalError:
                pass  # column already exists
        conn.commit()
        _tls.conn = conn
    return conn


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


# ── Public API ────────────────────────────────────────────────

def get_user(telegram_id: str) -> Optional[dict[str, Any]]:
    """Return user dict or None if not found.

    Dict keys: telegram_id, display_name, timezone, digest_enabled,
               digest_hour, onboarding_step, prefs_json, updated_at.
    """
    tid = str(telegram_id).strip()
    if not tid:
        return None

    with _lock:
        conn = _get_conn()
        row = conn.execute(
            "SELECT * FROM secretary_users WHERE telegram_id = ?",
            (tid,),
        ).fetchone()

    if row is None:
        return None

    result = dict(row)
    # Parse prefs_json for convenience
    try:
        result["prefs"] = json.loads(result.get("prefs_json", "{}") or "{}")
    except (json.JSONDecodeError, TypeError):
        result["prefs"] = {}
    return result


def upsert_user(telegram_id: str, **fields: Any) -> dict[str, Any]:
    """Create or update a user record. Returns the full user dict.

    Allowed fields: display_name, timezone, digest_enabled, digest_hour,
                    onboarding_step, prefs_json.
    Unknown fields are silently ignored.

    digest_enabled is coerced to int (0/1).
    digest_hour is coerced to int and clamped to 0–23.
    """
    tid = str(telegram_id).strip()
    if not tid:
        raise ValueError("telegram_id must not be empty")

    now = _now_iso()

    # Build clean field dict — only known upsertable fields
    clean: dict[str, Any] = {}
    for key, value in fields.items():
        if key not in _UPSERTABLE_FIELDS:
            continue
        if key == "digest_enabled":
            clean[key] = 1 if value else 0
        elif key == "digest_hour":
            try:
                h = int(value)
                clean[key] = max(0, min(23, h))
            except (ValueError, TypeError):
                clean[key] = 9
        elif key == "prefs_json":
            # Accept dict or JSON string
            if isinstance(value, dict):
                clean[key] = json.dumps(value, ensure_ascii=False)
            elif isinstance(value, str):
                # Validate it's parseable JSON
                try:
                    json.loads(value)
                    clean[key] = value
                except (json.JSONDecodeError, TypeError):
                    clean[key] = "{}"
            else:
                clean[key] = "{}"
        elif key == "onboarding_step":
            step = str(value).strip() if value else None
            if step not in _ONBOARDING_STEPS:
                step = None
            clean[key] = step
        else:
            clean[key] = str(value) if value is not None else None

    clean["updated_at"] = now

    with _lock:
        conn = _get_conn()

        # Try UPDATE first, then INSERT if no rows affected
        existing = conn.execute(
            "SELECT telegram_id FROM secretary_users WHERE telegram_id = ?",
            (tid,),
        ).fetchone()

        if existing:
            set_clause = ", ".join(f"{k} = ?" for k in clean)
            values = list(clean.values()) + [tid]
            conn.execute(
                f"UPDATE secretary_users SET {set_clause} WHERE telegram_id = ?",
                values,
            )
        else:
            columns = ["telegram_id"] + list(clean.keys())
            placeholders = ["?"] * len(columns)
            values = [tid] + list(clean.values())
            conn.execute(
                f"INSERT INTO secretary_users ({', '.join(columns)}) "
                f"VALUES ({', '.join(placeholders)})",
                values,
            )
        conn.commit()

    logger.debug("Upserted user %s with fields: %s", tid, list(clean.keys()))
    return get_user(tid) or {}


def is_onboarded(telegram_id: str) -> bool:
    """Return True if user completed onboarding (step == 'done')."""
    user = get_user(telegram_id)
    if user is None:
        return False
    return user.get("onboarding_step") == "done"


def set_onboarding_step(telegram_id: str, step: Optional[str]) -> None:
    """Set the onboarding step for a user. Creates user record if not exists."""
    upsert_user(telegram_id, onboarding_step=step)


def get_pref(telegram_id: str, key: str, default: Any = None) -> Any:
    """Read a single key from the user's prefs_json blob.

    Returns ``default`` if the user doesn't exist or the key is absent.
    """
    user = get_user(telegram_id)
    if not user:
        return default
    return (user.get("prefs") or {}).get(key, default)


def set_pref(telegram_id: str, key: str, value: Any) -> dict[str, Any]:
    """Set (or clear, if ``value is None``) a single prefs_json key.

    Returns the updated prefs dict. Creates the user record if absent.
    """
    user = get_user(telegram_id) or {}
    prefs = dict(user.get("prefs") or {})
    if value is None:
        prefs.pop(key, None)
    else:
        prefs[key] = value
    upsert_user(telegram_id, prefs_json=prefs)
    return prefs


def clear_pref(telegram_id: str, key: str) -> dict[str, Any]:
    """Convenience wrapper — delete a prefs key. Returns updated prefs."""
    return set_pref(telegram_id, key, None)


def get_digest_users() -> list[dict[str, Any]]:
    """Return all onboarded users with digest_enabled=1."""
    with _lock:
        conn = _get_conn()
        rows = conn.execute(
            "SELECT * FROM secretary_users "
            "WHERE onboarding_step = 'done' AND digest_enabled = 1"
        ).fetchall()
    return [dict(r) for r in rows]


def mark_digest_sent(telegram_id: str) -> None:
    """Record that digest was sent today (ISO date)."""
    today = datetime.now(timezone.utc).date().isoformat()
    upsert_user(telegram_id, last_digest_date=today)


def should_send_digest(telegram_id: str) -> bool:
    """Return True if the user hasn't received a digest today.

    Also checks digest_enabled and onboarding.
    """
    user = get_user(telegram_id)
    if not user:
        return False
    if user.get("onboarding_step") != "done":
        return False
    if not user.get("digest_enabled"):
        return False
    today = datetime.now(timezone.utc).date().isoformat()
    last = user.get("last_digest_date") or ""
    return last != today
