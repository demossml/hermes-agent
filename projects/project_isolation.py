"""
Project Isolation Layer — enforces strict project boundaries.

All memory operations (LTM, DuckDB, session history) for non-orchestrator
agents are automatically scoped to the agent's bound project.  The
orchestrator (level 0) retains full cross-project access.

Usage::

    from projects.project_isolation import (
        get_agent_project_id,
        check_project_access,
        get_active_project_filter,
    )

    # In ChatHistoryDB search:
    proj_filter = get_active_project_filter(caller_agent_id="coder")
    if proj_filter:
        results = db.search(..., project_id=proj_filter)

    # In LTM operations:
    proj_id = get_agent_project_id("coder")  # → "my-app" or None (orchestrator)
"""

from __future__ import annotations

import logging
from typing import Any

logger = logging.getLogger(__name__)


# ═══════════════════════════════════════════════════════════════
# Resolving the current project for an agent
# ═══════════════════════════════════════════════════════════════

def get_agent_project_id(agent_id: str) -> str | None:
    """Return the ``project_id`` bound to *agent_id*.

    - Orchestrator → ``None`` (full access to all projects)
    - Sub-agent in a project → the project's id (e.g. ``"my-app"``)
    - Sub-agent NOT in a project → ``None``

    Reads from AgentRegistry's in-memory config.  Falls back to the
    process-level ``_current_project_id`` global.
    """
    if agent_id == "orchestrator":
        return None

    # Try to read from the agent registry first
    try:
        from agent_registry import get_registry
        registry = get_registry()
        cfg = registry.get(agent_id)
        if cfg and cfg.get("project_id"):
            return cfg["project_id"]
    except Exception:
        pass

    # Fallback: process-level global
    try:
        from projects.project_context import get_current_project_id
        return get_current_project_id()
    except Exception:
        pass

    return None


def get_active_project_filter(
    caller_agent_id: str = "",
) -> str | None:
    """Return the project_id to filter by, or None for full access.

    Returns ``None`` for orchestrator → no filter applied.
    Returns the project_id string for sub-agents → strict filtering.
    """
    if not caller_agent_id or caller_agent_id == "orchestrator":
        return None
    return get_agent_project_id(caller_agent_id)


# ═══════════════════════════════════════════════════════════════
# Access enforcement
# ═══════════════════════════════════════════════════════════════

def check_project_access(
    caller_agent_id: str,
    target_project_id: str | None,
) -> bool:
    """Check if *caller_agent_id* is allowed to access *target_project_id*.

    Rules:
    - Orchestrator (level 0) → always allowed
    - Sub-agent → allowed ONLY if target matches its own project_id
    - Sub-agent with no project → allowed (no project isolation active)

    Returns ``True`` if access is allowed, ``False`` otherwise.
    """
    if caller_agent_id == "orchestrator":
        return True

    caller_project = get_agent_project_id(caller_agent_id)
    if caller_project is None:
        return True  # No project isolation active

    if target_project_id is None:
        # Target has no project — allow if caller also has no project,
        # OR if the target data is un-tagged (legacy records).
        # Sub-agents can see un-tagged data but not other projects' data.
        return True

    return caller_project == target_project_id


def enforce_project_isolation(
    caller_agent_id: str,
    target_project_id: str | None,
    operation: str = "access memory",
) -> str | None:
    """Check project access and return an error message if denied.

    Returns ``None`` if access is allowed.
    Returns an error string if access is denied.

    This is the central enforcement point called before any memory
    operation (LTM search, DuckDB query, session read).
    """
    if not check_project_access(caller_agent_id, target_project_id):
        caller_proj = get_agent_project_id(caller_agent_id) or "(none)"
        target_proj = target_project_id or "(none)"
        msg = (
            f"[PROJECT ISOLATION] Agent '{caller_agent_id}' "
            f"(project={caller_proj}) cannot {operation} "
            f"from project '{target_proj}'."
        )
        logger.warning(msg)
        return msg
    return None


# ═══════════════════════════════════════════════════════════════
# Project metadata for storage operations
# ═══════════════════════════════════════════════════════════════

def get_project_metadata_for_agent(
    agent_id: str,
) -> dict[str, Any]:
    """Return a dict of project metadata to attach to storage records.

    Called before adding entries to LTM or DuckDB so every record
    carries its project origin.

    Returns an empty dict for orchestrator or when no project is
    active (no overhead).
    """
    proj_id = get_agent_project_id(agent_id)
    if not proj_id:
        return {}

    try:
        from projects.project_context import get_current_project_name
        name = get_current_project_name() or proj_id
    except Exception:
        name = proj_id

    return {
        "project_id": proj_id,
        "project_name": name,
    }
