"""
Tests: secretary skill tree (L2) — registry, store, skills screen, callbacks.

Run: venv/bin/python -m pytest tests/test_secretary_skills.py -q
"""

from __future__ import annotations

import tempfile
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

import gateway.platforms.telegram as _tg_mod


# ── Fake inline-keyboard types (python-telegram-bot may be absent) ──


class _FakeInlineKeyboardButton:
    def __init__(self, text, callback_data=None, **kwargs):
        self.text = text
        self.callback_data = callback_data


class _FakeInlineKeyboardMarkup:
    def __init__(self, inline_keyboard):
        self.inline_keyboard = inline_keyboard


@pytest.fixture(autouse=True)
def _patch_inline_keyboard(monkeypatch):
    monkeypatch.setattr(_tg_mod, "InlineKeyboardButton", _FakeInlineKeyboardButton)
    monkeypatch.setattr(_tg_mod, "InlineKeyboardMarkup", _FakeInlineKeyboardMarkup)


def _make_adapter():
    from gateway.platforms.telegram import TelegramAdapter

    adapter = TelegramAdapter.__new__(TelegramAdapter)
    adapter._disable_link_previews = False
    adapter._reply_to_mode = "off"
    adapter._bot = MagicMock()
    adapter._bot.send_message = AsyncMock()
    adapter._is_callback_user_authorized = MagicMock(return_value=True)
    return adapter


def _make_query(chat_type="private"):
    query = SimpleNamespace()
    query.from_user = SimpleNamespace(id="123", first_name="Test")
    query.message = SimpleNamespace(chat=SimpleNamespace(type=chat_type), chat_id="456", message_thread_id=None)
    query.answer = AsyncMock()
    query.edit_message_text = AsyncMock()
    return query


# ── Registry ───────────────────────────────────────────────────


class TestRegistry:
    def test_skill_ids(self):
        from gateway.secretary_skills_registry import SKILLS
        assert [s.id for s in SKILLS] == ["mail", "calendar", "groups", "tasks", "vision", "tgcli"]

    def test_get_skill(self):
        from gateway.secretary_skills_registry import get_skill
        assert get_skill("mail").title == "Почта"
        assert get_skill("nope") is None
        assert get_skill("") is None

    def test_default_state(self):
        from gateway.secretary_skills_registry import default_state
        state = default_state()
        assert state["mail"] == {"enabled": True, "status": "needs_setup"}
        assert state["groups"] == {"enabled": True, "status": "ready"}
        assert state["vision"] == {"enabled": False, "status": "off"}

    def test_apply_toggle_requires_setup(self):
        from gateway.secretary_skills_registry import apply_toggle
        assert apply_toggle("mail", True) == {"enabled": True, "status": "needs_setup"}

    def test_apply_toggle_no_setup(self):
        from gateway.secretary_skills_registry import apply_toggle
        assert apply_toggle("groups", True) == {"enabled": True, "status": "ready"}
        assert apply_toggle("tasks", True) == {"enabled": True, "status": "ready"}

    def test_apply_toggle_off(self):
        from gateway.secretary_skills_registry import apply_toggle
        assert apply_toggle("groups", False) == {"enabled": False, "status": "off"}
        assert apply_toggle("mail", False) == {"enabled": False, "status": "off"}

    def test_apply_toggle_unknown(self):
        from gateway.secretary_skills_registry import apply_toggle
        assert apply_toggle("nope", True) is None


# ── Store ──────────────────────────────────────────────────────


class TestStore:
    def test_load_state_defaults(self):
        from gateway.secretary_skills_store import load_state
        with tempfile.TemporaryDirectory() as tmp:
            state = load_state(Path(tmp))
            assert set(state) == {"mail", "calendar", "groups", "tasks", "vision", "tgcli"}
            assert state["groups"]["status"] == "ready"

    def test_set_and_roundtrip(self):
        from gateway.secretary_skills_store import load_state, set_skill
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp)
            set_skill(home, "vision", True, "ready")
            set_skill(home, "mail", False, "off")
            state = load_state(home)
            assert state["vision"] == {"enabled": True, "status": "ready"}
            assert state["mail"] == {"enabled": False, "status": "off"}
            # others untouched
            assert state["groups"] == {"enabled": True, "status": "ready"}

    def test_persistence_across_loads(self):
        from gateway.secretary_skills_store import load_state, set_skill
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp)
            set_skill(home, "tgcli", True, "needs_setup")
            # new load object — must read from disk
            state = load_state(home)
            assert state["tgcli"] == {"enabled": True, "status": "needs_setup"}

    def test_corrupt_file_falls_back(self):
        from gateway.secretary_skills_store import load_state, state_path
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp)
            state_path(home).write_text("{not valid json", encoding="utf-8")
            state = load_state(home)
            assert state["groups"]["status"] == "ready"

    def test_unknown_ids_dropped_and_missing_defaulted(self):
        from gateway.secretary_skills_store import load_state, state_path
        import json
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp)
            state_path(home).write_text(json.dumps({
                "version": 1,
                "skills": {"mail": {"enabled": False, "status": "off"}, "bogus": {"enabled": True, "status": "ready"}},
            }), encoding="utf-8")
            state = load_state(home)
            assert "bogus" not in state
            assert state["mail"] == {"enabled": False, "status": "off"}
            assert state["groups"] == {"enabled": True, "status": "ready"}


