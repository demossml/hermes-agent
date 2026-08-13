"""
Tests: secretary profile lifecycle from Telegram (L1–L3).

Covers:
  - slug normalization/validation (_normalize_secretary_slug)
  - prefs helpers (get_pref / set_pref / clear_pref)
  - router unset_active_for_profile (stale pointer cleanup)
  - picker / delete-view keyboard construction (actions + empty case)
  - sec:* callback routing (create / delete / del / confirm_del / cancel)
  - delete blocked when the target profile is active
  - create flow text intercept (success / duplicate / bad slug)
  - cancel clears the awaiting flag

Run: venv/bin/python -m pytest tests/test_secretary_lifecycle.py -q
"""

from __future__ import annotations

import tempfile
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest


# ── Fake telegram inline-keyboard types ───────────────────────
# python-telegram-bot is not always installed in the test venv; when absent
# the adapter binds InlineKeyboardButton/InlineKeyboardMarkup to `Any`, which
# raises "Any cannot be instantiated". Patch lightweight stand-ins so the
# picker/delete-view builders can be exercised without the real library.

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


# ── Slug validation ───────────────────────────────────────────


class TestNormalizeSecretarySlug:
    def _normalize(self, text):
        from gateway.platforms.telegram import _normalize_secretary_slug
        return _normalize_secretary_slug(text)

    def test_lowercase_trim(self):
        assert self._normalize("  Ceh  ") == "ceh"

    def test_strips_secretary_prefix(self):
        assert self._normalize("secretary-HR") == "hr"
        assert self._normalize("secretary-ceh") == "ceh"

    def test_valid_chars(self):
        assert self._normalize("hr") == "hr"
        assert self._normalize("logist-2") == "logist-2"
        assert self._normalize("hr_v2") == "hr_v2"

    def test_invalid_path_traversal(self):
        assert self._normalize("bad/../slug") == ""
        assert self._normalize("../etc") == ""
        assert self._normalize("a\\b") == ""

    def test_invalid_leading(self):
        assert self._normalize("-abc") == ""
        assert self._normalize("_abc") == ""

    def test_invalid_chars(self):
        assert self._normalize("слаш") == ""  # cyrillic
        assert self._normalize("hello world") == ""
        assert self._normalize("a.b") == ""

    def test_empty(self):
        assert self._normalize("") == ""
        assert self._normalize("   ") == ""

    def test_too_long(self):
        assert self._normalize("a" * 65) == ""

    def test_single_char(self):
        assert self._normalize("a") == "a"


# ── prefs helpers ─────────────────────────────────────────────


def _setup_temp_store():
    import gateway.secretary_user_store as store

    tmp = tempfile.mkdtemp(prefix="sec_lifecycle_store_")
    db_path = Path(tmp) / "secretary_users.db"
    orig_db_path = store._db_path

    def _temp_db_path():
        return db_path

    store._db_path = _temp_db_path
    if hasattr(store._tls, "conn") and store._tls.conn is not None:
        try:
            store._tls.conn.close()
        except Exception:
            pass
        store._tls.conn = None
    return tmp, db_path, orig_db_path


def _cleanup_temp_store(tmp, orig_db_path):
    import shutil
    import gateway.secretary_user_store as store

    store._db_path = orig_db_path
    if hasattr(store._tls, "conn") and store._tls.conn is not None:
        try:
            store._tls.conn.close()
        except Exception:
            pass
        store._tls.conn = None
    shutil.rmtree(tmp, ignore_errors=True)


class TestPrefsHelpers:
    def test_get_pref_default(self):
        import gateway.secretary_user_store as store

        tmp, db_path, orig = _setup_temp_store()
        try:
            assert store.get_pref("nobody", "awaiting_secretary_name") is None
            assert store.get_pref("nobody", "x", default=False) is False
        finally:
            _cleanup_temp_store(tmp, orig)

    def test_set_and_get_pref(self):
        import gateway.secretary_user_store as store

        tmp, db_path, orig = _setup_temp_store()
        try:
            store.set_pref("123", "awaiting_secretary_name", True)
            assert store.get_pref("123", "awaiting_secretary_name") is True
            # set_pref creates the user record if absent
            assert store.get_user("123") is not None
        finally:
            _cleanup_temp_store(tmp, orig)

    def test_clear_pref(self):
        import gateway.secretary_user_store as store

        tmp, db_path, orig = _setup_temp_store()
        try:
            store.set_pref("123", "awaiting_secretary_name", True)
            store.clear_pref("123", "awaiting_secretary_name")
            assert store.get_pref("123", "awaiting_secretary_name") is None
        finally:
            _cleanup_temp_store(tmp, orig)

    def test_prefs_do_not_collide(self):
        import gateway.secretary_user_store as store

        tmp, db_path, orig = _setup_temp_store()
        try:
            store.set_pref("1", "a", 1)
            store.set_pref("2", "b", 2)
            assert store.get_pref("1", "a") == 1
            assert store.get_pref("1", "b") is None
            assert store.get_pref("2", "b") == 2
        finally:
            _cleanup_temp_store(tmp, orig)


