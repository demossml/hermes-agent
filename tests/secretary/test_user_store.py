"""
Unit tests for gateway/secretary_user_store.py

Run: python3 -m pytest tests/secretary/test_user_store.py -v
"""

from __future__ import annotations

import json
import sqlite3
import tempfile
from pathlib import Path
from unittest.mock import patch


def _setup_temp_store():
    """Patch _db_path to use a temp file. Returns (temp dir, original)."""
    tmp = tempfile.mkdtemp(prefix="sec_users_test_")
    db_path = Path(tmp) / "secretary_users.db"

    orig = None
    try:
        from gateway.secretary_user_store import _db_path as _orig_db_path
        orig = _orig_db_path
    except Exception:
        pass

    def _temp_db_path():
        return db_path

    return tmp, db_path, orig


def _cleanup_temp_store(tmp, orig):
    """Restore original and clean up temp dir."""
    import shutil
    import gateway.secretary_user_store as mod

    if orig is not None:
        mod._db_path = orig  # type: ignore[attr-defined]
    # Reset thread-local connection
    if hasattr(mod._tls, "conn") and mod._tls.conn is not None:
        try:
            mod._tls.conn.close()
        except Exception:
            pass
        mod._tls.conn = None
    shutil.rmtree(tmp, ignore_errors=True)


# ── Tests ─────────────────────────────────────────────────────


class TestGetUser:
    """get_user() tests."""

    def test_get_nonexistent(self):
        """get_user returns None for unknown ID."""
        import gateway.secretary_user_store as store

        tmp, db_path, orig = _setup_temp_store()
        try:
            store._db_path = lambda: db_path
            # Reset connection so it reconnects to temp DB
            if hasattr(store._tls, "conn") and store._tls.conn is not None:
                store._tls.conn.close()
                store._tls.conn = None

            result = store.get_user("123456")
            assert result is None
        finally:
            _cleanup_temp_store(tmp, orig)

    def test_get_existing(self):
        """get_user returns full dict for known ID."""
        import gateway.secretary_user_store as store

        tmp, db_path, orig = _setup_temp_store()
        try:
            store._db_path = lambda: db_path
            if hasattr(store._tls, "conn") and store._tls.conn is not None:
                store._tls.conn.close()
                store._tls.conn = None

            store.upsert_user("123456", display_name="Alice")
            result = store.get_user("123456")

            assert result is not None
            assert result["telegram_id"] == "123456"
            assert result["display_name"] == "Alice"
            assert result["timezone"] == "Europe/Moscow"
            assert result["digest_enabled"] == 1
            assert result["digest_hour"] == 9
            assert result["onboarding_step"] is None
            assert result["prefs"] == {}
            assert "updated_at" in result
        finally:
            _cleanup_temp_store(tmp, orig)

    def test_get_empty_id(self):
        """get_user returns None for empty/whitespace ID."""
        import gateway.secretary_user_store as store

        tmp, db_path, orig = _setup_temp_store()
        try:
            store._db_path = lambda: db_path
            assert store.get_user("") is None
            assert store.get_user("   ") is None
        finally:
            _cleanup_temp_store(tmp, orig)