# ── Skills screen (_skill_view) ────────────────────────────────


def _button_rows(markup):
    return [[(b.text, b.callback_data) for b in row] for row in markup.inline_keyboard]


class TestSkillView:
    def test_skill_view_buttons(self):
        adapter = _make_adapter()
        state = {
            "mail": {"enabled": True, "status": "needs_setup"},
            "calendar": {"enabled": False, "status": "off"},
            "groups": {"enabled": True, "status": "ready"},
            "tasks": {"enabled": True, "status": "ready"},
            "vision": {"enabled": False, "status": "off"},
            "tgcli": {"enabled": False, "status": "off"},
        }
        with tempfile.TemporaryDirectory() as tmp:
            with patch("gateway.secretary_router.get_active", return_value="secretary-test"), \
                 patch("gateway.secretary_router.get_active_profile_path", return_value=Path(tmp)), \
                 patch("gateway.secretary_skills_store.load_state", return_value=state):
                text, markup = adapter._skill_view("123")

        assert "Что умеет" in text
        rows = _button_rows(markup)
        # 6 skill rows + 1 menu row
        assert len(rows) == 7

        by_label = {}
        for row in rows[:-1]:
            (label_text, _label_cb), (action_text, action_cb) = row
            by_label[label_text] = (action_text, action_cb)

        # mail — needs_setup → [Настроить]
        mail_label = next(k for k in by_label if "Почта" in k)
        assert "настроить" in mail_label
        assert by_label[mail_label][1] == "skill:setup:mail"

        # groups — ready → [Выкл]
        groups_label = next(k for k in by_label if "Группы" in k)
        assert "готов" in groups_label
        assert by_label[groups_label][1] == "skill:off:groups"

        # vision — off → [Вкл]
        vision_label = next(k for k in by_label if "Зрение" in k)
        assert "выкл" in vision_label
        assert by_label[vision_label][1] == "skill:on:vision"

        # menu back button
        assert rows[-1][0] == ("⬅️ Меню", "menu:home")


# ── Callback handling ─────────────────────────────────────────


class TestSkillCallback:
    def test_on_requires_setup(self):
        adapter = _make_adapter()
        query = _make_query()
        with tempfile.TemporaryDirectory() as tmp:
            with patch("gateway.secretary_router.get_active_profile_path", return_value=Path(tmp)), \
                 patch("gateway.secretary_router.get_active", return_value="secretary-test"), \
                 patch("gateway.secretary_skills_store.load_state", return_value={}):
                import asyncio
                asyncio.run(adapter._handle_skill_callback(query, "skill:on:mail", "456", None, "Test"))

        query.answer.assert_awaited_once()
        # "нужна настройка" alert shown
        assert "настройка" in query.answer.call_args.kwargs.get("text", "")
        query.edit_message_text.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_on_no_setup(self):
        adapter = _make_adapter()
        query = _make_query()
        with tempfile.TemporaryDirectory() as tmp:
            with patch("gateway.secretary_router.get_active_profile_path", return_value=Path(tmp)), \
                 patch("gateway.secretary_router.get_active", return_value="secretary-test"), \
                 patch("gateway.secretary_skills_store.load_state", return_value={}):
                await adapter._handle_skill_callback(query, "skill:on:groups", "456", None, "Test")

        assert "включено" in query.answer.call_args.kwargs.get("text", "")
        query.edit_message_text.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_off(self):
        adapter = _make_adapter()
        query = _make_query()
        with tempfile.TemporaryDirectory() as tmp:
            with patch("gateway.secretary_router.get_active_profile_path", return_value=Path(tmp)), \
                 patch("gateway.secretary_router.get_active", return_value="secretary-test"), \
                 patch("gateway.secretary_skills_store.load_state", return_value={}):
                await adapter._handle_skill_callback(query, "skill:off:mail", "456", None, "Test")

        assert "выключено" in query.answer.call_args.kwargs.get("text", "")

    @pytest.mark.asyncio
    async def test_setup_starts_wizard(self):
        adapter = _make_adapter()
        query = _make_query()
        with tempfile.TemporaryDirectory() as tmp:
            with patch("gateway.secretary_router.get_active_profile_path", return_value=Path(tmp)), \
                 patch("gateway.secretary_user_store.set_pref") as set_pref:
                await adapter._handle_skill_callback(query, "skill:setup:mail", "456", None, "Test")
        # L3: setup arms the wizard and edits to its first step
        set_pref.assert_called_once_with(
            "123", "awaiting_skill_setup",
            {"skill_id": "mail", "field_index": 0, "draft": {}, "await_text": False},
        )
        query.edit_message_text.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_unknown_skill(self):
        adapter = _make_adapter()
        query = _make_query()
        await adapter._handle_skill_callback(query, "skill:on:bogus", "456", None, "Test")
        assert "Неизвестное" in query.answer.call_args.kwargs.get("text", "")
        query.edit_message_text.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_unauthorized(self):
        adapter = _make_adapter()
        adapter._is_callback_user_authorized = MagicMock(return_value=False)
        query = _make_query()
        await adapter._handle_skill_callback(query, "skill:on:mail", "456", None, "Test")
        assert "Not authorized" in query.answer.call_args.kwargs.get("text", "")
        query.edit_message_text.assert_not_awaited()
