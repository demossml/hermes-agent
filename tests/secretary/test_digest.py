"""
Unit tests for gateway/secretary_digest.py and user store digest functions.
"""

from __future__ import annotations

import tempfile
import shutil
from pathlib import Path
from unittest.mock import patch


def _setup_temp_store():
    tmp = tempfile.mkdtemp(prefix="digest_test_")
    db_path = Path(tmp) / "secretary_users.db"
    import gateway.secretary_user_store as store
    orig = store._db_path
    store._db_path = lambda: db_path
    if hasattr(store._tls, "conn") and store._tls.conn is not None:
        store._tls.conn.close()
        store._tls.conn = None
    return tmp, db_path, orig


def _cleanup_temp_store(tmp, orig):
    import gateway.secretary_user_store as store
    store._db_path = orig
    if hasattr(store._tls, "conn") and store._tls.conn is not None:
        store._tls.conn.close()
        store._tls.conn = None
    shutil.rmtree(tmp, ignore_errors=True)


class TestDigestStore:
    """Tests for digest-related user store functions."""

    def test_should_send_digest_new_user(self):
        import gateway.secretary_user_store as store
        tmp, db_path, orig = _setup_temp_store()
        try:
            store._db_path = lambda: db_path
            if hasattr(store._tls, "conn") and store._tls.conn is not None:
                store._tls.conn.close()
                store._tls.conn = None

            uid = "digest_new"
            # Fresh user — not onboarded
            assert not store.should_send_digest(uid)

            # Onboarded but digest disabled
            store.upsert_user(uid, onboarding_step="done", digest_enabled=False)
            assert not store.should_send_digest(uid)

            # Onboarded, digest enabled, no last_digest_date → should send
            store.upsert_user(uid, digest_enabled=True)
            assert store.should_send_digest(uid)
        finally:
            _cleanup_temp_store(tmp, orig)

    def test_mark_digest_sent(self):
        import gateway.secretary_user_store as store
        from datetime import datetime, timezone

        tmp, db_path, orig = _setup_temp_store()
        try:
            store._db_path = lambda: db_path
            if hasattr(store._tls, "conn") and store._tls.conn is not None:
                store._tls.conn.close()
                store._tls.conn = None

            uid = "digest_mark"
            store.upsert_user(uid, onboarding_step="done", digest_enabled=True)

            # Should send before marking
            assert store.should_send_digest(uid)

            # Mark sent
            store.mark_digest_sent(uid)
            today = datetime.now(timezone.utc).date().isoformat()
            assert store.get_user(uid)["last_digest_date"] == today

            # Should NOT send again today
            assert not store.should_send_digest(uid)
        finally:
            _cleanup_temp_store(tmp, orig)

    def test_get_digest_users(self):
        import gateway.secretary_user_store as store

        tmp, db_path, orig = _setup_temp_store()
        try:
            store._db_path = lambda: db_path
            if hasattr(store._tls, "conn") and store._tls.conn is not None:
                store._tls.conn.close()
                store._tls.conn = None

            # User 1: onboarded, digest on → should appear
            store.upsert_user("u1", onboarding_step="done", digest_enabled=True, display_name="U1")
            # User 2: onboarded, digest off → should NOT appear
            store.upsert_user("u2", onboarding_step="done", digest_enabled=False, display_name="U2")
            # User 3: not onboarded → should NOT appear
            store.upsert_user("u3", onboarding_step="welcome", digest_enabled=True, display_name="U3")
            # User 4: onboarded, digest on → should appear
            store.upsert_user("u4", onboarding_step="done", digest_enabled=True, display_name="U4")

            users = store.get_digest_users()
            ids = {u["telegram_id"] for u in users}
            assert "u1" in ids
            assert "u2" not in ids
            assert "u3" not in ids
            assert "u4" in ids
            assert len(ids) == 2
        finally:
            _cleanup_temp_store(tmp, orig)


class TestBuildDigestText:
    """build_digest_text() tests."""

    def test_returns_empty_when_not_onboarded(self):
        import gateway.secretary_user_store as store

        tmp, db_path, orig = _setup_temp_store()
        try:
            store._db_path = lambda: db_path
            if hasattr(store._tls, "conn") and store._tls.conn is not None:
                store._tls.conn.close()
                store._tls.conn = None

            result = _build_digest_text("no_such_user")
            assert result == ""
        finally:
            _cleanup_temp_store(tmp, orig)

    def test_returns_empty_when_already_sent(self):
        import gateway.secretary_user_store as store

        tmp, db_path, orig = _setup_temp_store()
        try:
            store._db_path = lambda: db_path
            if hasattr(store._tls, "conn") and store._tls.conn is not None:
                store._tls.conn.close()
                store._tls.conn = None

            uid = "digest_sent_today"
            store.upsert_user(uid, onboarding_step="done", digest_enabled=True, display_name="Test")
            store.mark_digest_sent(uid)

            result = _build_digest_text(uid)
            assert result == ""
        finally:
            _cleanup_temp_store(tmp, orig)

    def test_returns_greeting_with_mail_placeholder(self):
        import gateway.secretary_user_store as store

        tmp, db_path, orig = _setup_temp_store()
        try:
            store._db_path = lambda: db_path
            if hasattr(store._tls, "conn") and store._tls.conn is not None:
                store._tls.conn.close()
                store._tls.conn = None

            uid = "digest_greeting"
            store.upsert_user(uid, onboarding_step="done", digest_enabled=True, display_name="Alice")

            result = _build_digest_text(uid)
            assert "Доброе утро" in result
            assert "Alice" in result
            # Mail is not configured in tests, so expect placeholder
            assert "Почта" in result
        finally:
            _cleanup_temp_store(tmp, orig)


def _build_digest_text(telegram_id: str) -> str:
    """Import under test — must be called after store setup."""
    from gateway.secretary_digest import build_digest_text
    return build_digest_text(telegram_id)
