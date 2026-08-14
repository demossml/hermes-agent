"""
Tests: secretary skill policy (L7) — ready-skills → toolsets/guidance.

Run: venv/bin/python -m pytest tests/test_secretary_skill_policy.py -q
"""

from __future__ import annotations

import tempfile
from pathlib import Path
from unittest.mock import patch

import pytest

from gateway.secretary_skills_store import set_skill


@pytest.fixture
def profile_home():
    with tempfile.TemporaryDirectory() as tmp:
        yield Path(tmp)


def _ready(profile_home):
    from gateway.secretary_skill_policy import get_ready_skill_ids
    return get_ready_skill_ids(profile_home)


class TestReadySkillIds:
    def test_defaults_ready(self, profile_home):
        # groups and tasks are enabled+ready by default
        assert {"groups", "tasks"} <= _ready(profile_home)

    def test_vision_ready_when_set(self, profile_home):
        set_skill(profile_home, "vision", True, "ready")
        assert "vision" in _ready(profile_home)

    def test_needs_setup_not_ready(self, profile_home):
        set_skill(profile_home, "mail", True, "needs_setup")
        assert "mail" not in _ready(profile_home)

    def test_off_not_ready(self, profile_home):
        set_skill(profile_home, "groups", False, "off")
        assert "groups" not in _ready(profile_home)

    def test_error_not_ready(self, profile_home):
        set_skill(profile_home, "mail", True, "error")
        assert "mail" not in _ready(profile_home)


class TestExtraToolsets:
    def _extra(self, user_id):
        from gateway.secretary_skill_policy import extra_toolsets_for
        return extra_toolsets_for(user_id)

    def test_no_user(self):
        assert self._extra(None) == frozenset()
        assert self._extra("") == frozenset()

    def test_maps_vision_and_tgcli(self, profile_home):
        set_skill(profile_home, "vision", True, "ready")
        set_skill(profile_home, "tgcli", True, "ready")
        with patch("gateway.secretary_router.get_active_profile_path", return_value=profile_home):
            assert self._extra("123") == frozenset({"vision", "terminal"})

    def test_ignores_non_optional(self, profile_home):
        set_skill(profile_home, "mail", True, "ready")  # mail → no toolset mapping
        set_skill(profile_home, "groups", True, "ready")
        with patch("gateway.secretary_router.get_active_profile_path", return_value=profile_home):
            assert self._extra("123") == frozenset()

    def test_router_error_returns_empty(self):
        with patch("gateway.secretary_router.get_active_profile_path", side_effect=RuntimeError("x")):
            assert self._extra("123") == frozenset()


class TestGuidanceLine:
    def _line(self, user_id):
        from gateway.secretary_skill_policy import skill_guidance_line
        return skill_guidance_line(user_id)

    def test_empty(self, profile_home):
        with patch("gateway.secretary_router.get_active_profile_path", return_value=profile_home):
            assert self._line("123") == ""

    def test_ready_skills_listed(self, profile_home):
        set_skill(profile_home, "vision", True, "ready")
        with patch("gateway.secretary_router.get_active_profile_path", return_value=profile_home):
            line = self._line("123")
        assert "Зрение" in line
        assert "vision_analyze" in line

    def test_no_user(self):
        assert self._line(None) == ""
