"""Tests for projects/project_context.py"""

import sys
import tempfile
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

from projects.project_context import (
    ProjectContextMiddleware,
    get_project_block,
    get_project_block_compact,
    get_project_context,
    get_current_project_id,
    get_current_project_name,
    switch_project,
    create_project,
    list_projects,
    get_current_project,
)


# ═══════════════════════════════════════════════════════════════
# Helpers
# ═══════════════════════════════════════════════════════════════

# Reset the singleton and globals between tests
def _reset_singleton():
    import projects.project_context as mod
    ProjectContextMiddleware._instance = None
    mod._current_project_id = None
    mod._current_project_name = None


@pytest.fixture(autouse=True)
def reset_state():
    _reset_singleton()
    yield
    _reset_singleton()


@pytest.fixture
def ctx():
    """Create a ProjectContextMiddleware scoped to a temp Hermes home."""
    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
        import projects.project_context as mod
        ProjectContextMiddleware._instance = None
        mod._current_project_id = None
        mod._current_project_name = None
        yield ProjectContextMiddleware.get_instance()


# ═══════════════════════════════════════════════════════════════
# Module-level globals
# ═══════════════════════════════════════════════════════════════

class TestGlobals:
    def test_none_on_fresh(self):
        _reset_singleton()
        assert get_current_project_id() is None
        assert get_current_project_name() is None

    def test_set_after_create(self, ctx):
        ctx.create_project("Test")
        assert get_current_project_id() == "test"
        assert get_current_project_name() == "Test"


# ═══════════════════════════════════════════════════════════════
# ProjectContextMiddleware
# ═══════════════════════════════════════════════════════════════

class TestMiddleware:
    def test_singleton(self, ctx):
        ctx2 = ProjectContextMiddleware.get_instance()
        assert ctx is ctx2

    def test_active_none_on_fresh(self, ctx):
        assert ctx.active_project_id is None
        assert ctx.active_project_name is None

    def test_create_project(self, ctx):
        proj = ctx.create_project("My App")
        assert proj["project_id"] == "my-app"
        assert proj["name"] == "My App"
        assert ctx.active_project_id == "my-app"
        assert ctx.active_project_name == "My App"

    def test_creates_disk_state(self, ctx):
        ctx.create_project("Disk Test")
        # Verify .current_project file was written
        current_file = ctx.manager._current_file
        assert current_file.exists()
        assert current_file.read_text().strip() == "disk-test"

    def test_list_projects(self, ctx):
        ctx.create_project("Alpha")
        ctx.create_project("Beta")
        projects = ctx.list_projects()
        assert len(projects) == 2
        assert projects[0]["_is_active"]  # Beta is active (latest created)
        assert not projects[1]["_is_active"]  # Alpha is not

    def test_switch_project(self, ctx):
        ctx.create_project("First")
        ctx.create_project("Second")
        ctx.switch_project("first")
        assert ctx.active_project_id == "first"
        ctx.switch_project("second")
        assert ctx.active_project_id == "second"

    def test_get_current_project(self, ctx):
        ctx.create_project("Current")
        current = ctx.get_current_project()
        assert current is not None
        assert current["project_id"] == "current"
        assert current["_is_active"] is True


# ═══════════════════════════════════════════════════════════════
# Project blocks
# ═══════════════════════════════════════════════════════════════

class TestProjectBlock:
    def test_empty_when_no_project(self, ctx):
        assert get_project_block() == ""

    def test_contains_key_info(self, ctx):
        ctx.create_project("My App")
        block = get_project_block()
        assert "[ACTIVE PROJECT]" in block
        assert "My App" in block
        assert "project-my-app" in block
        assert "project_my-app" in block
        assert "[/ACTIVE PROJECT]" in block

    def test_compact_when_no_project(self, ctx):
        assert get_project_block_compact() == ""

    def test_compact_contains_key_info(self, ctx):
        ctx.create_project("Compact Test")
        compact = get_project_block_compact()
        assert "Compact Test" in compact
        assert "compact-test" in compact

    def test_block_stable_when_no_project(self, ctx):
        """When create/delete cycle, block is empty after removal."""
        ctx.create_project("Temp")
        assert get_project_block() != ""
        ctx.manager.delete_project("temp")
        assert get_project_block() == ""


# ═══════════════════════════════════════════════════════════════
# Module-level convenience functions
# ═══════════════════════════════════════════════════════════════

class TestConvenienceFunctions:
    def test_get_project_context_singleton(self):
        ctx1 = get_project_context()
        ctx2 = get_project_context()
        assert ctx1 is ctx2

    def test_create_and_list(self, ctx):
        create_project("One")
        create_project("Two")
        projects = list_projects()
        assert len(projects) == 2

    def test_get_current(self, ctx):
        create_project("Solo")
        current = get_current_project()
        assert current["project_id"] == "solo"

    def test_switch(self, ctx):
        create_project("A")
        create_project("B")
        switch_project("a")
        assert get_current_project_id() == "a"
