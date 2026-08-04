"""Tests for modes.policy — toolset allowlists and individual tool filtering."""

import pytest
from modes.policy import (
    MODE_TOOLSETS,
    filter_tools,
    filter_individual_tools,
    _get_blocked_tools,
    _is_archive_query_allowed_in_dev,
    _is_terminal_allowed_in_secretary,
)


class TestToolsetAllowlists:
    def test_dev_has_terminal(self):
        assert "terminal" in MODE_TOOLSETS["dev"]

    def test_dev_has_delegation(self):
        assert "delegation" in MODE_TOOLSETS["dev"]

    def test_dev_has_file(self):
        assert "file" in MODE_TOOLSETS["dev"]

    def test_secretary_no_terminal(self):
        assert "terminal" not in MODE_TOOLSETS["secretary"]

    def test_secretary_no_delegation(self):
        assert "delegation" not in MODE_TOOLSETS["secretary"]

    def test_secretary_no_file(self):
        assert "file" not in MODE_TOOLSETS["secretary"]

    def test_secretary_no_coding(self):
        assert "coding" not in MODE_TOOLSETS["secretary"]

    def test_secretary_has_minimal_tools(self):
        sec = MODE_TOOLSETS["secretary"]
        for toolset in ["todo", "memory", "session_search", "skills", "cronjob"]:
            assert toolset in sec, f"secretary must have {toolset}"


class TestFilterTools:
    def test_dev_keeps_terminal(self):
        names = frozenset(["terminal", "file", "delegation", "todo"])
        result = filter_tools("dev", names)
        assert "terminal" in result
        assert "delegation" in result

    def test_secretary_removes_terminal(self):
        names = frozenset(["terminal", "todo", "memory", "cronjob"])
        result = filter_tools("secretary", names)
        assert "terminal" not in result
        assert "todo" in result
        assert "cronjob" in result

    def test_none_mode_passthrough(self):
        names = frozenset(["terminal", "todo"])
        assert filter_tools(None, names) == names

    def test_unknown_mode_passthrough(self):
        names = frozenset(["terminal", "todo"])
        assert filter_tools("unknown_mode", names) == names

    def test_intersection_with_available(self):
        """Only toolsets enabled in config + mode allowlist are returned."""
        available = frozenset(["terminal", "todo", "memory"])
        result = filter_tools("secretary", available)
        assert result == frozenset(["todo", "memory"])


class TestBlockedTools:
    def test_dev_blocks_archive_query(self):
        blocked = _get_blocked_tools("dev")
        assert "archive_query" in blocked

    def test_secretary_blocks_terminal(self):
        blocked = _get_blocked_tools("secretary")
        assert "terminal" in blocked

    def test_none_mode_no_blocks(self):
        assert _get_blocked_tools(None) == frozenset()
        assert _get_blocked_tools("unknown") == frozenset()


class TestFilterIndividualTools:
    def test_dev_removes_archive_query(self):
        names = frozenset(["terminal", "archive_query", "todo"])
        result = filter_individual_tools("dev", names)
        assert "archive_query" not in result
        assert "terminal" in result

    def test_secretary_removes_terminal(self):
        names = frozenset(["terminal", "archive_query", "todo"])
        result = filter_individual_tools("secretary", names)
        assert "terminal" not in result
        assert "archive_query" in result

    def test_idempotent(self):
        names = frozenset(["terminal", "archive_query", "todo"])
        f1 = filter_individual_tools("dev", names)
        f2 = filter_individual_tools("dev", f1)
        assert f1 == f2

    def test_none_mode_passthrough(self):
        names = frozenset(["terminal", "archive_query"])
        assert filter_individual_tools(None, names) == names


class TestConfigGates:
    def test_archive_query_default_false(self):
        assert not _is_archive_query_allowed_in_dev()

    def test_terminal_in_secretary_default_false(self):
        assert not _is_terminal_allowed_in_secretary()
