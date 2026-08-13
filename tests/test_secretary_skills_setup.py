"""
Tests: secretary skill setup wizard (L3) — manifests, env write, wizard steps.

Run: venv/bin/python -m pytest tests/test_secretary_skills_setup.py -q
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


def _make_query(chat_type="private"):
    query = SimpleNamespace()
    query.from_user = SimpleNamespace(id="123", first_name="Test")
    query.message = SimpleNamespace(chat=SimpleNamespace(type=chat_type), chat_id="456", message_thread_id=None)
    query.answer = AsyncMock()
    query.edit_message_text = AsyncMock()
    return query


def _buttons(markup):
    return [[(b.text, b.callback_data) for b in row] for row in markup.inline_keyboard]


# ── Manifests ──────────────────────────────────────────────────


class TestManifests:
    def test_mail_fields(self):
        from gateway.secretary_skills_registry import get_manifest
        m = get_manifest("mail")
        assert [f.key for f in m] == [
            "SECRETARY_MAIL_EMAIL", "SECRETARY_MAIL_PASSWORD", "SECRETARY_MAIL_IMAP_HOST",
        ]
        assert m[1].secret is True
        assert m[1].type == "password"
        assert m[2].type == "choice"
        assert m[2].choice_map["gmail"] == "imap.gmail.com"
        assert "other" in m[2].choices

    def test_calendar_and_tgcli(self):
        from gateway.secretary_skills_registry import get_manifest
        assert [f.key for f in get_manifest("calendar")] == ["SECRETARY_CAL_ICS_URL"]
        tg = get_manifest("tgcli")
        assert [f.key for f in tg] == ["TG_API_ID", "TG_API_HASH"]
        assert tg[1].secret is True

    def test_empty_manifests(self):
        from gateway.secretary_skills_registry import get_manifest
        assert get_manifest("vision") == ()
        assert get_manifest("groups") == ()
        assert get_manifest("tasks") == ()
        assert get_manifest("nope") == ()


# ── Env write/read ─────────────────────────────────────────────


class TestEnvWrite:
    def test_write_and_read(self):
        from gateway.secretary_skills_store import read_env_value, write_env_value
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp)
            write_env_value(home, "SECRETARY_MAIL_EMAIL", "a@b.com")
            assert read_env_value(home, "SECRETARY_MAIL_EMAIL") == "a@b.com"

    def test_update_existing(self):
        from gateway.secretary_skills_store import read_env_value, write_env_value
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp)
            write_env_value(home, "K", "first")
            write_env_value(home, "K", "second")
            assert read_env_value(home, "K") == "second"
            # no duplicate lines
            lines = [l for l in (home / ".env").read_text().splitlines() if l.strip() and l.startswith("K=")]
            assert len(lines) == 1

    def test_quote_special_chars(self):
        from gateway.secretary_skills_store import read_env_value, write_env_value
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp)
            write_env_value(home, "P", "p@ss w0rd #1")
            assert read_env_value(home, "P") == "p@ss w0rd #1"

    def test_read_missing(self):
        from gateway.secretary_skills_store import read_env_value
        with tempfile.TemporaryDirectory() as tmp:
            assert read_env_value(Path(tmp), "NOPE") is None

    def test_env_0600_perms(self):
        import stat
        from gateway.secretary_skills_store import env_path, write_env_value
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp)
            write_env_value(home, "K", "v")
            mode = stat.S_IMODE(env_path(home).stat().st_mode)
            assert mode == 0o600

    def test_preserves_unrelated_lines(self):
        from gateway.secretary_skills_store import read_env_value, write_env_value
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp)
            (home / ".env").write_text("# comment\nOTHER=keep\n", encoding="utf-8")
            write_env_value(home, "NEW", "val")
            assert read_env_value(home, "OTHER") == "keep"
            assert read_env_value(home, "NEW") == "val"


# ── Wizard step rendering (_setup_step_view) ───────────────────


class TestSetupStepView:
    def test_text_field_prompt(self):
        adapter = _make_adapter()
        with tempfile.TemporaryDirectory() as tmp:
            state = {"skill_id": "mail", "field_index": 0, "draft": {}, "await_text": False}
            text, markup = adapter._setup_step_view(state, Path(tmp))
        assert "1/3" in text
        assert "email" in text.lower()
        rows = _buttons(markup)
        assert rows[-1] == [("↩ Отмена", "skill:cancel")]

    def test_choice_field_buttons(self):
        adapter = _make_adapter()
        with tempfile.TemporaryDirectory() as tmp:
            state = {"skill_id": "mail", "field_index": 2, "draft": {}, "await_text": False}
            text, markup = adapter._setup_step_view(state, Path(tmp))
        rows = _buttons(markup)
        cb = [r[0][1] for r in rows if r[0][0] in ("gmail", "mailru", "yandex", "other")]
        assert cb == [
            "skill:choice:mail:gmail", "skill:choice:mail:mailru",
            "skill:choice:mail:yandex", "skill:choice:mail:other",
        ]

    def test_secret_already_set_keep_replace(self):
        adapter = _make_adapter()
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp)
            from gateway.secretary_skills_store import write_env_value
            write_env_value(home, "SECRETARY_MAIL_PASSWORD", "existing")
            state = {"skill_id": "mail", "field_index": 1, "draft": {}, "await_text": False}
            text, markup = adapter._setup_step_view(state, home)
        rows = _buttons(markup)
        flat = [b for row in rows for b in row]
        assert ("Оставить", "skill:keep:mail") in flat
        assert ("Заменить", "skill:replace:mail") in flat

    def test_choice_other_awaits_text(self):
        adapter = _make_adapter()
        with tempfile.TemporaryDirectory() as tmp:
            state = {"skill_id": "mail", "field_index": 2, "draft": {}, "await_text": True}
            text, markup = adapter._setup_step_view(state, Path(tmp))
        rows = _buttons(markup)
        # no choice buttons — only cancel
        assert len(rows) == 1
        assert rows[0] == [("↩ Отмена", "skill:cancel")]


# ── Wizard transitions ─────────────────────────────────────────


class TestSetupFinish:
    def test_finish_writes_env_and_clears(self):
        adapter = _make_adapter()
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp)
            state = {
                "skill_id": "mail", "field_index": 3,
                "draft": {
                    "SECRETARY_MAIL_EMAIL": "a@b.com",
                    "SECRETARY_MAIL_PASSWORD": "secret",
                    "SECRETARY_MAIL_IMAP_HOST": "imap.gmail.com",
                },
                "await_text": False,
            }
            with patch("gateway.secretary_user_store.set_pref") as set_pref:
                text, markup = adapter._setup_finish("123", state, home)
            set_pref.assert_called_once_with("123", "awaiting_skill_setup", None)
            from gateway.secretary_skills_store import read_env_value
            assert read_env_value(home, "SECRETARY_MAIL_EMAIL") == "a@b.com"
            assert read_env_value(home, "SECRETARY_MAIL_IMAP_HOST") == "imap.gmail.com"
            assert "сохранены" in text


class TestSetupFieldAction:
    @pytest.mark.asyncio
    async def test_choice_advances(self):
        adapter = _make_adapter()
        query = _make_query()
        state = {"skill_id": "mail", "field_index": 2, "draft": {}, "await_text": False}
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp)
            with patch("gateway.secretary_router.get_active_profile_path", return_value=home), \
                 patch("gateway.secretary_user_store.get_pref", return_value=state) as get_pref, \
                 patch("gateway.secretary_user_store.set_pref") as set_pref:
                await adapter._setup_field_action(query, "123", "choice", "mail", "gmail")

            # choice maps to hostname and advances to done → finish writes env
            from gateway.secretary_skills_store import read_env_value
            assert read_env_value(home, "SECRETARY_MAIL_IMAP_HOST") == "imap.gmail.com"
        # last set_pref call cleared awaiting (finish) → value None
        assert set_pref.call_args_list[-1].args[2] is None

    @pytest.mark.asyncio
    async def test_choice_other_sets_await_text(self):
        adapter = _make_adapter()
        query = _make_query()
        state = {"skill_id": "mail", "field_index": 2, "draft": {}, "await_text": False}
        with tempfile.TemporaryDirectory() as tmp:
            with patch("gateway.secretary_router.get_active_profile_path", return_value=Path(tmp)), \
                 patch("gateway.secretary_user_store.get_pref", return_value=state), \
                 patch("gateway.secretary_user_store.set_pref") as set_pref:
                await adapter._setup_field_action(query, "123", "choice", "mail", "other")
        # stays on same field, await_text=True, no finish
        assert set_pref.call_args_list[-1].args[2]["await_text"] is True
        assert set_pref.call_args_list[-1].args[2]["field_index"] == 2

    @pytest.mark.asyncio
    async def test_keep_advances_without_writing(self):
        adapter = _make_adapter()
        query = _make_query()
        state = {"skill_id": "mail", "field_index": 1, "draft": {"SECRETARY_MAIL_EMAIL": "a@b.com"}, "await_text": False}
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp)
            from gateway.secretary_skills_store import write_env_value
            write_env_value(home, "SECRETARY_MAIL_PASSWORD", "keep-me")
            with patch("gateway.secretary_router.get_active_profile_path", return_value=home), \
                 patch("gateway.secretary_user_store.get_pref", return_value=state), \
                 patch("gateway.secretary_user_store.set_pref") as set_pref:
                await adapter._setup_field_action(query, "123", "keep", "mail", "")

            from gateway.secretary_skills_store import read_env_value
            assert read_env_value(home, "SECRETARY_MAIL_PASSWORD") == "keep-me"  # unchanged


class TestSetupCancel:
    @pytest.mark.asyncio
    async def test_cancel_disables_and_clears(self):
        adapter = _make_adapter()
        query = _make_query()
        state = {"skill_id": "mail", "field_index": 1, "draft": {}, "await_text": False}
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp)
            with patch("gateway.secretary_router.get_active_profile_path", return_value=home), \
                 patch("gateway.secretary_user_store.get_pref", return_value=state), \
                 patch("gateway.secretary_user_store.set_pref") as set_pref, \
                 patch("gateway.secretary_router.get_active", return_value="secretary-test"), \
                 patch("gateway.secretary_skills_store.load_state", return_value={}):
                await adapter._setup_cancel(query, "123")

            from gateway.secretary_skills_store import get_skill_state
            assert get_skill_state(home, "mail")["enabled"] is False
        set_pref.assert_called_with("123", "awaiting_skill_setup", None)


class TestSetupTextIntercept:
    def _msg(self, text, chat_id="456"):
        msg = SimpleNamespace()
        msg.text = text
        msg.chat = SimpleNamespace(id=chat_id)
        msg.from_user = SimpleNamespace(id="123")
        msg.message_id = 1
        msg.message_thread_id = None
        return msg

    @pytest.mark.asyncio
    async def test_accepts_field_and_advances(self):
        adapter = _make_adapter()
        msg = self._msg("a@b.com")
        state = {"skill_id": "mail", "field_index": 0, "draft": {}, "await_text": False}
        with tempfile.TemporaryDirectory() as tmp:
            with patch("gateway.secretary_router.get_active_profile_path", return_value=Path(tmp)), \
                 patch("gateway.secretary_user_store.get_pref", return_value=state), \
                 patch("gateway.secretary_user_store.set_pref") as set_pref:
                handled = await adapter._handle_skill_setup_text(msg, "a@b.com")
        assert handled is True
        # advanced to field 1, draft has email
        saved_state = set_pref.call_args_list[0].args[2]
        assert saved_state["field_index"] == 1
        assert saved_state["draft"]["SECRETARY_MAIL_EMAIL"] == "a@b.com"

    @pytest.mark.asyncio
    async def test_command_passes_through(self):
        adapter = _make_adapter()
        msg = self._msg("/menu")
        state = {"skill_id": "mail", "field_index": 0, "draft": {}, "await_text": False}
        with patch("gateway.secretary_user_store.get_pref", return_value=state):
            handled = await adapter._handle_skill_setup_text(msg, "/menu")
        assert handled is False

    @pytest.mark.asyncio
    async def test_not_awaiting_passes_through(self):
        adapter = _make_adapter()
        msg = self._msg("random")
        with patch("gateway.secretary_user_store.get_pref", return_value=None):
            handled = await adapter._handle_skill_setup_text(msg, "random")
        assert handled is False


class TestSkillCallbackRouting:
    @pytest.mark.asyncio
    async def test_on_requires_setup_opens_wizard(self):
        adapter = _make_adapter()
        query = _make_query()
        with tempfile.TemporaryDirectory() as tmp:
            with patch("gateway.secretary_router.get_active_profile_path", return_value=Path(tmp)), \
                 patch("gateway.secretary_user_store.set_pref") as set_pref:
                await adapter._handle_skill_callback(query, "skill:on:mail", "456", None, "Test")
        # armed the wizard with field_index 0
        assert set_pref.call_args_list[0].args == ("123", "awaiting_skill_setup", {"skill_id": "mail", "field_index": 0, "draft": {}, "await_text": False})
        # edited to wizard step
        query.edit_message_text.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_validate_is_stub(self):
        adapter = _make_adapter()
        query = _make_query()
        await adapter._handle_skill_callback(query, "skill:validate:mail", "456", None, "Test")
        assert "L4" in query.answer.call_args.kwargs.get("text", "")

    @pytest.mark.asyncio
    async def test_cancel_routes(self):
        adapter = _make_adapter()
        query = _make_query()
        with patch("gateway.secretary_user_store.get_pref", return_value=None), \
             patch("gateway.secretary_user_store.set_pref"), \
             patch("gateway.secretary_router.get_active", return_value="secretary-test"), \
             patch("gateway.secretary_skills_store.load_state", return_value={}):
            await adapter._handle_skill_callback(query, "skill:cancel", "456", None, "Test")
        assert "отменена" in query.answer.call_args.kwargs.get("text", "")
