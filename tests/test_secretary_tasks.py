"""
Tests: secretary tasks (L6) — task/schedule model, due parsing, UI routing.

Run: venv/bin/python -m pytest tests/test_secretary_tasks.py -q
"""

from __future__ import annotations

import tempfile
from datetime import datetime, timedelta, timezone
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


@pytest.fixture
def tasks_db(monkeypatch, tmp_path):
    import tools.secretary.tasks as t
    monkeypatch.setattr(t, "_db_path", lambda: tmp_path / "secretary_tasks.db")
    t._tls.conn = None
    yield
    t._tls.conn = None


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


# ── tasks model ───────────────────────────────────────────────


class TestTasksModel:
    def test_add_and_list_one_off(self, tasks_db):
        from tools.secretary.tasks import add_task, list_tasks
        add_task("123", "купить молоко", "2026-09-01")
        add_task("123", "позвонить")
        rows = list_tasks("123")
        texts = {r["text"] for r in rows}
        assert texts == {"купить молоко", "позвонить"}
        due = {r["text"]: r["due_at"] for r in rows}
        assert due["купить молоко"] == "2026-09-01"
        assert due["позвонить"] == ""

    def test_schedule_separate_from_tasks(self, tasks_db):
        from tools.secretary.tasks import add_schedule_task, add_task, list_schedule, list_tasks
        add_task("123", "разовое")
        add_schedule_task("123", "отчёт", "daily")
        assert [r["text"] for r in list_tasks("123")] == ["разовое"]
        assert [r["text"] for r in list_schedule("123")] == ["отчёт"]

    def test_add_schedule_computes_next_run(self, tasks_db):
        from tools.secretary.tasks import add_schedule_task, list_schedule
        add_schedule_task("123", "отчёт", "weekly")
        row = list_schedule("123")[0]
        assert row["recurrence"] == "weekly"
        assert row["next_run"]  # non-empty ISO

    def test_add_schedule_bad_recurrence(self, tasks_db):
        import pytest as _p
        from tools.secretary.tasks import add_schedule_task
        with _p.raises(ValueError):
            add_schedule_task("123", "x", "hourly")

    def test_advance_bumps_next_run(self, tasks_db):
        from tools.secretary.tasks import add_schedule_task, advance_schedule, list_schedule
        add_schedule_task("123", "отчёт", "daily")
        before = list_schedule("123")[0]["next_run"]
        assert advance_schedule("123", list_schedule("123")[0]["id"]) is True
        after = list_schedule("123")[0]["next_run"]
        assert after > before

    def test_remove_schedule(self, tasks_db):
        from tools.secretary.tasks import add_schedule_task, list_schedule, remove_schedule
        add_schedule_task("123", "отчёт", "daily")
        tid = list_schedule("123")[0]["id"]
        assert remove_schedule("123", tid) is True
        assert list_schedule("123") == []

    def test_mark_done_removes_from_list(self, tasks_db):
        from tools.secretary.tasks import add_task, list_tasks, mark_done
        add_task("123", "разовое")
        tid = list_tasks("123")[0]["id"]
        assert mark_done("123", tid) is True
        assert list_tasks("123") == []

    def test_migration_adds_columns(self, tasks_db):
        # Simulate a pre-L6 DB (no due_at/recurrence/next_run), then connect.
        import sqlite3
        import tools.secretary.tasks as t
        db = t._db_path()
        db.parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(str(db))
        conn.execute(
            "CREATE TABLE tasks (id INTEGER PRIMARY KEY AUTOINCREMENT, telegram_id TEXT NOT NULL, "
            "text TEXT NOT NULL, status TEXT NOT NULL DEFAULT 'open', created_at TEXT NOT NULL, "
            "updated_at TEXT NOT NULL)"
        )
        conn.commit()
        conn.close()
        t._tls.conn = None  # force reconnect + migrate
        from tools.secretary.tasks import add_task, list_tasks
        add_task("123", "после миграции", "2026-09-01")
        assert list_tasks("123")[0]["due_at"] == "2026-09-01"


# ── due parsing ───────────────────────────────────────────────


class TestDueParsing:
    def _adapter(self):
        return _make_adapter()

    def test_parse_no_due(self):
        assert self._adapter()._parse_task_due("купить") == ("купить", "")

    def test_parse_tomorrow(self):
        title, due = self._adapter()._parse_task_due("купить @ завтра")
        tomorrow = (datetime.now(timezone.utc) + timedelta(days=1)).date().isoformat()
        assert title == "купить"
        assert due == tomorrow

    def test_parse_specific_date(self):
        title, due = self._adapter()._parse_task_due("купить @ 2026-09-01")
        assert title == "купить"
        assert due == "2026-09-01"

    def test_parse_ndays(self):
        title, due = self._adapter()._parse_task_due("купить @ 3д")
        assert title == "купить"
        expected = (datetime.now(timezone.utc) + timedelta(days=3)).date().isoformat()
        assert due == expected

    def test_parse_invalid_date_falls_back(self):
        title, due = self._adapter()._parse_task_due("купить @ not-a-date")
        assert title == "купить @ not-a-date"
        assert due == ""


