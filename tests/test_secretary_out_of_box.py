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


class TestTelegramFileId:
    """file_id propagates through context and schema."""

    def test_build_hook_context_with_file_ids(self):
        from gateway.archive_bridge import build_hook_context
        ctx = build_hook_context(
            telegram_file_ids=["AgACAgIAAxkBAAICtest"],
            media_urls=["/tmp/photo.jpg"],
            media_types=["image/jpeg"],
        )
        assert ctx["telegram_file_ids"] == ["AgACAgIAAxkBAAICtest"]

    def test_build_hook_context_empty_file_ids(self):
        from gateway.archive_bridge import build_hook_context
        ctx = build_hook_context()
        assert ctx["telegram_file_ids"] == []

    def test_file_id_in_schema(self):
        from plugins.message_archive.db import SCHEMA_SQL
        assert "telegram_file_id" in SCHEMA_SQL

    def test_file_id_in_dataclass(self):
        from plugins.message_archive.db import ArchiveRecord
        fields = ArchiveRecord.__dataclass_fields__
        assert "telegram_file_id" in fields

    def test_file_id_in_db_column(self):
        with tempfile.TemporaryDirectory() as tmp:
            db_path = Path(tmp) / "archive" / "test.db"
            from plugins.message_archive.db import get_db
            import plugins.message_archive.db as db_mod
            db_mod._db = None
            db = get_db(db_path)
            cols = db._conn.execute("PRAGMA table_info(messages)").fetchall()
            col_names = [c[1] for c in cols]
            assert "telegram_file_id" in col_names
            db.close()
            db_mod._db = None

    def test_message_event_has_file_ids(self):
        from gateway.platforms.base import MessageEvent
        event = MessageEvent(text="test")
        assert event.telegram_file_ids == []

    def test_enqueue_with_file_id(self):
        with tempfile.TemporaryDirectory() as tmp:
            db_path = Path(tmp) / "archive" / "test.db"
            from plugins.message_archive.db import get_db, ArchiveRecord, utc_now_iso
            import plugins.message_archive.db as db_mod
            db_mod._db = None
            db = get_db(db_path)
            fid = "AgACAgIAAxkBAAICtest123"
            record = ArchiveRecord(
                platform="telegram", chat_id="-100123", thread_id="42",
                user_id="100500", message_id="1", ts_utc=utc_now_iso(),
                msg_type="photo", raw_text="чек", telegram_file_id=fid,
            )
            db.enqueue(record)
            import time
            time.sleep(2.5)
            rows = db.query(chat_id="-100123")
            assert len(rows) > 0
            assert rows[0]["telegram_file_id"] == fid
            db.close()
            db_mod._db = None


class TestParseReceipt:
    """Receipt parser extracts structured fields."""

    def test_import(self):
        from plugins.message_archive.extractors import parse_receipt
        assert callable(parse_receipt)

    def test_parse_inn(self):
        from plugins.message_archive.extractors import parse_receipt
        result = parse_receipt("ООО «Ромашка» ИНН: 1234567890")
        assert result["inn"] == "1234567890"

    def test_parse_inn_12_digits(self):
        from plugins.message_archive.extractors import parse_receipt
        result = parse_receipt("ИП Иванов ИНН 123456789012")
        assert result["inn"] == "123456789012"

    def test_parse_total(self):
        from plugins.message_archive.extractors import parse_receipt
        result = parse_receipt("ИТОГО: 1 500.00")
        assert result["total"] == 1500.0

    def test_parse_total_comma(self):
        from plugins.message_archive.extractors import parse_receipt
        result = parse_receipt("ИТОГ: 255,50")
        assert result["total"] == 255.5

    def test_parse_date(self):
        from plugins.message_archive.extractors import parse_receipt
        result = parse_receipt("11.08.2026 14:30")
        assert result["date"] == "11.08.2026"

    def test_parse_date_slash(self):
        from plugins.message_archive.extractors import parse_receipt
        result = parse_receipt("01/12/2026")
        assert result["date"] == "01/12/2026"

    def test_parse_empty(self):
        from plugins.message_archive.extractors import parse_receipt
        assert parse_receipt("") == {}
        assert parse_receipt("просто текст без цифр") == {}

    def test_parse_full_receipt(self):
        from plugins.message_archive.extractors import parse_receipt
        text = """
        ООО «Продукты»
        ИНН: 6732123456
        Кассовый чек №1234
        11.08.2026 15:22
        Молоко 2.5% — 2 x 95.00 = 190.00
        Хлеб ржаной — 1 x 65.00 = 65.00
        ИТОГО: 255.00
        """
        result = parse_receipt(text)
        assert result["store"] == 'ООО «Продукты»'
        assert result["inn"] == "6732123456"
        assert result["total"] == 255.0
        assert result["date"] == "11.08.2026"


