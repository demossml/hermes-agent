"""
Tests: secretary-profile works out of the box.

Verifies message_archive, archive_bridge, categorize_document,
doc_category, forum_topic_names, and secretary auto-config.
"""

import sys
import tempfile
from pathlib import Path
from unittest.mock import patch, MagicMock
import pytest


class TestCategorizeDocument:
    """Document category detection via keyword matching."""

    def test_import(self):
        from plugins.message_archive.extractors import categorize_document
        assert callable(categorize_document)

    def test_receipt(self):
        from plugins.message_archive.extractors import categorize_document
        assert categorize_document(text="КАССОВЫЙ ЧЕК Итого: 1500") == "receipt"
        assert categorize_document(file_name="чек_12345.pdf") == "receipt"

    def test_invoice(self):
        from plugins.message_archive.extractors import categorize_document
        assert categorize_document(text="СЧЁТ-ФАКТУРА № 42") == "invoice"

    def test_contract(self):
        from plugins.message_archive.extractors import categorize_document
        assert categorize_document(text="ДОГОВОР поставки № 15") == "contract"

    def test_act(self):
        from plugins.message_archive.extractors import categorize_document
        assert categorize_document(text="Акт приёма-передачи работ") == "act"

    def test_waybill(self):
        from plugins.message_archive.extractors import categorize_document
        assert categorize_document(file_name="Товарная_накладная.xlsx") == "waybill"

    def test_payment(self):
        from plugins.message_archive.extractors import categorize_document
        assert categorize_document(text="Платёжное поручение № 89") == "payment"

    def test_no_match(self):
        from plugins.message_archive.extractors import categorize_document
        assert categorize_document(text="Отчёт о продажах") == ""

    def test_empty(self):
        from plugins.message_archive.extractors import categorize_document
        assert categorize_document() == ""


class TestArchiveDBSchema:
    """Verify doc_category in schema and ArchiveRecord."""

    def test_doc_category_in_schema_sql(self):
        from plugins.message_archive.db import SCHEMA_SQL
        assert "doc_category" in SCHEMA_SQL

    def test_doc_category_in_dataclass(self):
        from plugins.message_archive.db import ArchiveRecord
        fields = ArchiveRecord.__dataclass_fields__
        assert "doc_category" in fields

    def test_record_default_and_set(self):
        from plugins.message_archive.db import ArchiveRecord
        r = ArchiveRecord()
        assert r.doc_category == ""
        r2 = ArchiveRecord(doc_category="receipt")
        assert r2.doc_category == "receipt"


class TestArchiveBridge:
    """Verify archive_bridge functions exist and work."""

    def test_build_hook_context(self):
        from gateway.archive_bridge import build_hook_context
        ctx = build_hook_context(
            platform="telegram", user_id="123",
            chat_id="-1003811950263", thread_id="42",
            chat_type="group", message="test", message_id="999",
            media_urls=["/tmp/photo.jpg"], media_types=["image/jpeg"],
        )
        assert ctx["platform"] == "telegram"
        assert ctx["chat_id"] == "-1003811950263"
        assert ctx["thread_id"] == "42"
        assert ctx["message_id"] == "999"

    def test_build_hook_context_defaults(self):
        from gateway.archive_bridge import build_hook_context
        ctx = build_hook_context()
        assert ctx["platform"] == ""
        assert ctx["media_urls"] == []


class TestMessageArchivePlugin:
    """Verify message_archive plugin API."""

    def test_chat_is_archived_empty_allows_all(self):
        from plugins.message_archive import chat_is_archived
        with patch("plugins.message_archive.allowed_chats", return_value=[]):
            assert chat_is_archived("-1003811950263") is True

    def test_chat_is_archived_filter(self):
        from plugins.message_archive import chat_is_archived
        with patch("plugins.message_archive.allowed_chats",
                   return_value=["-1003811950263"]):
            assert chat_is_archived("-1003811950263") is True
            assert chat_is_archived("-1009999999999") is False

    def test_db_singleton(self):
        with tempfile.TemporaryDirectory() as tmp:
            db_path = Path(tmp) / "archive" / "test.db"
            from plugins.message_archive.db import get_db
            import plugins.message_archive.db as db_mod
            db_mod._db = None
            db1 = get_db(db_path)
            db2 = get_db(db_path)
            assert db1 is db2
            db1.close()
            db_mod._db = None

    def test_tables_created(self):
        with tempfile.TemporaryDirectory() as tmp:
            db_path = Path(tmp) / "archive" / "test.db"
            from plugins.message_archive.db import get_db
            import plugins.message_archive.db as db_mod
            db_mod._db = None
            db = get_db(db_path)
            rows = db._conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name='messages'"
            ).fetchall()
            assert len(rows) == 1
            db.close()
            db_mod._db = None

    def test_doc_category_column(self):
        with tempfile.TemporaryDirectory() as tmp:
            db_path = Path(tmp) / "archive" / "test.db"
            from plugins.message_archive.db import get_db
            import plugins.message_archive.db as db_mod
            db_mod._db = None
            db = get_db(db_path)
            cols = db._conn.execute("PRAGMA table_info(messages)").fetchall()
            col_names = [c[1] for c in cols]
            assert "doc_category" in col_names
            db.close()
            db_mod._db = None