# ── router unset_active_for_profile ───────────────────────────


class TestRouterUnsetActive:
    def test_clears_stale_pointers(self):
        import gateway.secretary_router as router

        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp)
            (home / "profiles" / "secretary-acme").mkdir(parents=True)
            (home / "profiles" / "secretary-beta").mkdir(parents=True)

            with patch.object(router, "_hermes_home", return_value=home):
                router.set_active("111", "secretary-acme")
                router.set_active("222", "secretary-acme")
                router.set_active("333", "secretary-beta")

                assert router.get_active("111") == "secretary-acme"

                affected = router.unset_active_for_profile("secretary-acme")
                assert affected == 2

                # stale pointers now fall back to default
                assert router.get_active("111") == "default"
                assert router.get_active("222") == "default"
                # unaffected user keeps their profile
                assert router.get_active("333") == "secretary-beta"

    def test_no_match_returns_zero(self):
        import gateway.secretary_router as router

        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp)
            (home / "profiles" / "secretary-acme").mkdir(parents=True)
            with patch.object(router, "_hermes_home", return_value=home):
                assert router.unset_active_for_profile("secretary-nope") == 0


# ── picker / delete-view keyboards ─────────────────────────────


def _make_adapter():
    from gateway.platforms.telegram import TelegramAdapter

    adapter = TelegramAdapter.__new__(TelegramAdapter)
    adapter._disable_link_previews = False
    adapter._reply_to_mode = "off"
    adapter._bot = MagicMock()
    adapter._bot.send_message = AsyncMock()
    return adapter


def _button_rows(markup):
    """Return [(text, callback_data), ...] rows for an InlineKeyboardMarkup."""
    rows = []
    for row in markup.inline_keyboard:
        rows.append([(b.text, b.callback_data) for b in row])
    return rows


class TestSecretaryPickerView:
    def test_empty_case_shows_create_first(self):
        adapter = _make_adapter()
        with patch("gateway.secretary_router.list_secretaries", return_value=["default"]):
            text, markup = adapter._secretary_picker_view("default")
        assert "Создайте первого" in text
        rows = _button_rows(markup)
        assert len(rows) == 1
        assert rows[0][0] == ("➕ Создать первого секретаря", "sec:create")

    def test_profiles_plus_action_row(self):
        adapter = _make_adapter()
        with patch(
            "gateway.secretary_router.list_secretaries",
            return_value=["default", "secretary-ceh", "secretary-hr"],
        ):
            text, markup = adapter._secretary_picker_view("secretary-ceh")
        rows = _button_rows(markup)
        # profile rows (default + 2 secretary) + action row
        assert len(rows) == 4
        # active profile is marked with ✓
        assert rows[1][0][0] == "secretary-ceh ✓"
        assert rows[2][0][0] == "secretary-hr"
        # action row
        assert rows[3][0] == ("➕ Создать", "sec:create")
        assert rows[3][1] == ("🗑 Удалить", "sec:delete")

    def test_delete_view_excludes_default_and_marks_active(self):
        adapter = _make_adapter()
        with patch(
            "gateway.secretary_router.list_secretaries",
            return_value=["default", "secretary-ceh", "secretary-hr"],
        ):
            text, markup = adapter._secretary_delete_view("secretary-hr")
        rows = _button_rows(markup)
        # only secretary-* profiles (no default) + cancel row
        assert len(rows) == 3
        assert rows[0][0] == ("secretary-ceh", "sec:del:secretary-ceh")
        assert rows[1][0] == ("secretary-hr (активен)", "sec:del:secretary-hr")
        assert rows[2][0] == ("↩ Отмена", "sec:cancel")

    def test_delete_view_empty(self):
        adapter = _make_adapter()
        with patch("gateway.secretary_router.list_secretaries", return_value=["default"]):
            text, markup = adapter._secretary_delete_view("default")
        rows = _button_rows(markup)
        assert len(rows) == 1
        assert rows[0][0] == ("↩ Отмена", "sec:cancel")


# ── callback routing ──────────────────────────────────────────


def _make_query(chat_type="private"):
    query = SimpleNamespace()
    query.from_user = SimpleNamespace(id="123", first_name="Test")
    query.message = SimpleNamespace(
        chat=SimpleNamespace(type=chat_type),
        chat_id="456",
        message_thread_id=None,
    )
    query.answer = AsyncMock()
    query.edit_message_text = AsyncMock()
    return query