class TestReDownloadUtil:
    """re_download_file utility exists and is callable."""

    def test_import(self):
        from plugins.message_archive.re_download import re_download_file
        assert callable(re_download_file)

    def test_returns_none_without_token(self):
        from plugins.message_archive.re_download import re_download_file
        assert re_download_file("", "some_id", "/tmp") is None
        assert re_download_file("token", "", "/tmp") is None


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


class TestSchemaMigration:
    """_migrate_schema добавляет отсутствующие колонки."""

    def test_migration_adds_missing_column(self):
        """При запуске на БД без telegram_file_id — колонка добавляется."""
        import sqlite3
        from plugins.message_archive.db import _migrate_schema
        conn = sqlite3.connect(":memory:")
        conn.execute("""
            CREATE TABLE messages (
                id INTEGER PRIMARY KEY,
                platform TEXT DEFAULT '',
                chat_id TEXT DEFAULT '',
                doc_category TEXT DEFAULT ''
            )
        """)
        # Миграция должна добавить telegram_file_id и project_id
        _migrate_schema(conn.cursor())
        conn.commit()
        cols = {r[1] for r in conn.execute("PRAGMA table_info(messages)")}
        assert "telegram_file_id" in cols
        assert "project_id" in cols
        # doc_category уже была — не должна сломаться
        assert "doc_category" in cols
        conn.close()

    def test_migration_idempotent(self):
        """Повторная миграция не ломает БД."""
        import sqlite3
        from plugins.message_archive.db import _migrate_schema
        conn = sqlite3.connect(":memory:")
        conn.execute("""
            CREATE TABLE messages (
                id INTEGER PRIMARY KEY,
                platform TEXT DEFAULT '',
                telegram_file_id TEXT DEFAULT '',
                doc_category TEXT DEFAULT '',
                project_id TEXT DEFAULT ''
            )
        """)
        # Первый проход
        _migrate_schema(conn.cursor())
        # Второй проход не должен упасть
        _migrate_schema(conn.cursor())
        conn.commit()
        cols = {r[1] for r in conn.execute("PRAGMA table_info(messages)")}
        assert "telegram_file_id" in cols
        assert "doc_category" in cols
        assert "project_id" in cols
        conn.close()

    def test_message_archive_db_runs_migration(self):
        """MessageArchiveDB.__init__ выполняет миграцию при старте."""
        with tempfile.TemporaryDirectory() as tmp:
            import sqlite3
            # Создаём «старую» БД — все колонки, кроме doc_category, telegram_file_id, project_id
            raw_db = Path(tmp) / "archive" / "old.db"
            raw_db.parent.mkdir(parents=True, exist_ok=True)
            conn = sqlite3.connect(str(raw_db))
            conn.executescript("""
                CREATE TABLE messages (
                    id          INTEGER PRIMARY KEY AUTOINCREMENT,
                    platform    TEXT DEFAULT '',
                    chat_id     TEXT DEFAULT '',
                    thread_id   TEXT DEFAULT '',
                    user_id     TEXT DEFAULT '',
                    username    TEXT DEFAULT '',
                    message_id  TEXT DEFAULT '',
                    ts_utc      TEXT NOT NULL,
                    msg_type    TEXT DEFAULT 'text',
                    raw_text    TEXT DEFAULT '',
                    extracted_text TEXT DEFAULT '',
                    extractor   TEXT DEFAULT '',
                    file_path   TEXT DEFAULT '',
                    original_name TEXT DEFAULT '',
                    mime_type   TEXT DEFAULT '',
                    metadata_json TEXT DEFAULT '{}'
                );
            """)
            conn.commit()
            conn.close()
            # Открываем через MessageArchiveDB — миграция должна добавить новые колонки
            from plugins.message_archive.db import MessageArchiveDB
            import plugins.message_archive.db as db_mod
            db_mod._db = None
            db = MessageArchiveDB(raw_db)
            cols = {r[1] for r in db._conn.execute("PRAGMA table_info(messages)").fetchall()}
            assert "telegram_file_id" in cols
            assert "doc_category" in cols
            assert "project_id" in cols
            db.close()
