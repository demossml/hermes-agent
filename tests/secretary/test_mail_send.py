"""
Unit tests for tools/secretary/mail_draft.py and mail_inbox send (dry-run).
"""

from __future__ import annotations

import os
from unittest.mock import patch


class TestGenerateDraft:
    """generate_draft() tests."""

    def test_basic_draft(self):
        from tools.secretary.mail_draft import generate_draft
        mail = {
            "subject": "Test",
            "from_addr": "alice@example.com",
            "from_name": "Alice Smith",
            "body_text": "Hello, can we meet tomorrow?",
        }
        draft = generate_draft(mail, sender_name="Bob")
        assert "Re: Test" in draft
        assert "Здравствуйте, Alice" in draft
        # "can we meet tomorrow?" → has "?" → question template
        assert "вопрос" in draft.lower()
        assert "Bob" in draft

    def test_meeting_detection(self):
        from tools.secretary.mail_draft import generate_draft
        mail = {
            "subject": "Call",
            "from_addr": "x@x.com",
            "from_name": "X",
            "body_text": "Давайте созвонимся завтра в 10.",
        }
        draft = generate_draft(mail)
        assert "созвон" in draft.lower() or "встреч" in draft.lower()

    def test_thanks_reply(self):
        from tools.secretary.mail_draft import generate_draft
        mail = {
            "subject": "Thanks",
            "from_addr": "x@x.com",
            "from_name": "X",
            "body_text": "Спасибо за помощь!",
        }
        draft = generate_draft(mail)
        assert "Пожалуйста" in draft or "Рад" in draft

    def test_short_body(self):
        from tools.secretary.mail_draft import generate_draft
        mail = {
            "subject": "Hi",
            "from_addr": "x@x.com",
            "from_name": "X",
            "body_text": "ok",
        }
        draft = generate_draft(mail)
        assert "ознакомился" in draft.lower()

    def test_no_name_uses_addr(self):
        from tools.secretary.mail_draft import generate_draft
        mail = {
            "subject": "Q",
            "from_addr": "x@x.com",
            "from_name": "",
            "body_text": "What time is it?",
        }
        draft = generate_draft(mail)
        assert "Здравствуйте" in draft


class TestSendMailDryRun:
    """send_mail with DRY_RUN=1."""

    def test_dry_run_logs_no_send(self):
        from tools.secretary.mail_inbox import send_mail

        with patch.dict(os.environ, {"SECRETARY_MAIL_DRY_RUN": "1"}, clear=True):
            result = send_mail("to@x.com", "Test", "Body")
            assert result["sent"] is True
            assert result["dry_run"] is True
            assert "DRY_RUN" in result["details"]

    def test_dry_run_disabled_needs_config(self):
        from tools.secretary.mail_inbox import send_mail

        with patch.dict(os.environ, {"SECRETARY_MAIL_DRY_RUN": "0"}, clear=True):
            # No SMTP host → error
            result = send_mail("to@x.com", "Test", "Body")
            assert result["sent"] is False
            assert "SMTP" in result.get("details", "")


class TestFetchBodyErrors:
    """fetch_body error handling."""

    def test_not_configured(self):
        from tools.secretary.mail_inbox import fetch_body

        with patch.dict(os.environ, {}, clear=True):
            try:
                fetch_body("123")
                assert False, "Should raise"
            except RuntimeError as e:
                assert "not configured" in str(e).lower()
