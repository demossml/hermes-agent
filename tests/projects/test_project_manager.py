"""Tests for projects/project_manager.py"""

import json
import os
import sys
import tempfile
from pathlib import Path

import pytest

# Ensure the project root is on sys.path so we can import project_manager
sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

from projects.project_manager import (
    ProjectManager,
    _slugify,
    _ensure_unique_id,
    _new_metadata,
)


# ═══════════════════════════════════════════════════════════════
# Fixtures
# ═══════════════════════════════════════════════════════════════

@pytest.fixture
def pm():
    """Create a ProjectManager scoped to a temp Hermes home."""
    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
        yield ProjectManager(hermes_home=tmp)


@pytest.fixture
def pm_with_project(pm):
    """ProjectManager with a pre-created project."""
    pm.create_project("Test Project")
    return pm


# ═══════════════════════════════════════════════════════════════
# _slugify
# ═══════════════════════════════════════════════════════════════

class TestSlugify:
    def test_simple_english(self):
        assert _slugify("My Web App") == "my-web-app"

    def test_camel_case(self):
        assert _slugify("HelloWorld") == "helloworld"

    def test_spaces_and_punctuation(self):
        assert _slugify("Hello, World!") == "hello-world"

    def test_multiple_spaces(self):
        assert _slugify("a   b\t\tc") == "a-b-c"

    def test_cyrillic(self):
        assert _slugify("Мой проект") == "moy-proekt"

    def test_cyrillic_complex(self):
        assert _slugify("Привет мир") == "privet-mir"

    def test_leading_trailing_special(self):
        assert _slugify("!!!Hello!!!") == "hello"

    def test_numbers(self):
        assert _slugify("Project 2024 v2") == "project-2024-v2"

    def test_empty_string(self):
        assert _slugify("") == "untitled-project"

    def test_only_special_chars(self):
        assert _slugify("!!!") == "untitled-project"

    def test_consecutive_hyphens_collapsed(self):
        assert _slugify("a---b") == "a-b"


# ═══════════════════════════════════════════════════════════════
# _ensure_unique_id
# ═══════════════════════════════════════════════════════════════

class TestEnsureUniqueId:
    def test_no_conflict(self):
        assert _ensure_unique_id("my-project", {"other"}) == "my-project"

    def test_conflict_appends_suffix(self):
        assert _ensure_unique_id("my-project", {"my-project"}) == "my-project-2"

    def test_multiple_conflicts(self):
        existing = {"my-project", "my-project-2", "my-project-3"}
        assert _ensure_unique_id("my-project", existing) == "my-project-4"

    def test_empty_existing(self):
        assert _ensure_unique_id("abc", set()) == "abc"


# ═══════════════════════════════════════════════════════════════
# _new_project_meta
# ═══════════════════════════════════════════════════════════════

class TestNewProjectMeta:
    def test_keys(self):
        meta = _new_metadata("my-app", "My App")
        assert meta["project_id"] == "my-app"
        assert meta["name"] == "My App"
        assert meta["subtree_session_id"] == "project-my-app"
        assert meta["chroma_collection"] == "project_my-app"
        assert "created_at" in meta
        assert "updated_at" in meta


# ═══════════════════════════════════════════════════════════════
# ProjectManager — create_project
# ═══════════════════════════════════════════════════════════════

class TestCreateProject:
    def test_creates_project(self, pm):
        result = pm.create_project("My App")
        assert result["project_id"] == "my-app"
        assert result["name"] == "My App"
        assert result["subtree_session_id"] == "project-my-app"
        assert result["chroma_collection"] == "project_my-app"
        assert "created_at" in result

    def test_creates_project_dir(self, pm):
        result = pm.create_project("My App")
        proj_dir = pm.projects_dir / "my-app"
        assert proj_dir.is_dir()

    def test_saves_metadata(self, pm):
        pm.create_project("My App")
        meta_file = pm._project_dir("my-app") / "metadata.json"
        assert meta_file.exists()
        data = json.loads(meta_file.read_text())
        assert data["project_id"] == "my-app"
        assert data["name"] == "My App"

    def test_auto_switches_to_new_project(self, pm):
        result = pm.create_project("Fresh")
        current = pm.get_current_project()
        assert current is not None
        assert current["project_id"] == "fresh"

    def test_cyrillic_name(self, pm):
        result = pm.create_project("Мой проект")
        assert result["project_id"] == "moy-proekt"
        assert result["name"] == "Мой проект"

    def test_duplicate_name_generates_unique_id(self, pm):
        r1 = pm.create_project("My App")
        r2 = pm.create_project("My App")
        assert r1["project_id"] == "my-app"
        assert r2["project_id"] == "my-app-2"
        assert r1["project_id"] != r2["project_id"]

    def test_empty_name_raises(self, pm):
        with pytest.raises(ValueError, match="must not be empty"):
            pm.create_project("")
        with pytest.raises(ValueError, match="must not be empty"):
            pm.create_project("   ")