class TestSecCallbackRouting:
    def _adapter(self):
        adapter = _make_adapter()
        adapter._is_callback_user_authorized = MagicMock(return_value=True)
        adapter._sec_create_flow = AsyncMock()
        adapter._sec_delete_picker = AsyncMock()
        adapter._sec_del_confirm = AsyncMock()
        adapter._sec_confirm_delete = AsyncMock()
        adapter._sec_cancel = AsyncMock()
        return adapter

    @pytest.mark.asyncio
    async def test_create(self):
        adapter = self._adapter()
        query = _make_query()
        await adapter._handle_sec_callback(query, "sec:create", "456", None, "Test")
        adapter._sec_create_flow.assert_awaited_once_with(query, "123", "private")

    @pytest.mark.asyncio
    async def test_delete(self):
        adapter = self._adapter()
        query = _make_query()
        await adapter._handle_sec_callback(query, "sec:delete", "456", None, "Test")
        adapter._sec_delete_picker.assert_awaited_once_with(query, "123")

    @pytest.mark.asyncio
    async def test_cancel(self):
        adapter = self._adapter()
        query = _make_query()
        await adapter._handle_sec_callback(query, "sec:cancel", "456", None, "Test")
        adapter._sec_cancel.assert_awaited_once_with(query, "123")

    @pytest.mark.asyncio
    async def test_del(self):
        adapter = self._adapter()
        query = _make_query()
        await adapter._handle_sec_callback(query, "sec:del:secretary-foo", "456", None, "Test")
        adapter._sec_del_confirm.assert_awaited_once_with(query, "123", "secretary-foo")

    @pytest.mark.asyncio
    async def test_confirm_del(self):
        adapter = self._adapter()
        query = _make_query()
        await adapter._handle_sec_callback(
            query, "sec:confirm_del:secretary-foo", "456", None, "Test"
        )
        adapter._sec_confirm_delete.assert_awaited_once_with(query, "123", "secretary-foo")

    @pytest.mark.asyncio
    async def test_unauthorized_is_rejected(self):
        adapter = _make_adapter()
        adapter._is_callback_user_authorized = MagicMock(return_value=False)
        adapter._sec_create_flow = AsyncMock()
        query = _make_query()
        await adapter._handle_sec_callback(query, "sec:create", "456", None, "Test")
        adapter._sec_create_flow.assert_not_awaited()
        query.answer.assert_awaited_once()


# ── delete blocked when active ────────────────────────────────


class TestDeleteBlockedWhenActive:
    def _adapter(self):
        return _make_adapter()

    @pytest.mark.asyncio
    async def test_del_confirm_blocks_active(self):
        adapter = self._adapter()
        query = _make_query()
        with patch("gateway.secretary_router.get_active", return_value="secretary-foo"), \
             patch("gateway.secretary_router.validate_profile", return_value=True):
            await adapter._sec_del_confirm(query, "123", "secretary-foo")
        # answered with an alert, did NOT show the confirm keyboard
        assert query.answer.await_count == 1
        alert_text = query.answer.call_args.kwargs.get("text", "")
        assert "активн" in alert_text
        query.edit_message_text.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_confirm_delete_blocks_active(self):
        adapter = self._adapter()
        query = _make_query()
        with patch("gateway.secretary_router.get_active", return_value="secretary-foo"), \
             patch("gateway.secretary_router.validate_profile", return_value=True), \
             patch("hermes_cli.profiles.delete_profile") as dp:
            await adapter._sec_confirm_delete(query, "123", "secretary-foo")
        dp.assert_not_called()
        query.edit_message_text.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_confirm_delete_blocks_default(self):
        adapter = self._adapter()
        query = _make_query()
        with patch("hermes_cli.profiles.delete_profile") as dp:
            await adapter._sec_confirm_delete(query, "123", "default")
        dp.assert_not_called()


# ── create flow text intercept ────────────────────────────────


def _make_msg(text, user_id="123", chat_id="456"):
    msg = SimpleNamespace()
    msg.text = text
    msg.chat = SimpleNamespace(id=chat_id)
    msg.from_user = SimpleNamespace(id=user_id)
    msg.message_id = 1
    msg.message_thread_id = None
    return msg


