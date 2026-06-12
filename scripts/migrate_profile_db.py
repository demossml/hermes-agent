#!/usr/bin/env python3
"""
Migrate/create the DuckDB database for a Hermes profile or clone.

Usage::

    python scripts/migrate_profile_db.py --profile bot-1
    python scripts/migrate_profile_db.py --profile default

Creates ``~/.hermes/profiles/<name>/data/evotor.duckdb`` with the
same table structure as the default profile.  Safe to run multiple
times — existing tables and data are never overwritten.
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
from pathlib import Path

logging.basicConfig(level=logging.INFO, format="%(message)s")
logger = logging.getLogger(__name__)

try:
    import duckdb
    HAS_DUCKDB = True
except ImportError:
    HAS_DUCKDB = False
    logger.error("DuckDB is not installed. Run: pip install duckdb")
    sys.exit(1)

# All tables this script knows how to create.
# Each key is a table name, value is the CREATE TABLE SQL.
KNOWN_TABLES: dict[str, str] = {
    "group_messages": """
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
            embedding         FLOAT[],
            created_at        TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """,
    "chat_rules": """
        CREATE TABLE IF NOT EXISTS chat_rules (
            id        INTEGER PRIMARY KEY,
            group_id  TEXT NOT NULL,
            rule      TEXT NOT NULL,
            priority  INTEGER DEFAULT 0,
            active    BOOLEAN DEFAULT TRUE
        )
    """,
    "workflows": """
        CREATE TABLE IF NOT EXISTS workflows (
            id            TEXT PRIMARY KEY,
            task          TEXT NOT NULL,
            language      TEXT,
            status        TEXT NOT NULL DEFAULT 'in_progress',
            iteration     INTEGER NOT NULL DEFAULT 0,
            max_iter      INTEGER NOT NULL DEFAULT 4,
            coder_id      TEXT,
            tester_id     TEXT,
            final_code    TEXT,
            final_review  TEXT,
            result_json   TEXT,
            created_at    TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            updated_at    TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """,
    "workflow_iterations": """
        CREATE TABLE IF NOT EXISTS workflow_iterations (
            id            BIGINT PRIMARY KEY DEFAULT nextval('seq_wf_iter_id'),
            workflow_id   TEXT NOT NULL,
            iteration     INTEGER NOT NULL,
            phase         TEXT NOT NULL,
            code          TEXT,
            review        TEXT,
            test_code     TEXT,
            exec_report   TEXT,
            created_at    TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """,
}

KNOWN_SEQUENCES = [
    "CREATE SEQUENCE IF NOT EXISTS seq_group_messages_id START 1",
    "CREATE SEQUENCE IF NOT EXISTS seq_wf_iter_id START 1",
]

KNOWN_INDEXES = [
    "CREATE INDEX IF NOT EXISTS idx_chat ON group_messages (chat_id, timestamp)",
    "CREATE INDEX IF NOT EXISTS idx_timestamp ON group_messages (timestamp)",
    "CREATE INDEX IF NOT EXISTS idx_sender ON group_messages (sender_id, chat_id)",
    "CREATE INDEX IF NOT EXISTS idx_workflows_status ON workflows (status, updated_at DESC)",
    "CREATE INDEX IF NOT EXISTS idx_wf_iter ON workflow_iterations (workflow_id, iteration)",
]

OBSERVER_GROUPS_TEMPLATE: list[dict] = []


# ── Helpers ───────────────────────────────────────────────────


def _profile_db_path(profile_name: str) -> Path:
    """Return the DuckDB path for a given profile."""
    home = Path.home() / ".hermes"
    if profile_name in ("default", ""):
        db_dir = home / "data"
    else:
        db_dir = home / "profiles" / profile_name / "data"
    db_dir.mkdir(parents=True, exist_ok=True)
    return db_dir / "evotor.duckdb"


def _create_tables(conn, *, verbose: bool = False) -> int:
    """Create all known tables, sequences, and indexes. Returns count created."""
    created = 0

    for sql in KNOWN_SEQUENCES:
        try:
            conn.execute(sql)
            created += 1
        except Exception as e:
            if verbose:
                logger.debug("sequence: %s", e)

    for name, sql in KNOWN_TABLES.items():
        try:
            conn.execute(sql)
            if verbose:
                logger.info("  ✓ %s", name)
            created += 1
        except Exception as e:
            logger.warning("  ✗ %s: %s", name, e)

    for sql in KNOWN_INDEXES:
        try:
            conn.execute(sql)
            created += 1
        except Exception:
            pass

    return created


def _copy_schema(source_path: Path, dest_path: Path, *, verbose: bool = False) -> bool:
    """Copy table schemas from source DuckDB to destination.

    Reads all CREATE TABLE statements from source and executes them
    on destination (using IF NOT EXISTS for safety).
    """
    if not source_path.exists():
        if verbose:
            logger.info("Source DB not found, creating empty schema")
        return True

    try:
        src_conn = duckdb.connect(str(source_path))
        rows = src_conn.execute(
            "SELECT sql FROM sqlite_master WHERE type='table' AND sql IS NOT NULL"
        ).fetchall()
        src_conn.close()
    except Exception as e:
        logger.warning("Could not read source schema: %s", e)
        return False

    try:
        dest_conn = duckdb.connect(str(dest_path))
        for (sql,) in rows:
            safe_sql = sql.replace("CREATE TABLE ", "CREATE TABLE IF NOT EXISTS ")
            try:
                dest_conn.execute(safe_sql)
            except Exception:
                pass
        dest_conn.close()
        if verbose:
            logger.info("  Copied %d table schemas from source", len(rows))
        return True
    except Exception as e:
        logger.warning("Could not apply source schema: %s", e)
        return False


def _create_observer_groups(profile_dir: Path) -> bool:
    """Create observer_groups.json if it doesn't exist."""
    groups_path = profile_dir / "observer_groups.json"
    if groups_path.exists():
        return False
    groups_path.write_text(
        json.dumps(OBSERVER_GROUPS_TEMPLATE, indent=2, ensure_ascii=False),
        encoding="utf-8",
    )
    return True


# ── Main ──────────────────────────────────────────────────────


def migrate_profile(profile_name: str, *, verbose: bool = False) -> bool:
    """Create/migrate the DuckDB database for a profile.

    Returns True on success.
    """
    home = Path.home() / ".hermes"
    if profile_name in ("default", ""):
        profile_dir = home / "data"
    else:
        profile_dir = home / "profiles" / profile_name / "data"

    profile_dir.mkdir(parents=True, exist_ok=True)
    db_path = profile_dir / "evotor.duckdb"
    source_path = home / "data" / "evotor.duckdb"

    logger.info("Profile:  %s", profile_name)
    logger.info("Database: %s", db_path)

    # 1. Try to copy schema from default DB
    schema_copied = _copy_schema(source_path, db_path, verbose=verbose)

    # 2. Ensure all known tables exist (idempotent)
    try:
        conn = duckdb.connect(str(db_path))
        created = _create_tables(conn, verbose=verbose)
        conn.close()
    except Exception as e:
        logger.error("Failed to open target DB: %s", e)
        return False

    # 3. Create observer_groups.json
    groups_created = _create_observer_groups(profile_dir)
    if groups_created:
        logger.info("  ✓ observer_groups.json created")

    # 4. Report
    if schema_copied and created > 0:
        logger.info("✓ Profile '%s' ready — %d objects created", profile_name, created)
    elif created > 0:
        logger.info("✓ Profile '%s' ready — %d objects (empty schema)", profile_name, created)
    else:
        logger.info("✓ Profile '%s' already up to date", profile_name)

    return True


def main():
    parser = argparse.ArgumentParser(
        description="Create/migrate DuckDB for a Hermes profile or clone",
    )
    parser.add_argument(
        "--profile", "-p",
        required=True,
        help="Profile name (e.g. 'bot-1', 'default')",
    )
    parser.add_argument(
        "--verbose", "-v",
        action="store_true",
        help="Show detailed progress",
    )
    args = parser.parse_args()

    success = migrate_profile(args.profile, verbose=args.verbose)
    sys.exit(0 if success else 1)


if __name__ == "__main__":
    main()
