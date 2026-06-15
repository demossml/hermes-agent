"""Tests for projects/project_state.py"""

import json
import sys
import tempfile
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

from projects.project_state import (
    ProjectStateManager,
    pause_project_and_summarize,
    restore_project_and_notify,
)


@pytest.fixture
def psm():
    """ProjectStateManager scoped to a temp Hermes home."""
    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
        yield ProjectStateManager(hermes_home=tmp)


@pytest.fixture
def psm_with_state(psm):
    """PSM with pre-saved state for 'test-proj'."""
    state = {
        "project_id": "test-proj",
        "paused_at": "2026-06-15T12:00:00Z",
        "paused_workflows": {
            "task-abc": {
                "task_description": "Fix auth bug",
                "status": "writing",
                "iteration": 2,
                "max_iterations": 4,
            },
            "task-def": {
                "task_description": "Add tests",
                "status": "reviewing",
                "iteration": 1,
                "max_iterations": 3,
            },
        },
        "paused_workflow_count": 2,
        "unfinished_tasks": 3,
    }
    psm._save_state("test-proj", state)
    return psm


# ═══════════════════════════════════════════════════════════════
# ProjectStateManager — save/load
# ═══════════════════════════════════════════════════════════════

class TestSaveLoadState:
    def test_save_and_load(self, psm):
        state = {"project_id": "my-app", "paused_workflows": {}, "unfinished_tasks": 0}
        psm._save_state("my-app", state)
        loaded = psm._load_state("my-app")
        assert loaded is not None
        assert loaded["project_id"] == "my-app"

    def test_load_nonexistent(self, psm):
        assert psm._load_state("ghost") is None

    def test_state_path(self, psm):
        path = psm._state_path("my-app")
        assert "projects" in str(path)
        assert path.name == "snapshot.json"
        assert "my-app" in str(path)


class TestRestoreProject:
    def test_no_saved_state(self, psm):
        summary = psm.restore_project("no-state")
        assert summary["has_saved_state"] is False
        assert summary["notifications"] == []

    def test_has_saved_state_with_notifications(self, psm_with_state):
        summary = psm_with_state.restore_project("test-proj")
        assert summary["has_saved_state"] is True
        assert len(summary["notifications"]) >= 1
        assert any("workflow" in n.lower() for n in summary["notifications"])
        assert any("unfinished" in n.lower() for n in summary["notifications"])


class TestPauseProject:
    def test_pause_creates_state_file(self, psm):
        # Create project dir first
        proj_dir = psm._projects_dir / "pausable"
        proj_dir.mkdir(parents=True, exist_ok=True)
        summary = psm.pause_current_project("pausable")
        assert "paused_workflow_count" in summary
        assert psm._state_path("pausable").exists()

    def test_pause_preserves_metadata(self, psm):
        proj_dir = psm._projects_dir / "meta-test"
        proj_dir.mkdir(parents=True, exist_ok=True)
        psm.pause_current_project("meta-test")
        state = psm._load_state("meta-test")
        assert state["project_id"] == "meta-test"
        assert "paused_at" in state


class TestGetStateSummary:
    def test_none_for_nonexistent(self, psm):
        assert psm.get_project_state_summary("ghost") is None

    def test_returns_data(self, psm_with_state):
        state = psm_with_state.get_project_state_summary("test-proj")
        assert state is not None
        assert state["paused_workflow_count"] == 2
        assert state["unfinished_tasks"] == 3


# ═══════════════════════════════════════════════════════════════
# Module-level helpers
# ═══════════════════════════════════════════════════════════════

class TestModuleHelpers:
    def test_pause_and_summarize_empty(self, psm):
        proj_dir = psm._projects_dir / "empty-proj"
        proj_dir.mkdir(parents=True, exist_ok=True)
        msg = pause_project_and_summarize("empty-proj")
        # No active workflows in test → empty message
        assert msg == ""

    def test_restore_and_notify_no_state(self, psm):
        msgs = restore_project_and_notify("ghost")
        assert msgs == []

    def test_restore_and_notify_with_state(self, psm_with_state):
        msgs = restore_project_and_notify(
            "test-proj", hermes_home=psm_with_state._home,
        )
        assert len(msgs) >= 1


# ═══════════════════════════════════════════════════════════════
# Integration: switch_project saves state
# ═══════════════════════════════════════════════════════════════

class TestSwitchSavesState:
    def test_switch_saves_previous_project_state(self, psm):
        """When switching projects, the previous project's state is saved."""
        import projects.project_context as pctx
        from unittest.mock import patch

        pctx.ProjectContextMiddleware._instance = None
        pctx._current_project_id = None
        pctx._current_project_name = None

        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
            with patch('projects.project_manager._get_hermes_home', return_value=Path(tmp)):
                mw = pctx.ProjectContextMiddleware()
                mw.create_project("Project A")
                mw.create_project("Project B")

                # Switch back to A — should save B's state
                result = mw.switch_project("project-a")

                # Verify state.json was created for "project-b"
                state_path = Path(tmp) / "projects" / "project-b" / "state" / "workflows" / "snapshot.json"
                assert state_path.exists(), f"Expected snapshot.json at {state_path}"

                # Check it has proper structure
                state = json.loads(state_path.read_text())
                assert state["project_id"] == "project-b"
                assert "paused_at" in state
