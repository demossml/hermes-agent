"""Tests for modes.router — end-to-end mode switch flow."""

import pytest
from modes.router import (
    apply_mode_change,
    get_effective_mode,
    format_status_reply,
    format_already_reply,
)
from modes.state import get_mode, set_mode


class TestApplyModeChange:
    def test_switch_to_secretary(self):
        reply = apply_mode_change(
            "telegram", "flow-test-1", "secretary",
            user_text="режим секретаря",
        )
        assert "секретарь" in reply or "Secretary" in reply
        assert get_mode("telegram", "flow-test-1") == "secretary"

    def test_switch_to_dev(self):
        reply = apply_mode_change(
            "telegram", "flow-test-1", "dev",
            user_text="режим разработки",
        )
        assert "разработка" in reply or "dev" in reply.lower()
        assert get_mode("telegram", "flow-test-1") == "dev"

    def test_switch_back_to_secretary(self):
        set_mode("telegram", "flow-test-1", "dev")
        reply = apply_mode_change(
            "telegram", "flow-test-1", "secretary",
            user_text="режим секретаря",
        )
        assert "Архив" in reply or "Secretary" in reply
        assert get_mode("telegram", "flow-test-1") == "secretary"

    def test_secretary_warns_archive_disabled(self):
        reply = apply_mode_change(
            "telegram", "archive-off-test", "secretary",
            user_text="режим секретаря",
        )
        # Archive is disabled by default — should warn
        assert "Архив выключен" in reply or "disabled" in reply.lower()

    def test_en_response(self):
        reply = apply_mode_change(
            "discord", "en-test", "secretary",
            user_text="switch to secretary mode",
        )
        assert "secretary" in reply.lower()

    def test_idempotent_apply(self):
        set_mode("test", "idem-r", "dev")
        r1 = apply_mode_change("test", "idem-r", "dev")
        r2 = apply_mode_change("test", "idem-r", "dev")
        assert get_mode("test", "idem-r") == "dev"


class TestGetEffectiveMode:
    def test_resolves_from_state(self):
        set_mode("telegram", "eff-test", "secretary")
        assert get_effective_mode("telegram", "eff-test") == "secretary"

    def test_default_is_dev(self):
        assert get_effective_mode("unknown", "no-record") == "dev"


class TestFormatStatusReply:
    def test_status_ru(self):
        set_mode("telegram", "status-test", "dev")
        reply = format_status_reply(
            "telegram", "status-test",
            user_text="какой режим",
        )
        assert "разработка" in reply or "dev" in reply
        assert "Архив" in reply or "Archive" in reply
        assert "Проект" in reply or "Project" in reply

    def test_status_en(self):
        set_mode("discord", "status-en", "secretary")
        reply = format_status_reply(
            "discord", "status-en",
            user_text="current mode",
        )
        assert "secretary" in reply.lower()
        assert "archive" in reply.lower()


class TestFormatAlreadyReply:
    def test_already_ru(self):
        reply = format_already_reply("dev", "перейди в режим разработки")
        assert "Уже в режиме" in reply

    def test_already_en(self):
        reply = format_already_reply("secretary", "switch to secretary mode")
        assert "Already in mode" in reply


class TestEndToEndFlow:
    """Simulate a full inbound message flow: NL → state change → confirmation."""

    def test_full_cycle(self):
        chat = "e2e-full-cycle"

        # 1. Start: unknown → dev
        assert get_effective_mode("telegram", chat) == "dev"

        # 2. User says "режим секретаря" → switch
        reply = apply_mode_change("telegram", chat, "secretary",
                                  user_text="режим секретаря")
        assert get_effective_mode("telegram", chat) == "secretary"
        assert len(reply) > 10  # meaningful reply

        # 3. Status check
        status = format_status_reply("telegram", chat, user_text="какой режим")
        assert "секретарь" in status or "secretary" in status

        # 4. Switch back
        reply = apply_mode_change("telegram", chat, "dev",
                                  user_text="режим разработки")
        assert get_effective_mode("telegram", chat) == "dev"

        # 5. Already in dev → short confirmation
        already = format_already_reply("dev", "перейди в режим разработки")
        assert len(already) < 80

    def test_two_chats_independent_flow(self):
        """Chat A and chat B have independent modes."""
        apply_mode_change("telegram", "chat-a", "secretary",
                          user_text="режим секретаря")
        apply_mode_change("telegram", "chat-b", "dev",
                          user_text="режим разработки")

        assert get_effective_mode("telegram", "chat-a") == "secretary"
        assert get_effective_mode("telegram", "chat-b") == "dev"

    def test_cross_platform_isolation(self):
        apply_mode_change("telegram", "same-id", "secretary",
                          user_text="режим секретаря")
        apply_mode_change("discord", "same-id", "dev",
                          user_text="switch to dev mode")

        assert get_effective_mode("telegram", "same-id") == "secretary"
        assert get_effective_mode("discord", "same-id") == "dev"