# ═══════════════════════════════════════════════════════════════
# ProjectManager — get_project
# ═══════════════════════════════════════════════════════════════

class TestGetProject:
    def test_existing(self, pm_with_project):
        proj = pm_with_project.get_project("test-project")
        assert proj is not None
        assert proj["project_id"] == "test-project"
        assert proj["name"] == "Test Project"

    def test_nonexistent_returns_none(self, pm):
        assert pm.get_project("does-not-exist") is None


# ═══════════════════════════════════════════════════════════════
# ProjectManager — list_projects
# ═══════════════════════════════════════════════════════════════

class TestListProjects:
    def test_empty(self, pm):
        assert pm.list_projects() == []

    def test_single_project(self, pm_with_project):
        projects = pm_with_project.list_projects()
        assert len(projects) == 1
        assert projects[0]["name"] == "Test Project"

    def test_multiple_projects_sorted_by_date(self, pm):
        pm.create_project("Alpha")
        pm.create_project("Beta")
        pm.create_project("Gamma")
        projects = pm.list_projects()
        assert len(projects) == 3
        # Newest first (Gamma → Beta → Alpha)
        assert [p["name"] for p in projects] == ["Gamma", "Beta", "Alpha"]

    def test_returns_copy_not_reference(self, pm):
        pm.create_project("X")
        projects = pm.list_projects()
        projects[0]["name"] = "Modified"
        # Re-read from disk — should still be original
        reloaded = pm.list_projects()
        assert reloaded[0]["name"] == "X"


# ═══════════════════════════════════════════════════════════════
# ProjectManager — get_current_project / switch_project
# ═══════════════════════════════════════════════════════════════

class TestCurrentProject:
    def test_none_when_no_projects(self, pm):
        assert pm.get_current_project() is None

    def test_returns_after_create(self, pm_with_project):
        current = pm_with_project.get_current_project()
        assert current is not None
        assert current["project_id"] == "test-project"

    def test_switch_project(self, pm):
        pm.create_project("First")
        pm.create_project("Second")
        result = pm.switch_project("first")
        assert result["project_id"] == "first"
        current = pm.get_current_project()
        assert current["project_id"] == "first"

        result2 = pm.switch_project("second")
        assert result2["project_id"] == "second"
        current2 = pm.get_current_project()
        assert current2["project_id"] == "second"

    def test_switch_nonexistent_raises(self, pm):
        with pytest.raises(ValueError, match="not found"):
            pm.switch_project("ghost")

    def test_switch_updates_timestamp(self, pm):
        pm.create_project("One")
        pm.create_project("Two")
        before = pm.get_project("one")["updated_at"]
        import time
        time.sleep(0.01)  # ensure timestamp changes
        pm.switch_project("one")
        after = pm.get_project("one")["updated_at"]
        assert after > before


# ═══════════════════════════════════════════════════════════════
# ProjectManager — delete_project
# ═══════════════════════════════════════════════════════════════

class TestDeleteProject:
    def test_delete_existing(self, pm):
        pm.create_project("Temp")
        assert pm.get_project("temp") is not None
        result = pm.delete_project("temp")
        assert result is True
        assert pm.get_project("temp") is None

    def test_delete_nonexistent(self, pm):
        assert pm.delete_project("ghost") is False

    def test_delete_current_clears_marker(self, pm):
        pm.create_project("Current")
        assert pm.get_current_project() is not None
        pm.delete_project("current")
        assert pm.get_current_project() is None

    def test_delete_preserves_directory(self, pm):
        pm.create_project("Keep Dir")
        proj_dir = pm.projects_dir / "keep-dir"
        assert proj_dir.is_dir()
        pm.delete_project("keep-dir")
        # Directory still exists (metadata-only delete)
        assert proj_dir.is_dir()


# ═══════════════════════════════════════════════════════════════
# ProjectManager — rename_project
# ═══════════════════════════════════════════════════════════════

class TestRenameProject:
    def test_rename_updates_name(self, pm):
        pm.create_project("Old Name")
        result = pm.rename_project("old-name", "New Name")
        assert result["name"] == "New Name"
        assert result["project_id"] == "old-name"  # slug unchanged

    def test_rename_persisted(self, pm):
        pm.create_project("X")
        pm.rename_project("x", "Y")
        proj = pm.get_project("x")
        assert proj["name"] == "Y"

    def test_rename_nonexistent_raises(self, pm):
        with pytest.raises(ValueError, match="not found"):
            pm.rename_project("ghost", "Whatever")

    def test_rename_empty_name_raises(self, pm):
        pm.create_project("Valid")
        with pytest.raises(ValueError, match="must not be empty"):
            pm.rename_project("valid", "")
