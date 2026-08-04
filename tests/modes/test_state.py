"""Tests for modes.state — persistent mode storage."""

import json
import os
from pathlib import Path

import pytest
from modes.state import (
    get_mode, set_mode, get_mode_record,
    get_default_mode, _DEFAULT_MODE,
    set_current_turn_mode, get_current_turn_mode,
)


class TestSetGet:
    def test_set_and_get(self):
        set_mode("telegram", "chat-001", "dev")
        assert get_mode("telegram", "chat-001") == "dev"

    def test_switch_mode(self):
        set_mode("telegram", "chat-001", "secretary")
        assert get_mode("telegram", "chat-001") == "secretary"

    def test_switch_back(self):
        set_mode("telegram", "chat-001", "dev")
        assert get_mode("telegram", "chat-001") == "dev"

    def test_default_for_unknown(self):
        assert get_mode("nonexistent", "no-such-chat") == _DEFAULT_MODE

    def test_get_mode_record(self):
        set_mode("discord", "guild-999", "secretary")
        rec = get_mode_record("discord", "guild-999")
        assert rec is not None
        assert rec["mode"] == "secretary"
        assert "updated_at" in rec

    def test_get_mode_record_none(self):
        rec = get_mode_record("matrix", "room-void")
        assert rec is None

    def test_invalid_mode_raises(self):
        with pytest.raises(ValueError, match="Invalid mode"):
            set_mode("test", "bad-chat", "invalid_mode")


class TestIsolation:
    def test_two_chats_independent(self):
        set_mode("telegram", "chat-A", "dev")
        set_mode("telegram", "chat-B", "secretary")
        assert get_mode("telegram", "chat-A") == "dev"
        assert get_mode("telegram", "chat-B") == "secretary"

    def test_two_platforms_independent(self):
        set_mode("telegram", "shared-id", "dev")
        set_mode("discord", "shared-id", "secretary")
        assert get_mode("telegram", "shared-id") == "dev"
        assert get_mode("discord", "shared-id") == "secretary"

    def test_isolation_after_multiple_writes(self):
        for i in range(5):
            set_mode("test", f"chat-{i}", "dev" if i % 2 == 0 else "secretary")
        assert get_mode("test", "chat-0") == "dev"
        assert get_mode("test", "chat-1") == "secretary"
        assert get_mode("test", "chat-4") == "dev"


class TestIdempotency:
    def test_repeated_set_same_value(self):
        for _ in range(3):
            set_mode("test", "idem-chat", "dev")
        assert get_mode("test", "idem-chat") == "dev"

    def test_modes_json_survives_rewrites(self):
        set_mode("test", "survive", "secretary")
        first = get_mode("test", "survive")
        set_mode("test", "survive", "dev")
        set_mode("test", "survive", "secretary")
        assert get_mode("test", "survive") == first

    def test_clean_state_after_remove(self):
        """Setting a mode, then another chat should not affect first."""
        set_mode("test", "keep", "secretary")
        set_mode("test", "other", "dev")
        assert get_mode("test", "keep") == "secretary"


class TestDefaults:
    def test_default_mode_is_dev(self):
        assert _DEFAULT_MODE == "dev"
        assert get_default_mode() == "dev"

    def test_unknown_platform_chat_returns_dev(self):
        assert get_mode("", "") == "dev"
        assert get_mode("unknown", "") == "dev"


class TestTurnMode:
    def test_set_and_get(self):
        set_current_turn_mode("secretary")
        assert get_current_turn_mode() == "secretary"

    def test_consume_once(self):
        set_current_turn_mode("dev")
        get_current_turn_mode()  # consume
        assert get_current_turn_mode() is None

    def test_invalid_mode_resets(self):
        set_current_turn_mode("invalid")
        assert get_current_turn_mode() is None

    def test_none_passthrough(self):
        set_current_turn_mode(None)
        assert get_current_turn_mode() is None
