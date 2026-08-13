"""
Tests: secretary skill validation (L4) — validate_skill + result view + callback.

Run: venv/bin/python -m pytest tests/test_secretary_skill_validate.py -q
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
    query.message = SimpleNamespace(chat=SimpleNamespace(type="private"), chat_id="456", message_thread_id=None)
    query.answer = AsyncMock()
    query.edit_message_text = AsyncMock()
    return query


def _home_with_env(**kwargs):
    from gateway.secretary_skills_store import write_env_value
    tmp = tempfile.TemporaryDirectory()
    home = Path(tmp.name)
    for k, v in kwargs.items():
        write_env_value(home, k, v)
    return tmp, home


# ── validate_skill dispatch ───────────────────────────────────


class TestValidateSkillDispatch:
    def test_groups_tasks_ready(self):
        from gateway.secretary_skill_validate import validate_skill
        with tempfile.TemporaryDirectory() as tmp:
            assert validate_skill("groups", Path(tmp)) == ("ready", "")
            assert validate_skill("tasks", Path(tmp)) == ("ready", "")

    def test_unknown_skill(self):
        from gateway.secretary_skill_validate import validate_skill
        with tempfile.TemporaryDirectory() as tmp:
            status, msg = validate_skill("nope", Path(tmp))
            assert status == "error"

    def test_mail_missing_creds(self):
        from gateway.secretary_skill_validate import validate_skill
        with tempfile.TemporaryDirectory() as tmp:
            status, msg = validate_skill("mail", Path(tmp))
            assert status == "error"
            assert "заданы" in msg or "host" in msg

    def test_calendar_missing_url(self):
        from gateway.secretary_skill_validate import validate_skill
        with tempfile.TemporaryDirectory() as tmp:
            status, msg = validate_skill("calendar", Path(tmp))
            assert status == "error"


# ── vision / tgcli (binary checks) ────────────────────────────


class TestBinaryChecks:
    def test_vision_found(self):
        from gateway.secretary_skill_validate import validate_vision
        with patch("gateway.secretary_skill_validate.shutil.which", return_value="/usr/bin/vision-cli"):
            assert validate_vision(Path("/tmp")) == ("ready", "vision-cli найден")

    def test_vision_missing(self):
        from gateway.secretary_skill_validate import validate_vision
        with patch("gateway.secretary_skill_validate.shutil.which", return_value=None):
            assert validate_vision(Path("/tmp"))[0] == "error"

    def test_tgcli_missing_binary(self):
        from gateway.secretary_skill_validate import validate_tgcli
        with patch("gateway.secretary_skill_validate.shutil.which", return_value=None):
            assert validate_tgcli(Path("/tmp"))[0] == "error"

    def test_tgcli_with_session(self):
        from gateway.secretary_skill_validate import validate_tgcli
        with patch("gateway.secretary_skill_validate.shutil.which", return_value="/usr/bin/tg"), \
             patch("subprocess.run") as run:
            run.return_value = SimpleNamespace(returncode=0)
            assert validate_tgcli(Path("/tmp")) == ("ready", "tg: сессия есть")

    def test_tgcli_no_session(self):
        from gateway.secretary_skill_validate import validate_tgcli
        with patch("gateway.secretary_skill_validate.shutil.which", return_value="/usr/bin/tg"), \
             patch("subprocess.run") as run:
            run.return_value = SimpleNamespace(returncode=1)
            status, msg = validate_tgcli(Path("/tmp"))
            assert status == "needs_setup"
            assert "tg auth" in msg


# ── mail IMAP (mocked) ────────────────────────────────────────


class TestMailIMAP:
    def test_mail_login_success(self):
        from gateway.secretary_skill_validate import validate_mail

        class _FakeConn:
            def login(self, email, pwd):
                return ("OK", [b""])
            def logout(self):
                return ("OK", [b""])

        _, home = _home_with_env(
            SECRETARY_MAIL_IMAP_HOST="imap.gmail.com",
            SECRETARY_MAIL_EMAIL="a@b.com",
            SECRETARY_MAIL_PASSWORD="pw",
        )
        try:
            with patch("imaplib.IMAP4_SSL", return_value=_FakeConn()):
                assert validate_mail(home)[0] == "ready"
        finally:
            home_root = home
            import shutil
            # _home_with_env returns a TemporaryDirectory; clean up via parent
            shutil.rmtree(str(home), ignore_errors=True)

    def test_mail_login_failure(self):
        from gateway.secretary_skill_validate import validate_mail

        class _FakeConn:
            def login(self, email, pwd):
                raise Exception("AUTHENTICATIONFAILED")
            def logout(self):
                return ("OK", [b""])

        _, home = _home_with_env(
            SECRETARY_MAIL_IMAP_HOST="imap.gmail.com",
            SECRETARY_MAIL_EMAIL="a@b.com",
            SECRETARY_MAIL_PASSWORD="wrong",
        )
        try:
            with patch("imaplib.IMAP4_SSL", return_value=_FakeConn()):
                status, msg = validate_mail(home)
                assert status == "error"
                assert "входа" in msg
        finally:
            import shutil
            shutil.rmtree(str(home), ignore_errors=True)


# ── result view ───────────────────────────────────────────────


class _Skill:
    id = "mail"
    title = "Почта"


class TestValidateResultView:
    def test_ready_view(self):
        adapter = _make_adapter()
        text, markup = adapter._validate_result_view(_Skill(), "ready", "подключение успешно")
        assert "готово" in text
        assert "подключение успешно" in text

    def test_error_view(self):
        adapter = _make_adapter()
        text, markup = adapter._validate_result_view(_Skill(), "error", "ошибка входа")
        assert "ошибка" in text
        rows = [[(b.text, b.callback_data) for b in row] for row in markup.inline_keyboard]
        flat = [b for row in rows for b in row]
        assert ("🔧 Повторить настройку", "skill:setup:mail") in flat

    def test_needs_setup_view(self):
        adapter = _make_adapter()
        text, markup = adapter._validate_result_view(_Skill(), "needs_setup", "выполните tg auth")
        assert "нужна настройка" in text
        rows = [[(b.text, b.callback_data) for b in row] for row in markup.inline_keyboard]
        flat = [b for row in rows for b in row]
        assert ("⚙️ Настроить", "skill:setup:mail") in flat


# ── validate callback ─────────────────────────────────────────


class TestValidateCallback:
    @pytest.mark.asyncio
    async def test_validate_sets_status_and_edits(self):
        adapter = _make_adapter()
        query = _make_query()
        with tempfile.TemporaryDirectory() as tmp:
            with patch("gateway.secretary_skill_validate.validate_skill", return_value=("ready", "ok")), \
                 patch("gateway.secretary_skills_store.set_skill") as set_skill:
                await adapter._validate_skill_callback(query, "123", _Skill(), Path(tmp))
        set_skill.assert_called_once_with(Path(tmp), "mail", True, "ready")
        query.answer.assert_awaited_once()
        query.edit_message_text.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_validate_error_persists_error(self):
        adapter = _make_adapter()
        query = _make_query()
        with tempfile.TemporaryDirectory() as tmp:
            with patch("gateway.secretary_skill_validate.validate_skill", return_value=("error", "bad")), \
                 patch("gateway.secretary_skills_store.set_skill") as set_skill:
                await adapter._validate_skill_callback(query, "123", _Skill(), Path(tmp))
        set_skill.assert_called_once_with(Path(tmp), "mail", True, "error")


# ── skill view has [Проверить] button ─────────────────────────


class TestSkillViewValidateButton:
    def test_enabled_skill_has_validate_button(self):
        adapter = _make_adapter()
        state = {"mail": {"enabled": True, "status": "needs_setup"}}
        with tempfile.TemporaryDirectory() as tmp:
            with patch("gateway.secretary_router.get_active", return_value="secretary-test"), \
                 patch("gateway.secretary_router.get_active_profile_path", return_value=Path(tmp)), \
                 patch("gateway.secretary_skills_store.load_state", return_value=state):
                text, markup = adapter._skill_view("123")
        rows = [[(b.text, b.callback_data) for b in row] for row in markup.inline_keyboard]
        flat = [b for row in rows for b in row]
        assert ("Проверить", "skill:validate:mail") in flat