# ── schedule text intercept ───────────────────────────────────


class TestScheduleIntercept:
    def _msg(self, text, chat_id="123"):
        msg = SimpleNamespace()
        msg.text = text
        msg.chat = SimpleNamespace(id=chat_id)
        msg.from_user = SimpleNamespace(id="123")
        msg.message_id = 1
        msg.message_thread_id = None
        return msg

    @pytest.mark.asyncio
    async def test_parse_daily(self, tasks_db):
        adapter = _make_adapter()
        msg = self._msg("отчёт | ежедневно")
        with patch("gateway.secretary_user_store.get_user", return_value={"prefs": {"pending_schedule_add": True}}), \
             patch("gateway.secretary_user_store.upsert_user"):
            handled = await adapter._handle_pending_schedule_add(msg, "отчёт | ежедневно")
        assert handled is True
        from tools.secretary.tasks import list_schedule
        assert [r["text"] for r in list_schedule("123")] == ["отчёт"]
        assert list_schedule("123")[0]["recurrence"] == "daily"

    @pytest.mark.asyncio
    async def test_parse_weekly_english(self, tasks_db):
        adapter = _make_adapter()
        msg = self._msg("sync | weekly")
        with patch("gateway.secretary_user_store.get_user", return_value={"prefs": {"pending_schedule_add": True}}), \
             patch("gateway.secretary_user_store.upsert_user"):
            await adapter._handle_pending_schedule_add(msg, "sync | weekly")
        from tools.secretary.tasks import list_schedule
        assert list_schedule("123")[0]["recurrence"] == "weekly"

    @pytest.mark.asyncio
    async def test_missing_period(self, tasks_db):
        adapter = _make_adapter()
        msg = self._msg("без периода")
        with patch("gateway.secretary_user_store.get_user", return_value={"prefs": {"pending_schedule_add": True}}), \
             patch("gateway.secretary_user_store.upsert_user"):
            handled = await adapter._handle_pending_schedule_add(msg, "без периода")
        assert handled is True
        # send_message was called with an error hint
        assert "Укажите период" in adapter._bot.send_message.call_args.kwargs["text"]

    @pytest.mark.asyncio
    async def test_not_awaiting_passes_through(self, tasks_db):
        adapter = _make_adapter()
        msg = self._msg("random text")
        with patch("gateway.secretary_user_store.get_user", return_value={"prefs": {}}):
            handled = await adapter._handle_pending_schedule_add(msg, "random text")
        assert handled is False


# ── UI routing ────────────────────────────────────────────────


class TestTasksUI:
    @pytest.mark.asyncio
    async def test_tasks_show_has_schedule_button(self):
        adapter = _make_adapter()
        with patch("tools.secretary.tasks.list_tasks", return_value=[{"id": 1, "text": "a", "due_at": ""}]), \
             patch("tools.secretary.tasks.list_schedule", return_value=[]):
            await adapter._handle_tasks_show(123, "123")
        markup = adapter._bot.send_message.call_args.kwargs["reply_markup"]
        flat = [b for row in _buttons(markup) for b in row]
        assert ("📅 Расписание", "task:schedule") in flat

    @pytest.mark.asyncio
    async def test_render_schedule_empty(self):
        adapter = _make_adapter()
        with patch("tools.secretary.tasks.list_schedule", return_value=[]):
            await adapter._render_schedule(123, "123")
        text = adapter._bot.send_message.call_args.kwargs["text"]
        assert "Расписание пусто" in text

    @pytest.mark.asyncio
    async def test_render_schedule_lists(self):
        adapter = _make_adapter()
        rows = [{"id": 1, "text": "отчёт", "recurrence": "daily", "next_run": "2026-08-15T00:00:00+00:00"}]
        with patch("tools.secretary.tasks.list_schedule", return_value=rows):
            await adapter._render_schedule(123, "123")
        markup = adapter._bot.send_message.call_args.kwargs["reply_markup"]
        flat = [b for row in _buttons(markup) for b in row]
        assert ("✅ отчёт", "task:advance:1") in flat
        assert ("🗑 отчёт", "task:remove_schedule:1") in flat

    @pytest.mark.asyncio
    async def test_schedule_callback_routes(self):
        adapter = _make_adapter()
        query = _make_query()
        with patch("tools.secretary.tasks.list_schedule", return_value=[]):
            await adapter._handle_task_callback(query, "task:schedule", "123", None, "Test")
        query.delete_message.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_advance_callback(self, tasks_db):
        adapter = _make_adapter()
        query = _make_query()
        with patch("tools.secretary.tasks.list_schedule", return_value=[]):
            await adapter._handle_task_callback(query, "task:advance:99", "123", None, "Test")
        # advance for unknown id → answer "Не найдена", still re-renders
        assert query.answer.await_args.kwargs["text"] == "❌ Не найдена"
