"""
Project State Manager — save and restore project runtime state.

When switching projects, the system:
1. **Pauses** all active workflows in the current project
2. **Saves** runtime state to ``projects/<id>/state.json``
3. **Loads** the new project's saved state
4. **Notifies** about unfinished tasks left behind

Multiple projects can be "open" but only ONE is active at any time.
Switching between them preserves workflow progress.

Usage::

    from projects.project_state import ProjectStateManager

    psm = ProjectStateManager()
    psm.pause_current_project("my-app")   # save state, stop workflows
    psm.restore_project("other-app")      # load state, show notifications
"""

from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)


def _get_hermes_home() -> Path:
    try:
        from hermes_constants import get_hermes_home
        return get_hermes_home()
    except ImportError:
        import os
        return Path(os.environ.get("HERMES_HOME", Path.home() / ".hermes"))


# ═══════════════════════════════════════════════════════════════
# ProjectStateManager
# ═══════════════════════════════════════════════════════════════

class ProjectStateManager:
    """Manages runtime state snapshots for project switching.

    Parameters
    ----------
    hermes_home : str or Path, optional
        Override the Hermes home directory.
    """

    def __init__(self, hermes_home: str | Path | None = None):
        self._home = Path(hermes_home) if hermes_home else _get_hermes_home()
        self._projects_dir = self._home / "projects"

    # ── Path helpers ─────────────────────────────────────────

    def _state_path(self, project_id: str) -> Path:
        return self._projects_dir / project_id / "state" / "workflows" / "snapshot.json"

    # ── Public API ────────────────────────────────────────────

    def pause_current_project(self, project_id: str) -> dict[str, Any]:
        """Save the runtime state of *project_id* and pause its workflows.

        Called BEFORE switching away from a project.

        Returns a summary dict with:
        - paused_workflows: count
        - unfinished_tasks: count
        - ltm_snapshot: whether LTM snapshot was taken
        """
        state: dict[str, Any] = {
            "project_id": project_id,
            "paused_at": datetime.now(timezone.utc).isoformat(),
            "paused_workflows": {},
            "unfinished_tasks": 0,
        }

        # ── Pause active workflows ──────────────────────────
        try:
            paused_count = self._pause_workflows(state)
            state["paused_workflow_count"] = paused_count
        except Exception as e:
            logger.debug(f"Workflow pause skipped: {e}")
            state["paused_workflow_count"] = 0

        # ── Snapshot unfinished tasks ───────────────────────
        try:
            state["unfinished_tasks"] = self._count_unfinished_tasks()
        except Exception:
            state["unfinished_tasks"] = 0

        # ── Persist to disk ──────────────────────────────────
        self._save_state(project_id, state)

        logger.info(
            f"Project '{project_id}' state saved: "
            f"{state['paused_workflow_count']} workflows paused, "
            f"{state['unfinished_tasks']} unfinished tasks"
        )
        return state

    def restore_project(self, project_id: str) -> dict[str, Any]:
        """Load saved state for *project_id* and return a summary.

        Called AFTER switching to a project.

        Returns a summary dict with:
        - has_saved_state: bool
        - paused_workflows: count
        - unfinished_tasks: count
        - notifications: list of user-facing messages
        """
        state = self._load_state(project_id)
        summary: dict[str, Any] = {
            "project_id": project_id,
            "has_saved_state": state is not None,
            "notifications": [],
        }

        if not state:
            return summary

        # ── Report paused workflows ──────────────────────────
        paused_count = state.get("paused_workflow_count", 0)
        if paused_count:
            summary["paused_workflow_count"] = paused_count
            summary["notifications"].append(
                f"{paused_count} workflow(s) were paused in this project. "
                f"Resume with: /workflow continue"
            )

        # ── Report unfinished tasks ──────────────────────────
        unfinished = state.get("unfinished_tasks", 0)
        if unfinished:
            summary["unfinished_tasks"] = unfinished
            summary["notifications"].append(
                f"{unfinished} unfinished task(s) remain. "
                f"Review with: /agents"
            )

        # ── Update last-active timestamp ─────────────────────
        state["last_active_at"] = datetime.now(timezone.utc).isoformat()
        self._save_state(project_id, state)

        return summary

    def get_project_state_summary(self, project_id: str) -> dict[str, Any] | None:
        """Return the saved state for *project_id* without modifying it."""
        return self._load_state(project_id)

    # ── Internal ─────────────────────────────────────────────

    def _pause_workflows(self, state: dict) -> int:
        """Pause all active workflows and record them in the state dict.

        Returns the number of workflows paused.
        """
        try:
            from workflow_tracker import get_active_workflows, _workflows
        except ImportError:
            return 0

        active = get_active_workflows()
        count = 0
        for tracker in active:
            state["paused_workflows"][tracker.task_id] = {
                "task_description": tracker.task_description,
                "status": tracker.status,
                "iteration": tracker.iteration,
                "max_iterations": tracker.max_iterations,
                "coder_id": tracker.coder_id,
                "tester_id": tracker.tester_id,
            }
            tracker.request_stop()
            count += 1
            logger.info(
                f"Paused workflow {tracker.task_id} "
                f"({tracker.task_description[:60]}) "
                f"at iteration {tracker.iteration}/{tracker.max_iterations}"
            )
        return count

    def _count_unfinished_tasks(self) -> int:
        """Count active tasks (workflows + background processes)."""
        count = 0
        try:
            from workflow_tracker import get_active_workflows
            count += len(get_active_workflows())
        except Exception:
            pass
        try:
            from tools.process_registry import process_registry
            count += process_registry.count_running()
        except Exception:
            pass
        return count

    def _save_state(self, project_id: str, state: dict) -> None:
        """Write project state to disk."""
        path = self._state_path(project_id)
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(".tmp")
        try:
            tmp.write_text(
                json.dumps(state, indent=2, ensure_ascii=False, default=str),
                encoding="utf-8",
            )
            tmp.replace(path)
        except OSError as e:
            logger.error(f"Failed to save project state for {project_id}: {e}")

    def _load_state(self, project_id: str) -> dict[str, Any] | None:
        """Load project state from disk, or None if not found."""
        path = self._state_path(project_id)
        if not path.exists():
            return None
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError) as e:
            logger.warning(f"Failed to load project state for {project_id}: {e}")
            return None


# ═══════════════════════════════════════════════════════════════
# Module-level utility for integration with ProjectContextMiddleware
# ═══════════════════════════════════════════════════════════════

def pause_project_and_summarize(project_id: str, hermes_home: str | Path | None = None) -> str:
    """Pause a project and return a user-readable notification string.

    Called before switching away from a project.  Returns an empty
    string if there's nothing to report.
    """
    psm = ProjectStateManager(hermes_home=hermes_home)
    summary = psm.pause_current_project(project_id)

    parts = []
    wf_count = summary.get("paused_workflow_count", 0)
    if wf_count:
        parts.append(f"{wf_count} workflow(s) paused")
    ut_count = summary.get("unfinished_tasks", 0)
    if ut_count:
        parts.append(f"{ut_count} unfinished task(s)")

    if parts:
        return " · ".join(parts)
    return ""


def restore_project_and_notify(project_id: str, hermes_home: str | Path | None = None) -> list[str]:
    """Restore a project and return user-facing notification messages."""
    psm = ProjectStateManager(hermes_home=hermes_home)
    summary = psm.restore_project(project_id)
    return summary.get("notifications", [])
