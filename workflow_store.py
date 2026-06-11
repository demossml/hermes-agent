"""
Workflow Store — DuckDB-backed persistence for CodeGenerationWorkflow sessions.

Database: ``~/.hermes/data/workflows.duckdb``
"""

from __future__ import annotations

import json
import logging
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

DB_PATH = Path.home() / ".hermes" / "data" / "workflows.duckdb"
_conn = None


def _get_conn():
    global _conn
    if _conn is not None:
        return _conn
    import duckdb
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    _conn = duckdb.connect(str(DB_PATH))
    _conn.execute("""
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
    """)
    _conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_workflows_status ON workflows (status, updated_at DESC)"
    )
    # Per-iteration history
    _conn.execute(
        "CREATE SEQUENCE IF NOT EXISTS seq_wf_iter_id START 1"
    )
    _conn.execute("""
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
    """)
    _conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_wf_iter ON workflow_iterations (workflow_id, iteration)"
    )
    return _conn


# ── CRUD ──────────────────────────────────────────────────────


def save_workflow(
    task_id: str,
    task: str,
    *,
    language: str | None = None,
    status: str = "in_progress",
    iteration: int = 0,
    max_iter: int = 4,
    coder_id: str = "",
    tester_id: str = "",
    final_code: str = "",
    final_review: str = "",
    result_json: str = "",
) -> bool:
    """Insert or update a workflow record."""
    try:
        conn = _get_conn()
        existing = conn.execute(
            "SELECT id FROM workflows WHERE id = ?", [task_id]
        ).fetchone()

        if existing:
            conn.execute("""
                UPDATE workflows SET
                    status=?, iteration=?, final_code=?, final_review=?,
                    result_json=?, updated_at=CURRENT_TIMESTAMP
                WHERE id=?
            """, [status, iteration, final_code, final_review, result_json, task_id])
        else:
            conn.execute("""
                INSERT INTO workflows
                    (id, task, language, status, iteration, max_iter,
                     coder_id, tester_id, final_code, final_review, result_json)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, [task_id, task, language, status, iteration, max_iter,
                  coder_id, tester_id, final_code, final_review, result_json])
        return True
    except Exception as e:
        logger.debug("workflow_store save: %s", e)
        return False


def get_workflow(task_id: str) -> dict | None:
    """Get a single workflow by ID."""
    try:
        conn = _get_conn()
        row = conn.execute(
            "SELECT * FROM workflows WHERE id = ?", [task_id]
        ).fetchone()
        return _row_to_dict(row) if row else None
    except Exception:
        return None


def list_workflows(status: str | None = None, limit: int = 20) -> list[dict]:
    """List workflows, newest first. Optionally filter by status."""
    try:
        conn = _get_conn()
        if status:
            rows = conn.execute(
                "SELECT * FROM workflows WHERE status = ? ORDER BY updated_at DESC LIMIT ?",
                [status, limit],
            ).fetchall()
        else:
            rows = conn.execute(
                "SELECT * FROM workflows ORDER BY updated_at DESC LIMIT ?",
                [limit],
            ).fetchall()
        return [_row_to_dict(r) for r in rows]
    except Exception:
        return []


def update_workflow_status(task_id: str, status: str, iteration: int = 0) -> bool:
    """Quick status update."""
    try:
        conn = _get_conn()
        conn.execute(
            "UPDATE workflows SET status=?, iteration=?, updated_at=CURRENT_TIMESTAMP WHERE id=?",
            [status, iteration, task_id],
        )
        return True
    except Exception:
        return False


def save_workflow_result(
    task_id: str,
    status: str,
    iteration: int,
    final_code: str,
    final_review: str,
    result_json: str = "",
) -> bool:
    """Save final result after workflow completes."""
    try:
        conn = _get_conn()
        conn.execute("""
            UPDATE workflows SET
                status=?, iteration=?, final_code=?, final_review=?,
                result_json=?, updated_at=CURRENT_TIMESTAMP
            WHERE id=?
        """, [status, iteration, final_code, final_review, result_json, task_id])
        return True
    except Exception:
        return False


def save_iteration(
    workflow_id: str,
    iteration: int,
    phase: str,
    code: str = "",
    review: str = "",
    test_code: str = "",
    exec_report: str = "",
) -> bool:
    """Save one iteration's code + review + test results."""
    try:
        conn = _get_conn()
        conn.execute("""
            INSERT INTO workflow_iterations
                (workflow_id, iteration, phase, code, review, test_code, exec_report)
            VALUES (?, ?, ?, ?, ?, ?, ?)
        """, [workflow_id, iteration, phase, code, review, test_code, exec_report])
        return True
    except Exception as e:
        logger.debug("save_iteration: %s", e)
        return False


def get_iterations(workflow_id: str) -> list[dict]:
    """Get all iterations for a workflow, ordered by iteration."""
    try:
        conn = _get_conn()
        rows = conn.execute(
            "SELECT * FROM workflow_iterations WHERE workflow_id = ? ORDER BY iteration, id",
            [workflow_id],
        ).fetchall()
        return [
            {
                "id": r[0], "workflow_id": r[1], "iteration": r[2],
                "phase": r[3], "code": r[4] or "", "review": r[5] or "",
                "test_code": r[6] or "", "exec_report": r[7] or "",
                "created_at": str(r[8]) if r[8] else "",
            }
            for r in rows
        ]
    except Exception:
        return []


def delete_workflow(task_id: str) -> bool:
    """Delete a workflow and all its iterations."""
    try:
        conn = _get_conn()
        conn.execute("DELETE FROM workflow_iterations WHERE workflow_id = ?", [task_id])
        conn.execute("DELETE FROM workflows WHERE id = ?", [task_id])
        return True
    except Exception as e:
        logger.debug("delete_workflow: %s", e)
        return False


# ── Helpers ───────────────────────────────────────────────────


def _row_to_dict(row) -> dict:
    return {
        "id": row[0],
        "task": row[1],
        "language": row[2],
        "status": row[3],
        "iteration": row[4],
        "max_iter": row[5],
        "coder_id": row[6],
        "tester_id": row[7],
        "final_code": row[8] or "",
        "final_review": row[9] or "",
        "result_json": row[10] or "",
        "created_at": str(row[11]) if row[11] else "",
        "updated_at": str(row[12]) if row[12] else "",
    }