class TestUpsertUser:
    """upsert_user() tests."""

    def test_create_new_user(self):
        """upsert_user creates a new record."""
        import gateway.secretary_user_store as store

        tmp, db_path, orig = _setup_temp_store()
        try:
            store._db_path = lambda: db_path
            if hasattr(store._tls, "conn") and store._tls.conn is not None:
                store._tls.conn.close()
                store._tls.conn = None

            result = store.upsert_user(
                "999",
                display_name="Bob",
                timezone="Asia/Tokyo",
                digest_enabled=False,
                digest_hour=7,
            )

            assert result["telegram_id"] == "999"
            assert result["display_name"] == "Bob"
            assert result["timezone"] == "Asia/Tokyo"
            assert result["digest_enabled"] == 0
            assert result["digest_hour"] == 7
        finally:
            _cleanup_temp_store(tmp, orig)

    def test_update_existing_user(self):
        """upsert_user updates existing record, preserves other fields."""
        import gateway.secretary_user_store as store

        tmp, db_path, orig = _setup_temp_store()
        try:
            store._db_path = lambda: db_path
            if hasattr(store._tls, "conn") and store._tls.conn is not None:
                store._tls.conn.close()
                store._tls.conn = None

            # Create with defaults
            store.upsert_user("111")
            # Update only name
            result = store.upsert_user("111", display_name="Charlie")

            assert result["display_name"] == "Charlie"
            # Unchanged defaults
            assert result["timezone"] == "Europe/Moscow"
            assert result["digest_enabled"] == 1
            assert result["digest_hour"] == 9
        finally:
            _cleanup_temp_store(tmp, orig)

    def test_update_preserves_unchanged(self):
        """Updating one field doesn't wipe others."""
        import gateway.secretary_user_store as store

        tmp, db_path, orig = _setup_temp_store()
        try:
            store._db_path = lambda: db_path
            if hasattr(store._tls, "conn") and store._tls.conn is not None:
                store._tls.conn.close()
                store._tls.conn = None

            store.upsert_user(
                "222",
                display_name="Dana",
                timezone="UTC",
                digest_hour=8,
                onboarding_step="welcome",
            )
            # Update only digest_hour
            store.upsert_user("222", digest_hour=10)

            user = store.get_user("222")
            assert user["display_name"] == "Dana"
            assert user["timezone"] == "UTC"
            assert user["digest_hour"] == 10
            assert user["onboarding_step"] == "welcome"
        finally:
            _cleanup_temp_store(tmp, orig)

    def test_digest_hour_clamped(self):
        """digest_hour is clamped to 0–23."""
        import gateway.secretary_user_store as store

        tmp, db_path, orig = _setup_temp_store()
        try:
            store._db_path = lambda: db_path
            if hasattr(store._tls, "conn") and store._tls.conn is not None:
                store._tls.conn.close()
                store._tls.conn = None

            r1 = store.upsert_user("a", digest_hour=25)
            assert r1["digest_hour"] == 23

            r2 = store.upsert_user("b", digest_hour=-5)
            assert r2["digest_hour"] == 0

            r3 = store.upsert_user("c", digest_hour="not_a_number")
            assert r3["digest_hour"] == 9  # default
        finally:
            _cleanup_temp_store(tmp, orig)

    def test_digest_enabled_coerced(self):
        """digest_enabled is coerced to 0 or 1."""
        import gateway.secretary_user_store as store

        tmp, db_path, orig = _setup_temp_store()
        try:
            store._db_path = lambda: db_path
            if hasattr(store._tls, "conn") and store._tls.conn is not None:
                store._tls.conn.close()
                store._tls.conn = None

            assert store.upsert_user("x", digest_enabled=True)["digest_enabled"] == 1
            assert store.upsert_user("x", digest_enabled=False)["digest_enabled"] == 0
            assert store.upsert_user("y", digest_enabled=0)["digest_enabled"] == 0
            assert store.upsert_user("z", digest_enabled="truthy")["digest_enabled"] == 1
        finally:
            _cleanup_temp_store(tmp, orig)

    def test_prefs_json_dict_and_string(self):
        """prefs_json accepts dict or valid JSON string."""
        import gateway.secretary_user_store as store

        tmp, db_path, orig = _setup_temp_store()
        try:
            store._db_path = lambda: db_path
            if hasattr(store._tls, "conn") and store._tls.conn is not None:
                store._tls.conn.close()
                store._tls.conn = None

            # Dict form
            store.upsert_user("p1", prefs_json={"lang": "ru", "theme": "dark"})
            u1 = store.get_user("p1")
            assert u1["prefs"] == {"lang": "ru", "theme": "dark"}

            # JSON string form
            store.upsert_user("p2", prefs_json='{"lang":"en"}')
            u2 = store.get_user("p2")
            assert u2["prefs"] == {"lang": "en"}

            # Invalid JSON → defaults to {}
            store.upsert_user("p3", prefs_json="{bad json")
            u3 = store.get_user("p3")
            assert u3["prefs"] == {}
        finally:
            _cleanup_temp_store(tmp, orig)

    def test_unknown_fields_ignored(self):
        """Unknown field names are silently ignored."""
        import gateway.secretary_user_store as store

        tmp, db_path, orig = _setup_temp_store()
        try:
            store._db_path = lambda: db_path
            if hasattr(store._tls, "conn") and store._tls.conn is not None:
                store._tls.conn.close()
                store._tls.conn = None

            result = store.upsert_user("ign", display_name="OK", evil_field="DROP TABLE")
            assert result["display_name"] == "OK"
            # evil_field should not appear anywhere
            row = store.get_user("ign")
            assert "evil_field" not in row
        finally:
            _cleanup_temp_store(tmp, orig)

    def test_empty_id_raises(self):
        """upsert_user raises ValueError for empty telegram_id."""
        import gateway.secretary_user_store as store

        tmp, db_path, orig = _setup_temp_store()
        try:
            store._db_path = lambda: db_path
            try:
                store.upsert_user("")
                assert False, "Should have raised ValueError"
            except ValueError:
                pass
            try:
                store.upsert_user("   ")
                assert False, "Should have raised ValueError"
            except ValueError:
                pass
        finally:
            _cleanup_temp_store(tmp, orig)

    def test_onboarding_step_validation(self):
        """onboarding_step only accepts valid values."""
        import gateway.secretary_user_store as store

        tmp, db_path, orig = _setup_temp_store()
        try:
            store._db_path = lambda: db_path
            if hasattr(store._tls, "conn") and store._tls.conn is not None:
                store._tls.conn.close()
                store._tls.conn = None

            # Valid steps
            for step in ["welcome", "name", "tz", "email_skip", "digest", "done"]:
                r = store.upsert_user(f"s_{step}", onboarding_step=step)
                assert r["onboarding_step"] == step

            # Invalid → None
            r = store.upsert_user("bad_step", onboarding_step="garbage")
            assert r["onboarding_step"] is None

            # None → None (explicit)
            r = store.upsert_user("none_step", onboarding_step=None)
            assert r["onboarding_step"] is None
        finally:
            _cleanup_temp_store(tmp, orig)


