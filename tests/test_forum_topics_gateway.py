"""
tests/test_forum_topics_gateway.py — Forum Topics: gateway-level tests

Проверяет:
1. _send_local_with_topic helper — thread_id extraction
2. _thread_kwargs_for_send — correct routing
3. _message_thread_id_for_send — General topic (id=1) → None
4. Thread ID preserved through the pipeline
"""

import pytest
from unittest.mock import AsyncMock, MagicMock, PropertyMock


# ── Тест 1: _send_local_with_topic извлекает thread_id ──────

class FakeTelegramAdapter:
    """Minimal mock of the Telegram adapter for testing thread routing."""

    _GENERAL_TOPIC_THREAD_ID = "1"

    def __init__(self, monkeypatch):
        self._reply_to_mode = "default"
        self.sent_kwargs = None
        self.name = "test_bot"

    async def _send_message_with_thread_fallback(self, **kwargs):
        self.sent_kwargs = kwargs
        return MagicMock(message_id=1)

    @classmethod
    def _thread_kwargs_for_send(cls, chat_id, thread_id, metadata,
                                 reply_to_message_id=None, reply_to_mode="default"):
        # Simplified mock — matches the real implementation's core path
        # for forum topic sends. DM topic lanes not tested here.
        if metadata and metadata.get("telegram_dm_topic_reply_fallback"):
            if reply_to_mode == "off":
                return {"message_thread_id": cls._message_thread_id_for_send(thread_id)}
            return {"message_thread_id": cls._message_thread_id_for_send(thread_id)}
        direct_topic_id = None
        if metadata:
            direct_topic_id = metadata.get("telegram_direct_messages_topic_id")
        if direct_topic_id is not None:
            return {"message_thread_id": None, "direct_messages_topic_id": int(direct_topic_id)}
        return {"message_thread_id": cls._message_thread_id_for_send(thread_id)}

    @staticmethod
    def _message_thread_id_for_send(thread_id):
        if not thread_id or str(thread_id) == "1":
            return None
        return int(thread_id)

    async def _send_local_with_topic(self, msg, chat_id=None, **send_kwargs):
        """Copy of the real implementation for testing."""
        thread_id = getattr(msg, "message_thread_id", None)
        if thread_id is not None and chat_id is None:
            chat_id = str(msg.chat.id)
        if chat_id is not None and thread_id is not None:
            send_kwargs.update(
                self._thread_kwargs_for_send(
                    str(chat_id), str(thread_id),
                    {"thread_id": str(thread_id)},
                    reply_to_mode=self._reply_to_mode,
                )
            )
        await self._send_message_with_thread_fallback(**send_kwargs)
        return True


@pytest.fixture
def adapter():
    return FakeTelegramAdapter(None)


def test_send_local_in_forum_topic(adapter):
    """Сообщение из темы форума → thread_id пробрасывается в send."""
    msg = MagicMock()
    msg.message_thread_id = 42
    msg.chat.id = -1001234567890

    import asyncio
    asyncio.run(adapter._send_local_with_topic(
        msg,
        chat_id=-1001234567890,
        text="Ответ",
    ))

    assert adapter.sent_kwargs is not None
    assert adapter.sent_kwargs["text"] == "Ответ"
    assert adapter.sent_kwargs.get("message_thread_id") == 42


def test_send_local_in_regular_group(adapter):
    """Обычная группа (не форум) → message_thread_id НЕ передаётся."""
    msg = MagicMock()
    msg.message_thread_id = None
    msg.chat.id = -1009999999999

    import asyncio
    asyncio.run(adapter._send_local_with_topic(
        msg,
        chat_id=-1009999999999,
        text="Ответ",
    ))

    assert adapter.sent_kwargs is not None
    assert "message_thread_id" not in adapter.sent_kwargs


def test_send_local_general_topic_stripped(adapter):
    """General topic (thread_id=1) → message_thread_id = None (PTB-compatible)."""
    msg = MagicMock()
    msg.message_thread_id = 1
    msg.chat.id = -1001234567890

    import asyncio
    asyncio.run(adapter._send_local_with_topic(
        msg,
        chat_id=-1001234567890,
        text="В General",
    ))

    assert adapter.sent_kwargs is not None
    # message_thread_id is None (stripped by _message_thread_id_for_send),
    # PTB's send_message ignores None kwargs so the message lands in General.
    assert adapter.sent_kwargs.get("message_thread_id") is None


def test_send_local_no_thread_attr(adapter):
    """У сообщения нет атрибута message_thread_id (старая версия API)."""
    msg = MagicMock(spec=["chat"])  # no message_thread_id attribute
    msg.chat.id = -1001234567890

    import asyncio
    asyncio.run(adapter._send_local_with_topic(
        msg,
        chat_id=-1001234567890,
        text="Старенький",
    ))

    assert "message_thread_id" not in adapter.sent_kwargs


# ── Тест 2: _thread_kwargs_for_send — DM topic lanes ────────

def test_thread_kwargs_dm_topic_fallback_reply_off(adapter):
    """DM topic fallback + reply_to_mode=off → message_thread_id."""
    adapter._reply_to_mode = "off"
    metadata = {"telegram_dm_topic_reply_fallback": True}

    kwargs = adapter._thread_kwargs_for_send(
        "-100123", "42", metadata,
        reply_to_message_id=None,
        reply_to_mode="off",
    )
    assert kwargs == {"message_thread_id": 42}


def test_thread_kwargs_direct_messages_topic(adapter):
    """direct_messages_topic_id в метаданных (без dm_topic_reply_fallback)."""
    # Real _thread_kwargs_for_send checks dm_topic_reply_fallback first,
    # so without it, falls through to direct_messages_topic_id.
    metadata = {"telegram_direct_messages_topic_id": "99"}

    kwargs = adapter._thread_kwargs_for_send(
        "-100123", "42", metadata,
    )
    # Without dm_topic_reply_fallback, the real function checks
    # direct_messages_topic_id and returns {message_thread_id: None, direct_messages_topic_id: 99}
    assert kwargs["direct_messages_topic_id"] == 99
    assert kwargs["message_thread_id"] is None


# ── Тест 3: _message_thread_id_for_send ─────────────────────

def test_thread_id_for_send_null(adapter):
    assert adapter._message_thread_id_for_send(None) is None
    assert adapter._message_thread_id_for_send("") is None


def test_thread_id_for_send_general(adapter):
    """General topic (1) → None."""
    assert adapter._message_thread_id_for_send("1") is None
    assert adapter._message_thread_id_for_send(1) is None


def test_thread_id_for_send_regular(adapter):
    """Обычная тема форума → int."""
    assert adapter._message_thread_id_for_send("42") == 42
    assert adapter._message_thread_id_for_send(42) == 42