class TestForumTopicNames:
    """Verify get_topic_name method on TelegramAdapter."""

    def test_import_and_logic(self):
        """Test topic name resolution directly if httpx is available."""
        try:
            from gateway.platforms.telegram import TelegramAdapter as TA
        except (ImportError, ModuleNotFoundError):
            pytest.skip("httpx or deps not available — skipping gateway import test")
        ta = TA.__new__(TA)
        ta._forum_topic_names = {}
        assert ta.get_topic_name("-100123", "1") == "General"
        assert ta.get_topic_name("-100123", "") == "General"
        assert ta.get_topic_name("-100123", "42") == "Тема #42"
        ta._forum_topic_names = {-100123: {42: "НДС"}}
        assert ta.get_topic_name("-100123", "42") == "НДС"


class TestSecretaryProfileConfig:
    """Secretary profile template strings and seeding."""

    def test_soul_template(self):
        from hermes_cli.profiles import _SECRETARY_SOUL_MD
        lower = _SECRETARY_SOUL_MD.lower()
        assert "секретарь" in lower or "observer" in lower

    def test_config_template(self):
        from hermes_cli.profiles import _SECRETARY_CONFIG_YAML
        assert "message_archive" in _SECRETARY_CONFIG_YAML
        assert "observe_unmentioned_group_messages" in _SECRETARY_CONFIG_YAML
        assert "require_mention" in _SECRETARY_CONFIG_YAML

    def test_config_format(self):
        from hermes_cli.profiles import _SECRETARY_CONFIG_YAML
        result = _SECRETARY_CONFIG_YAML.format(profile_name="secretary-test")
        assert "secretary-test" in result

    def test_seed_writes_files(self):
        with tempfile.TemporaryDirectory() as tmp:
            from hermes_cli.profiles import _seed_secretary_profile_config
            _seed_secretary_profile_config(Path(tmp), "secretary-test")
            assert (Path(tmp) / "SOUL.md").exists()
            assert (Path(tmp) / "config.yaml").exists()
            config = (Path(tmp) / "config.yaml").read_text()
            assert "message_archive" in config

    def test_seed_preserves_existing_config(self):
        with tempfile.TemporaryDirectory() as tmp:
            from hermes_cli.profiles import _seed_secretary_profile_config
            (Path(tmp) / "config.yaml").write_text("existing: true\n")
            _seed_secretary_profile_config(Path(tmp), "secretary-test")
            assert (Path(tmp) / "config.yaml").read_text() == "existing: true\n"


class TestCfgRegression:
    """Verify _cfg() is defined; silence_without_reply_enabled works."""

    def test_cfg_callable(self):
        from plugins.message_archive import _cfg
        assert callable(_cfg)

    def test_silence_without_reply_returns_bool(self):
        from gateway.archive_bridge import silence_without_reply_enabled
        assert isinstance(silence_without_reply_enabled(), bool)


class TestEnqueueWithDocCategory:
    """Verify enqueue writes doc_category to DB."""

    def test_enqueue_and_read(self):
        with tempfile.TemporaryDirectory() as tmp:
            db_path = Path(tmp) / "archive" / "test.db"
            from plugins.message_archive.db import get_db, ArchiveRecord, utc_now_iso
            import plugins.message_archive.db as db_mod
            db_mod._db = None
            db = get_db(db_path)
            record = ArchiveRecord(
                platform="telegram", chat_id="-100123", thread_id="42",
                user_id="100500", message_id="1", ts_utc=utc_now_iso(),
                msg_type="document_pdf", raw_text="СЧЁТ-ФАКТУРА № 42",
                extracted_text="Счёт-фактура № 42", extractor="pymupdf",
                original_name="inv.pdf", mime_type="application/pdf",
                doc_category="invoice",
            )
            db.enqueue(record)
            import time
            time.sleep(2.5)  # wait for writer thread
            rows = db.query(chat_id="-100123")
            assert len(rows) > 0
            assert rows[0]["doc_category"] == "invoice"
            db.close()
            db_mod._db = None
