"""Tests for LLM draft + template fallback."""
from __future__ import annotations
import os
from unittest.mock import patch, MagicMock


class TestLLMDraft:
    def test_template_fallback_when_no_api_key(self):
        from tools.secretary.mail_draft import generate_draft
        with patch.dict(os.environ, {"SECRETARY_MAIL_DRAFT_MODE": "llm"}, clear=True):
            draft = generate_draft({
                "subject": "Test", "from_addr": "a@b.com",
                "from_name": "Alice", "body_text": "Hello, can we meet?",
            }, sender_name="Bob")
            assert "Re: Test" in draft
            assert "Bob" in draft

    def test_template_mode_explicit(self):
        from tools.secretary.mail_draft import generate_draft
        with patch.dict(os.environ, {"SECRETARY_MAIL_DRAFT_MODE": "template"}, clear=True):
            draft = generate_draft({
                "subject": "Q", "from_addr": "a@b.com",
                "from_name": "X", "body_text": "Question?",
            }, sender_name="")
            assert "Re: Q" in draft

    def test_llm_fail_falls_back_to_template(self):
        from tools.secretary.mail_draft import _llm_draft
        mail = {"subject": "T", "from_addr": "a@b.com", "from_name": "A", "body_text": "Hi"}
        try:
            _llm_draft(mail, "Bob")
            assert False, "Should raise without API key"
        except RuntimeError:
            pass  # Expected

    def test_llm_call_mocked(self):
        from tools.secretary.mail_draft import _call_llm
        from unittest.mock import patch
        from io import BytesIO
        import json

        mock_resp = BytesIO(json.dumps({
            "choices": [{"message": {"content": "Mocked reply text."}}]
        }).encode())

        with patch.dict(os.environ, {"SECRETARY_LLM_API_KEY": "sk-test"}, clear=True):
            with patch("tools.secretary.mail_draft.urlopen", return_value=mock_resp):
                result = _call_llm("system", "user")
                assert result == "Mocked reply text."