class TestCreateFlowText:
    def _adapter(self):
        adapter = _make_adapter()
        adapter._handle_secretary_picker_locally = AsyncMock()
        return adapter

    @pytest.mark.asyncio
    async def test_create_success(self):
        adapter = self._adapter()
        msg = _make_msg("ceh")
        with patch("gateway.secretary_user_store.get_pref", return_value=True), \
             patch("gateway.secretary_user_store.set_pref") as set_pref, \
             patch("gateway.secretary_router.validate_profile", return_value=False), \
             patch("gateway.secretary_router.set_active") as set_active, \
             patch("hermes_cli.profiles.create_profile") as create_profile:
            handled = await adapter._handle_secretary_name_text(msg, "ceh")

        assert handled is True
        create_profile.assert_called_once_with("secretary-ceh", no_skills=True)
        set_active.assert_called_once_with("123", "secretary-ceh")
        # awaiting flag cleared
        set_pref.assert_called_once_with("123", "awaiting_secretary_name", None)
        # updated picker re-shown
        adapter._handle_secretary_picker_locally.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_create_strips_prefix(self):
        adapter = self._adapter()
        msg = _make_msg("secretary-ceh")
        with patch("gateway.secretary_user_store.get_pref", return_value=True), \
             patch("gateway.secretary_user_store.set_pref"), \
             patch("gateway.secretary_router.validate_profile", return_value=False), \
             patch("gateway.secretary_router.set_active"), \
             patch("hermes_cli.profiles.create_profile") as create_profile:
            await adapter._handle_secretary_name_text(msg, "secretary-ceh")
        create_profile.assert_called_once_with("secretary-ceh", no_skills=True)

    @pytest.mark.asyncio
    async def test_duplicate_refuses_and_keeps_state(self):
        adapter = self._adapter()
        msg = _make_msg("ceh")
        with patch("gateway.secretary_user_store.get_pref", return_value=True), \
             patch("gateway.secretary_user_store.set_pref") as set_pref, \
             patch("gateway.secretary_router.validate_profile", return_value=True), \
             patch("hermes_cli.profiles.create_profile") as create_profile:
            handled = await adapter._handle_secretary_name_text(msg, "ceh")

        assert handled is True
        create_profile.assert_not_called()
        # state NOT reset — user can retry
        set_pref.assert_not_called()
        # "already exists" message sent
        sent = adapter._bot.send_message.await_args
        assert "существует" in sent.kwargs["text"]

    @pytest.mark.asyncio
    async def test_bad_slug_refuses(self):
        adapter = self._adapter()
        msg = _make_msg("../bad")
        with patch("gateway.secretary_user_store.get_pref", return_value=True), \
             patch("gateway.secretary_user_store.set_pref"), \
             patch("hermes_cli.profiles.create_profile") as create_profile:
            handled = await adapter._handle_secretary_name_text(msg, "../bad")
        assert handled is True
        create_profile.assert_not_called()
        sent = adapter._bot.send_message.await_args
        assert "slug" in sent.kwargs["text"].lower()

    @pytest.mark.asyncio
    async def test_not_awaiting_passes_through(self):
        adapter = self._adapter()
        msg = _make_msg("ceh")
        with patch("gateway.secretary_user_store.get_pref", return_value=False):
            handled = await adapter._handle_secretary_name_text(msg, "ceh")
        assert handled is False

    @pytest.mark.asyncio
    async def test_command_passes_through(self):
        adapter = self._adapter()
        msg = _make_msg("/menu")
        with patch("gateway.secretary_user_store.get_pref", return_value=True):
            handled = await adapter._handle_secretary_name_text(msg, "/menu")
        assert handled is False


# ── cancel clears awaiting flag ───────────────────────────────


class TestCancelClearsAwaiting:
    @pytest.mark.asyncio
    async def test_cancel_clears_pref_and_returns_to_picker(self):
        adapter = _make_adapter()
        query = _make_query()
        with patch("gateway.secretary_user_store.set_pref") as set_pref, \
             patch("gateway.secretary_router.get_active", return_value="default"), \
             patch("gateway.secretary_router.list_secretaries", return_value=["default"]):
            await adapter._sec_cancel(query, "123")

        set_pref.assert_called_once_with("123", "awaiting_secretary_name", None)
        # returned to picker (edited message)
        query.edit_message_text.assert_awaited_once()


# ── create flow group guard ───────────────────────────────────


class TestCreateFlowGroupGuard:
    @pytest.mark.asyncio
    async def test_create_in_group_is_blocked(self):
        adapter = _make_adapter()
        query = _make_query(chat_type="group")
        with patch("gateway.secretary_user_store.set_pref") as set_pref:
            await adapter._sec_create_flow(query, "123", "group")
        # no awaiting flag armed
        set_pref.assert_not_called()
        # alerted the user
        alert_text = query.answer.call_args.kwargs.get("text", "")
        assert "личном" in alert_text

    @pytest.mark.asyncio
    async def test_create_in_private_arms_flag(self):
        adapter = _make_adapter()
        query = _make_query(chat_type="private")
        with patch("gateway.secretary_user_store.set_pref") as set_pref:
            await adapter._sec_create_flow(query, "123", "private")
        set_pref.assert_called_once_with("123", "awaiting_secretary_name", True)
        query.edit_message_text.assert_awaited_once()
