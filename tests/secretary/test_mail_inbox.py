"""
Unit tests for tools/secretary/mail_inbox.py — formatter and mock backend.
"""

from __future__ import annotations

import os
from datetime import datetime, timedelta, timezone
from unittest.mock import patch


def _now() -> datetime:
    return datetime(2026, 8, 10, 14, 30, tzinfo=timezone.utc)


class TestFormatMailList:
    """format_mail_list() tests."""

    def test_empty(self):
        from tools.secretary.mail_inbox import format_mail_list
        result = format_mail_list([], hours=12)
        assert "Нет новых писем" in result

    def test_one_email(self):
        from tools.secretary.mail_inbox import format_mail_list
        emails = [{
            "id": "1",
            "from_addr": "alice@example.com",
            "from_name": "Alice",
            "subject": "Test subject",
            "date_display": "Сегодня 10:00",
        }]
        result = format_mail_list(emails, hours=12)
        assert "Почта за 12 ч (1)" in result
        assert "Alice" in result
        assert "Test subject" in result

    def test_truncation(self):
        from tools.secretary.mail_inbox import format_mail_list
        emails = [{
            "id": str(i),
            "from_addr": f"user{i}@example.com",
            "from_name": f"User {i}",
            "subject": f"Very long subject line that goes beyond sixty characters {i}",
            "date_display": "Вчера 12:00",
        } for i in range(20)]
        result = format_mail_list(emails, hours=24)
        assert "... и ещё" in result  # pagination
        assert "..." in result

    def test_no_name_uses_addr(self):
        from tools.secretary.mail_inbox import format_mail_list
        emails = [{
            "id": "1",
            "from_addr": "bob@example.com",
            "from_name": "",
            "subject": "Hi",
            "date_display": "10 авг 12:00",
        }]
        result = format_mail_list(emails, hours=12)
        assert "bob@example.com" in result


class TestDateDisplay:
    """_format_date_display tests."""

    def test_today(self):
        from tools.secretary.mail_inbox import _format_date_display
        dt = _now()
        result = _format_date_display(dt)
        assert "Сегодня" in result

    def test_yesterday(self):
        from tools.secretary.mail_inbox import _format_date_display
        dt = _now() - timedelta(days=1)
        result = _format_date_display(dt)
        assert "Вчера" in result

    def test_older(self):
        from tools.secretary.mail_inbox import _format_date_display
        dt = _now() - timedelta(days=5)
        result = _format_date_display(dt)
        assert "авг" in result  # August in Russian


class TestIsConfigured:
    """is_configured() tests."""

    def test_not_configured_by_default(self):
        # Clear relevant env vars
        with patch.dict(os.environ, {}, clear=True):
            from tools.secretary.mail_inbox import is_configured
            assert not is_configured()

    def test_configured_imap(self):
        with patch.dict(os.environ, {
            "SECRETARY_MAIL_BACKEND": "imap",
            "SECRETARY_MAIL_IMAP_HOST": "imap.example.com",
            "SECRETARY_MAIL_EMAIL": "test@example.com",
            "SECRETARY_MAIL_PASSWORD": "secret",
        }, clear=True):
            from tools.secretary.mail_inbox import is_configured
            assert is_configured()

    def test_not_configured_missing_password(self):
        with patch.dict(os.environ, {
            "SECRETARY_MAIL_BACKEND": "imap",
            "SECRETARY_MAIL_IMAP_HOST": "imap.example.com",
            "SECRETARY_MAIL_EMAIL": "test@example.com",
        }, clear=True):
            from tools.secretary.mail_inbox import is_configured
            assert not is_configured()


class TestListRecentErrors:
    """list_recent error handling."""

    def test_raises_when_not_configured(self):
        with patch.dict(os.environ, {}, clear=True):
            from tools.secretary.mail_inbox import list_recent
            try:
                list_recent(hours=12)
                assert False, "Should have raised RuntimeError"
            except RuntimeError as e:
                assert "not configured" in str(e).lower()

    def test_raises_on_imap_partial_config(self):
        with patch.dict(os.environ, {
            "SECRETARY_MAIL_BACKEND": "imap",
            "SECRETARY_MAIL_IMAP_HOST": "imap.example.com",
        }, clear=True):
            from tools.secretary.mail_inbox import list_recent
            try:
                list_recent(hours=12)
                assert False, "Should have raised RuntimeError"
            except RuntimeError as e:
                assert "not configured" in str(e).lower()


class TestParseFrom:
    """_parse_from tests."""

    def test_name_and_addr(self):
        from tools.secretary.mail_inbox import _parse_from
        name, addr = _parse_from("Alice <alice@example.com>")
        assert name == "Alice"
        assert addr == "alice@example.com"

    def test_addr_only(self):
        from tools.secretary.mail_inbox import _parse_from
        name, addr = _parse_from("alice@example.com")
        assert addr == "alice@example.com"

    def test_empty(self):
        from tools.secretary.mail_inbox import _parse_from
        name, addr = _parse_from("")
        assert name == ""