class TestIsOnboarded:
    """is_onboarded() tests."""

    def test_not_onboarded_new_user(self):
        """Returns False for unknown user."""
        import gateway.secretary_user_store as store

        tmp, db_path, orig = _setup_temp_store()
        try:
            store._db_path = lambda: db_path
            if hasattr(store._tls, "conn") and store._tls.conn is not None:
                store._tls.conn.close()
                store._tls.conn = None

            assert store.is_onboarded("no_such_user") is False
        finally:
            _cleanup_temp_store(tmp, orig)

    def test_not_onboarded_no_step(self):
        """Returns False when onboarding_step is NULL."""
        import gateway.secretary_user_store as store

        tmp, db_path, orig = _setup_temp_store()
        try:
            store._db_path = lambda: db_path
            if hasattr(store._tls, "conn") and store._tls.conn is not None:
                store._tls.conn.close()
                store._tls.conn = None

            store.upsert_user("nobody")
            assert store.is_onboarded("nobody") is False
        finally:
            _cleanup_temp_store(tmp, orig)

    def test_not_onboarded_mid_step(self):
        """Returns False when onboarding_step is not 'done'."""
        import gateway.secretary_user_store as store

        tmp, db_path, orig = _setup_temp_store()
        try:
            store._db_path = lambda: db_path
            if hasattr(store._tls, "conn") and store._tls.conn is not None:
                store._tls.conn.close()
                store._tls.conn = None

            for step in ["welcome", "name", "tz", "email_skip", "digest"]:
                store.upsert_user(f"mid_{step}", onboarding_step=step)
                assert store.is_onboarded(f"mid_{step}") is False
        finally:
            _cleanup_temp_store(tmp, orig)

    def test_is_onboarded(self):
        """Returns True when onboarding_step == 'done'."""
        import gateway.secretary_user_store as store

        tmp, db_path, orig = _setup_temp_store()
        try:
            store._db_path = lambda: db_path
            if hasattr(store._tls, "conn") and store._tls.conn is not None:
                store._tls.conn.close()
                store._tls.conn = None

            store.upsert_user("ready", onboarding_step="done")
            assert store.is_onboarded("ready") is True
        finally:
            _cleanup_temp_store(tmp, orig)


class TestSetOnboardingStep:
    """set_onboarding_step() tests."""

    def test_set_step_for_new_user(self):
        """set_onboarding_step creates user if not exists."""
        import gateway.secretary_user_store as store

        tmp, db_path, orig = _setup_temp_store()
        try:
            store._db_path = lambda: db_path
            if hasattr(store._tls, "conn") and store._tls.conn is not None:
                store._tls.conn.close()
                store._tls.conn = None

            store.set_onboarding_step("fresh", "welcome")
            user = store.get_user("fresh")
            assert user is not None
            assert user["onboarding_step"] == "welcome"
        finally:
            _cleanup_temp_store(tmp, orig)

    def test_set_step_for_existing_user(self):
        """set_onboarding_step updates existing user."""
        import gateway.secretary_user_store as store

        tmp, db_path, orig = _setup_temp_store()
        try:
            store._db_path = lambda: db_path
            if hasattr(store._tls, "conn") and store._tls.conn is not None:
                store._tls.conn.close()
                store._tls.conn = None

                store.upsert_user("prog", display_name="Prog")
                store.set_onboarding_step("prog", "name")
                store.set_onboarding_step("prog", "done")

                user = store.get_user("prog")
                assert user["display_name"] == "Prog"  # preserved
                assert user["onboarding_step"] == "done"
        finally:
            _cleanup_temp_store(tmp, orig)


class TestThreadSafety:
    """Basic concurrent access tests."""

    def test_concurrent_reads(self):
        """Multiple threads can read without errors."""
        import gateway.secretary_user_store as store
        import threading

        tmp, db_path, orig = _setup_temp_store()
        try:
            # Set up shared temp DB for the module
            store._db_path = lambda: db_path
            if hasattr(store._tls, "conn") and store._tls.conn is not None:
                store._tls.conn.close()
                store._tls.conn = None

            store.upsert_user("shared", display_name="Shared")
            errors = []

            def read():
                try:
                    # Each thread gets its own connection via _tls
                    u = store.get_user("shared")
                    if u is None or u["display_name"] != "Shared":
                        errors.append(f"bad read: {u}")
                except Exception as e:
                    errors.append(str(e))

            threads = [threading.Thread(target=read) for _ in range(5)]
            for t in threads:
                t.start()
            for t in threads:
                t.join()

            assert errors == [], f"Errors: {errors}"
        finally:
            # Close per-thread connections
            _cleanup_temp_store(tmp, orig)
