"""
Tests: secretary groups screen (L5) — view, watch/unwatch, report.

Run: venv/bin/python -m pytest tests/test_secretary_groups.py -q
"""

from __future__ import annotations

import tempfile
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

import gateway.platforms.telegram as _tg_mod


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


def _make_query():
    query = SimpleNamespace()
    query.from_user = SimpleNamespace(id="123", first_name="Test")
    query.message = SimpleNamespace(chat=SimpleNamespace(type="private"), chat_id="123", message_thread_id=None)
    query.answer = AsyncMock()
    query.edit_message_text = AsyncMock()
    query.delete_message = AsyncMock()
    return query


def _buttons(markup):
    return [[(b.text, b.callback_data) for b in row] for row in markup.inline_keyboard]


def _directory(entries):
    return {"updated_at": None, "platforms": {"telegram": entries}}


# ── _groups_view ──────────────────────────────────────────────


class TestGroupsView:
    def test_not_ready_shows_enable(self):
        adapter = _make_adapter()
        state = {"groups": {"enabled": False, "status": "off"}}
        with tempfile.TemporaryDirectory() as tmp:
            with patch("gateway.secretary_router.get_active_profile_path", return_value=Path(tmp)), \
                 patch("gateway.secretary_skills_store.load_state", return_value=state):
                text, markup = adapter._groups_view("123")
        assert "не включены" in text
        rows = _buttons(markup)
        flat = [b for row in rows for b in row]
        assert ("⚙️ Включить", "skill:on:groups") in flat

    def test_ready_lists_chats(self):
        adapter = _make_adapter()
        state = {"groups": {"enabled": True, "status": "ready"}}
        with tempfile.TemporaryDirectory() as tmp:
            with patch("gateway.secretary_router.get_active_profile_path", return_value=Path(tmp)), \
                 patch("gateway.secretary_skills_store.load_state", return_value=state), \
                 patch("tools.archive_admin.archive_admin_tool", return_value='{"mode":"allowlist","chats":["-100123"]}'), \
                 patch("gateway.channel_directory.load_directory", return_value=_directory([
                     {"id": "-100123", "name": "НДС", "type": "group"},
                     {"id": "-100999", "name": "Прочее", "type": "group"},
                 ])):
                text, markup = adapter._groups_view("123")
        rows = _buttons(markup)
        flat = [b for row in rows for b in row]
        # -100123 is watched → [Не следить]
        assert ("НДС (следит)", "mx:noop") in flat
        assert ("Не следить", "groups:unwatch:-100123") in flat
        # -100999 not watched → [Следить]
        assert ("Прочее (не следит)", "mx:noop") in flat
        assert ("Следить", "groups:watch:-100999") in flat
        # footer
        assert ("➕ Как добавить", "groups:how") in flat
        assert ("📊 Что было", "groups:report") in flat

    def test_empty_known_groups(self):
        adapter = _make_adapter()
        state = {"groups": {"enabled": True, "status": "ready"}}
        with tempfile.TemporaryDirectory() as tmp:
            with patch("gateway.secretary_router.get_active_profile_path", return_value=Path(tmp)), \
                 patch("gateway.secretary_skills_store.load_state", return_value=state), \
                 patch("tools.archive_admin.archive_admin_tool", return_value='{"mode":"all","chats":null}'), \
                 patch("gateway.channel_directory.load_directory", return_value=_directory([])):
                text, markup = adapter._groups_view("123")
        assert "нет известных групп" in text


# ── _resolve_chat_name ────────────────────────────────────────


class TestResolveChatName:
    def test_falls_back_to_id(self):
        adapter = _make_adapter()
        with patch("gateway.channel_directory.load_directory", return_value=_directory([])):
            assert adapter._resolve_chat_name("-100123") == "-100123"

    def test_resolves_name(self):
        adapter = _make_adapter()
        with patch("gateway.channel_directory.load_directory", return_value=_directory([
            {"id": "-100123", "name": "НДС", "type": "group"},
        ])):
            assert adapter._resolve_chat_name("-100123") == "НДС"


# ── _archive_summary ──────────────────────────────────────────


class _FakeDB:
    def __init__(self, rows):
        self._rows = rows

    def query(self, **kwargs):
        return self._rows


