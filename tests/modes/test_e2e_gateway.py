"""E2E: Gateway + Telegram flow — 7 acceptance criteria."""

import json

import pytest
from modes.state import set_mode, get_mode, set_current_turn_mode
from modes.router import get_effective_mode
from modes.detect import detect_mode_intent
from modes.router import apply_mode_change
from modes.policy import filter_individual_tools
from modes.prompts import DEV_GUIDANCE


CHAT = ("telegram", "-100_e2e_acceptance")


@pytest.fixture(autouse=True)
def clean_chat():
    """Reset test chat to dev before and after each test."""
    set_mode(*CHAT, "dev")
    set_current_turn_mode(None)
    yield
    set_mode(*CHAT, "dev")
    set_current_turn_mode(None)


class TestE2EGatewayTelegram:
    """Full gateway message path simulation."""

    def test_step1_initial_mode_is_dev(self):
        """Gateway starts, unknown chat → dev."""
        assert get_effective_mode(*CHAT) == "dev"

    def test_step2_secretary_switch_confirmation(self):
        """User: «режим секретаря» → detect + switch + confirmation."""
        intent = detect_mode_intent("режим секретаря")
        assert intent is not None
        assert intent.mode == "secretary"

        reply = apply_mode_change(*CHAT, "secretary", user_text="режим секретаря")
        assert "секретарь" in reply or "Secretary" in reply

        assert get_effective_mode(*CHAT) == "secretary"

    def test_step3_archive_admin_in_secretary(self):
        """User: «какие группы в архиве» → archive_admin list_chats."""
        set_mode(*CHAT, "secretary")
        set_current_turn_mode("secretary")

        from tools.archive_admin import check_archive_admin_requirements as check
        assert check() is True, "archive_admin must be available in secretary"

        from tools.archive_admin import archive_admin_tool
        result = json.loads(archive_admin_tool("list_chats"))
        assert result["action"] == "list_chats"
        # Should show config state (may be empty allowlist)
        assert "enabled" in result
        assert "chats" in result or "mode" in result

    def test_step4_archive_query_available_secretary(self):
        """User: «отчёт за сегодня» → archive_query IS in secretary tools."""
        tools = frozenset(["terminal", "archive_query", "todo", "memory"])
        sec_tools = filter_individual_tools("secretary", tools)
        assert "archive_query" in sec_tools
        assert "terminal" not in sec_tools

    def test_step5_switch_to_dev_with_project_hint(self):
        """User: «режим разработки» → switch + project hint."""
        set_mode(*CHAT, "secretary")
        reply = apply_mode_change(*CHAT, "dev", user_text="режим разработки")
        assert "разработка" in reply or "dev" in reply.lower()
        assert get_effective_mode(*CHAT) == "dev"
        # Dev reply should mention project
        assert "проект" in reply.lower() or "project" in reply.lower()

    def test_step6_dev_cannot_use_archive_tools(self):
        """In dev, agent does NOT have archive_query (policy A)."""
        tools = frozenset(["terminal", "archive_query", "todo", "delegate_task"])
        dev_tools = filter_individual_tools("dev", tools)
        assert "archive_query" not in dev_tools,             "archive_query must be blocked in dev (policy A)"

        # DEV_GUIDANCE must explicitly forbid archive usage
        assert "archive_query" in DEV_GUIDANCE
        assert ("недоступен" in DEV_GUIDANCE.lower()
                or "not available" in DEV_GUIDANCE.lower())

    def test_step7_mode_survives_restart(self):
        """Gateway restart → mode persists in modes.json."""
        set_mode(*CHAT, "secretary")

        # Simulate restart: read fresh from disk
        from modes.state import _hermes_home
        from pathlib import Path
        modes_file = Path(str(_hermes_home())) / "state" / "modes.json"
        assert modes_file.exists(), "modes.json must exist"

        with open(modes_file) as f:
            data = json.load(f)

        key = f"{CHAT[0]}::{CHAT[1]}"
        assert key in data, f"Key {key} not found in modes.json"
        assert data[key]["mode"] == "secretary"

        # After restart, get_mode reads from file
        mode = get_effective_mode(*CHAT)
        assert mode == "secretary", f"Mode should survive restart, got {mode}"


class TestPolicyAEnforcement:
    """Policy A: dev agent must NOT use archive tools unsolicited."""

    def test_dev_blocked_individual_tools(self):
        blocked = filter_individual_tools(
            "dev",
            frozenset(["archive_query", "terminal", "delegate_task"]),
        )
        assert "archive_query" not in blocked

    def test_secretary_blocked_individual_tools(self):
        blocked = filter_individual_tools(
            "secretary",
            frozenset(["terminal", "archive_query", "todo"]),
        )
        assert "terminal" not in blocked
        assert "archive_query" in blocked

    def test_guidance_blocks_archive_initiative(self):
        """DEV_GUIDANCE text explicitly prevents unsolicited archive use."""
        triggers = ["archive_query", "недоступен", "не лезь"]
        for t in triggers:
            assert t in DEV_GUIDANCE.lower(),                 f"DEV_GUIDANCE must contain '{t}'"
