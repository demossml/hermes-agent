"""Tests for projects/project_isolation.py"""

import sys
import tempfile
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

from projects.project_isolation import (
    get_agent_project_id,
    get_active_project_filter,
    check_project_access,
    enforce_project_isolation,
    get_project_metadata_for_agent,
)


# ═══════════════════════════════════════════════════════════════
# Helpers
# ═══════════════════════════════════════════════════════════════

def _reset_singleton():
    import projects.project_context as mod
    mod.ProjectContextMiddleware._instance = None
    mod._current_project_id = None
    mod._current_project_name = None


@pytest.fixture(autouse=True)
def reset_state():
    _reset_singleton()
    yield
    _reset_singleton()


# ═══════════════════════════════════════════════════════════════
# get_agent_project_id
# ═══════════════════════════════════════════════════════════════

class TestGetAgentProjectId:
    def test_orchestrator_returns_none(self):
        """Orchestrator always returns None (full access)."""
        assert get_agent_project_id("orchestrator") is None

    def test_unknown_agent_returns_none_when_no_project(self):
        """Agent not in registry, no active project → None."""
        assert get_agent_project_id("ghost-agent") is None

    def test_returns_project_from_global(self):
        """When project is active, non-orchestrator agents get project_id."""
        import projects.project_context as pctx
        pctx._current_project_id = "test-proj"
        pctx._current_project_name = "Test Project"
        assert get_agent_project_id("coder") == "test-proj"


# ═══════════════════════════════════════════════════════════════
# get_active_project_filter
# ═══════════════════════════════════════════════════════════════

class TestGetActiveProjectFilter:
    def test_orchestrator_no_filter(self):
        assert get_active_project_filter("orchestrator") is None

    def test_empty_caller_no_filter(self):
        assert get_active_project_filter("") is None

    def test_subagent_with_project_gets_filter(self):
        import projects.project_context as pctx
        pctx._current_project_id = "my-app"
        assert get_active_project_filter("coder") == "my-app"


# ═══════════════════════════════════════════════════════════════
# check_project_access
# ═══════════════════════════════════════════════════════════════

class TestCheckProjectAccess:
    def test_orchestrator_always_allowed(self):
        assert check_project_access("orchestrator", "any-project") is True
        assert check_project_access("orchestrator", None) is True

    def test_subagent_no_project_allowed(self):
        """Sub-agent without project binding can access anything."""
        assert check_project_access("coder", None) is True
        assert check_project_access("coder", "some-project") is True

    def test_subagent_with_project_same_project_allowed(self):
        import projects.project_context as pctx
        pctx._current_project_id = "my-app"
        assert check_project_access("coder", "my-app") is True

    def test_subagent_with_project_cross_project_denied(self):
        import projects.project_context as pctx
        pctx._current_project_id = "my-app"
        assert check_project_access("coder", "other-project") is False

    def test_subagent_with_project_target_none_allowed(self):
        """Target has no project → allowed (no conflict)."""
        import projects.project_context as pctx
        pctx._current_project_id = "my-app"
        assert check_project_access("coder", None) is True


# ═══════════════════════════════════════════════════════════════
# enforce_project_isolation
# ═══════════════════════════════════════════════════════════════

class TestEnforceProjectIsolation:
    def test_allowed_returns_none(self):
        assert enforce_project_isolation("orchestrator", "any") is None

    def test_denied_returns_error_msg(self):
        import projects.project_context as pctx
        pctx._current_project_id = "my-app"
        err = enforce_project_isolation("coder", "other-project")
        assert err is not None
        assert "PROJECT ISOLATION" in err
        assert "coder" in err
        assert "my-app" in err
        assert "other-project" in err

    def test_denied_custom_operation(self):
        import projects.project_context as pctx
        pctx._current_project_id = "proj-a"
        err = enforce_project_isolation("bot-1", "proj-b", operation="read memory")
        assert err is not None
        assert "read memory" in err


# ═══════════════════════════════════════════════════════════════
# get_project_metadata_for_agent
# ═══════════════════════════════════════════════════════════════

class TestGetProjectMetadata:
    def test_orchestrator_empty(self):
        assert get_project_metadata_for_agent("orchestrator") == {}

    def test_no_project_empty(self):
        assert get_project_metadata_for_agent("coder") == {}

    def test_with_project_has_keys(self):
        import projects.project_context as pctx
        pctx._current_project_id = "my-app"
        pctx._current_project_name = "My App"
        meta = get_project_metadata_for_agent("coder")
        assert meta["project_id"] == "my-app"
        assert meta["project_name"] == "My App"


# ═══════════════════════════════════════════════════════════════
# AgentRegistry integration: project_id in agent config
# ═══════════════════════════════════════════════════════════════

class TestAgentRegistryProjectBinding:
    def test_subagent_gets_project_id_when_active(self, tmp_path):
        """When a project is active, sub-agents created via create()
        should receive project_id in their config and a project-prefixed
        subtree_session_id."""
        import projects.project_context as pctx

        pctx._current_project_id = "test-project"
        pctx._current_project_name = "Test Project"

        from agent_registry import AgentRegistry
        
        reg = AgentRegistry(config_dir=tmp_path)
        reg._agents = {"orchestrator": {
            "agent_id": "orchestrator",
            "level": 0,
            "subtree_session_id": "main-session",
            "provider": "openrouter",
            "model": "some-model",
        }}
        
        config = {
            "system_prompt": "Test agent",
            "parent_id": "orchestrator",
        }
        
        created = reg.create("testbot", config, caller_id="orchestrator")
        
        # Project binding
        assert created.get("project_id") == "test-project"
        assert "project-test-project/" in created.get("subtree_session_id", "")
        
        # Clean up yaml
        yaml_path = tmp_path / "testbot.yaml"
        if yaml_path.exists():
            yaml_path.unlink()