class TestArchiveSummary:
    def test_empty(self):
        adapter = _make_adapter()
        with patch("plugins.message_archive.get_archive_db", return_value=_FakeDB([])):
            text = adapter._archive_summary(24)
        assert "ничего не найдено" in text

    def test_counts(self):
        adapter = _make_adapter()
        rows = [
            {"msg_type": "text", "chat_id": "-100123"},
            {"msg_type": "text", "chat_id": "-100123"},
            {"msg_type": "photo", "chat_id": "-100123"},
            {"msg_type": "document_pdf", "chat_id": "-100999"},
        ]
        with patch("plugins.message_archive.get_archive_db", return_value=_FakeDB(rows)), \
             patch("gateway.channel_directory.load_directory", return_value=_directory([])):
            text = adapter._archive_summary(24)
        assert "4 сообщений" in text
        assert "text: 2" in text
        assert "photo: 1" in text

    def test_db_error(self):
        adapter = _make_adapter()
        with patch("plugins.message_archive.get_archive_db", side_effect=RuntimeError("boom")):
            text = adapter._archive_summary(24)
        assert "Не удалось" in text


# ── _handle_groups_callback routing ───────────────────────────


class TestGroupsCallback:
    @pytest.mark.asyncio
    async def test_how_shows_instruction(self):
        adapter = _make_adapter()
        query = _make_query()
        await adapter._handle_groups_callback(query, "groups:how", "123", None, "Test")
        assert query.edit_message_text.await_args.kwargs["text"].startswith("➕ Как добавить")

    @pytest.mark.asyncio
    async def test_report_shows_submenu(self):
        adapter = _make_adapter()
        query = _make_query()
        await adapter._handle_groups_callback(query, "groups:report", "123", None, "Test")
        markup = query.edit_message_text.await_args.kwargs["reply_markup"]
        rows = _buttons(markup)
        flat = [b for row in rows for b in row]
        assert ("🕐 Сутки", "groups:report:24") in flat
        assert ("🗓 Неделя", "groups:report:168") in flat

    @pytest.mark.asyncio
    async def test_watch_calls_add_chat(self):
        adapter = _make_adapter()
        query = _make_query()
        state = {"groups": {"enabled": True, "status": "ready"}}
        with tempfile.TemporaryDirectory() as tmp:
            with patch("gateway.secretary_router.get_active_profile_path", return_value=Path(tmp)), \
                 patch("gateway.secretary_skills_store.load_state", return_value=state), \
                 patch("tools.archive_admin.archive_admin_tool") as tool, \
                 patch("gateway.channel_directory.load_directory", return_value=_directory([])):
                tool.return_value = '{"status":"added","chats":["-100123"]}'
                await adapter._handle_groups_callback(query, "groups:watch:-100123", "123", None, "Test")
        # add_chat called with confirmed=True
        call = [c for c in tool.call_args_list if c.args and c.args[0] == "add_chat"]
        assert call, "add_chat was not called"
        assert call[0].args[1] == "-100123"

    @pytest.mark.asyncio
    async def test_unwatch_calls_remove_chat(self):
        adapter = _make_adapter()
        query = _make_query()
        state = {"groups": {"enabled": True, "status": "ready"}}
        with tempfile.TemporaryDirectory() as tmp:
            with patch("gateway.secretary_router.get_active_profile_path", return_value=Path(tmp)), \
                 patch("gateway.secretary_skills_store.load_state", return_value=state), \
                 patch("tools.archive_admin.archive_admin_tool") as tool, \
                 patch("gateway.channel_directory.load_directory", return_value=_directory([])):
                tool.return_value = '{"status":"removed","chats":[]}'
                await adapter._handle_groups_callback(query, "groups:unwatch:-100123", "123", None, "Test")
        call = [c for c in tool.call_args_list if c.args and c.args[0] == "remove_chat"]
        assert call, "remove_chat was not called"

    @pytest.mark.asyncio
    async def test_unauthorized_rejected(self):
        adapter = _make_adapter()
        adapter._is_callback_user_authorized = MagicMock(return_value=False)
        query = _make_query()
        await adapter._handle_groups_callback(query, "groups:how", "123", None, "Test")
        assert "Not authorized" in query.answer.call_args.kwargs.get("text", "")
