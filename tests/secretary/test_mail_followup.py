"""
Unit tests for list_awaiting_reply.
"""

from __future__ import annotations

import os
from unittest.mock import patch


class TestListAwaitingReply:
    """list_awaiting_reply() error paths."""

    def test_not_configured(self):
        from tools.secretary.mail_inbox import list_awaiting_reply
        with patch.dict(os.environ, {}, clear=True):
            try:
                list_awaiting_reply(days=3)
                assert False, "Should raise"
            except RuntimeError as e:
                assert "not configured" in str(e).lower()

    def test_partial_config(self):
        from tools.secretary.mail_inbox import list_awaiting_reply
        with patch.dict(os.environ, {
            "SECRETARY_MAIL_BACKEND": "imap",
            "SECRETARY_MAIL_IMAP_HOST": "imap.example.com",
        }, clear=True):
            try:
                list_awaiting_reply(days=3)
                assert False, "Should raise"
            except RuntimeError as e:
                assert "not configured" in str(e).lower()
