"""
Tests: secretary /help (L8) — simplified help points to the menu.

Run: venv/bin/python -m pytest tests/test_secretary_help.py -q
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

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
    return adapter


@pytest.mark.asyncio
async def test_help_mentions_new_features():
    adapter = _make_adapter()
    msg = SimpleNamespace(chat=SimpleNamespace(id="123"), message_id=1)
    await adapter._handle_help_command(msg)
    text = adapter._bot.send_message.call_args.kwargs["text"]
    for fragment in ("Почта", "Календарь", "Задачи", "Группы", "Что умеет", "расписание"):
        assert fragment in text, f"help missing {fragment!r}"
    markup = adapter._bot.send_message.call_args.kwargs["reply_markup"]
    flat = [b for row in markup.inline_keyboard for b in row]
    assert ("📋 Меню", "menu:home") in [(b.text, b.callback_data) for b in flat]
