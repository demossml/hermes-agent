"""Bridge: archive inbound messages without running the agent loop.

Used when a group message should be persisted (message_archive) but must
not trigger an LLM turn or an outbound chat reply.

Safe no-op if message_archive plugin is missing or disabled.
"""

from __future__ import annotations

import logging
from typing import Any, Mapping, Optional, Sequence

logger = logging.getLogger(__name__)


def archive_enabled() -> bool:
    try:
        from plugins.message_archive import is_enabled
        return bool(is_enabled())
    except Exception:
        return False


def silence_without_reply_enabled() -> bool:
    """Config: message_archive.silence_without_reply (default True when archive on)."""
    try:
        from plugins.message_archive import _cfg
        cfg = _cfg()
        if "silence_without_reply" in cfg:
            return bool(cfg.get("silence_without_reply"))
        return True
    except Exception:
        return True


def chat_should_archive(chat_id: str) -> bool:
    try:
        from plugins.message_archive import chat_is_archived
        return bool(chat_is_archived(str(chat_id or "")))
    except Exception:
        return False


def build_hook_context(
    *,
    platform: str = "",
    user_id: str = "",
    chat_id: str = "",
    thread_id: str = "",
    chat_type: str = "",
    session_id: str = "",
    message: str = "",
    full_message: str = "",
    media_urls: Optional[Sequence[str]] = None,
    media_types: Optional[Sequence[str]] = None,
    telegram_file_ids: Optional[Sequence[str]] = None,
    message_id: str = "",
) -> dict:
    text = full_message or message or ""
    return {
        "platform": platform or "",
        "user_id": user_id or "",
        "chat_id": str(chat_id or ""),
        "thread_id": str(thread_id or ""),
        "chat_type": chat_type or "",
        "session_id": session_id or "",
        "message": (text or "")[:500],
        "full_message": text or "",
        "media_urls": list(media_urls or []),
        "media_types": list(media_types or []),
        "telegram_file_ids": list(telegram_file_ids or []),
        "message_id": str(message_id or ""),
    }


async def archive_message_context(context: Mapping[str, Any]) -> bool:
    """Archive using message-archiver handler or inline fallback."""
    chat_id = str(context.get("chat_id") or "")
    _enabled = archive_enabled()
    _allowed = chat_should_archive(chat_id) if _enabled else False
    logger.info(
        "archive_bridge: archive_enabled=%s chat=%s allowed=%s msg_id=%s thread_id=%s",
        _enabled, chat_id, _allowed,
        context.get("message_id", ""), context.get("thread_id", ""),
    )
    if not _enabled:
        return False
    if not _allowed:
        return False
    try:
        from hooks.message_archiver.handler import handle as archiver_handle
        await archiver_handle("agent:start", dict(context))
        logger.info("archive_bridge: archived via hook msg_id=%s", context.get("message_id", ""))
        return True
    except Exception:
        logger.debug("archive_bridge: hook handler import failed, trying inline", exc_info=True)
    try:
        await _inline_archive(dict(context))
        logger.info("archive_bridge: archived inline msg_id=%s", context.get("message_id", ""))
        return True
    except Exception:
        logger.exception("archive_bridge: failed to archive message")
        return False


async def _inline_archive(context: dict) -> None:
    from plugins.message_archive import get_archive_db, files_dir, max_file_bytes
    from plugins.message_archive.db import ArchiveRecord, utc_now_iso
    from plugins.message_archive import extractors
    import shutil
    import uuid
    from pathlib import Path

    db = get_archive_db()
    raw_text = context.get("full_message") or context.get("message") or ""
    media_urls = list(context.get("media_urls") or [])
    media_types = list(context.get("media_types") or [])
    file_ids = list(context.get("telegram_file_ids") or [])
    base = dict(
        platform=context.get("platform", ""),
        chat_id=context.get("chat_id", ""),
        thread_id=context.get("thread_id", ""),
        user_id=context.get("user_id", ""),
        username="",
        message_id=context.get("message_id", ""),
        ts_utc=utc_now_iso(),
    )
    if not media_urls:
        record = ArchiveRecord(**base, msg_type="text", raw_text=raw_text)
        db.enqueue(record)
        logger.info(
            "archive_bridge: enqueued msg_id=%s thread_id=%s msg_type=text",
            base["message_id"], base["thread_id"],
        )
        return

    multi = len(media_urls) > 1
    for i, path in enumerate(media_urls):
        mtype = media_types[i] if i < len(media_types) else ""
        item = dict(base)
        if multi and item.get("message_id"):
            item["message_id"] = f"{item['message_id']}:{i}"
        src = Path(path)
        if not src.exists():
            continue
        ext = src.suffix.lower()
        if str(mtype).startswith("image/") or ext in {".jpg", ".jpeg", ".png", ".webp", ".gif"}:
            msg_type = "photo"
            extracted, extractor = await extractors.extract_photo(str(src))
        elif str(mtype).startswith("audio/") or ext in {
            ".ogg", ".oga", ".mp3", ".m4a", ".wav", ".webm", ".opus"
        }:
            msg_type = "voice" if ext in {".ogg", ".oga", ".opus"} else "audio"
            extracted, extractor = await extractors.extract_audio(str(src))
        else:
            msg_type = extractors.classify_extension(str(src))
            extracted, extractor = await extractors.extract_document(str(src))
        archived_path = ""
        try:
            if src.stat().st_size <= max_file_bytes():
                dest = files_dir() / f"{uuid.uuid4().hex}{ext}"
                shutil.copy2(src, dest)
                archived_path = str(dest)
        except Exception:
            logger.exception("archive_bridge: copy failed for %s", path)
        doc_category = ""
        try:
            doc_category = extractors.categorize_document(
                extracted or raw_text,
                str(src.name),
            )
        except Exception:
            pass
        telegram_file_id = file_ids[i] if i < len(file_ids) else ""
        receipt_data = {}
        if doc_category == "receipt" and extracted:
            try:
                receipt_data = extractors.parse_receipt(extracted)
            except Exception:
                pass
        metadata = {}
        if doc_category:
            metadata["doc_category"] = doc_category
        if receipt_data:
            metadata["receipt"] = receipt_data
        db.enqueue(
            ArchiveRecord(
                **item,
                msg_type=msg_type,
                raw_text=raw_text if i == 0 else "",
                extracted_text=extracted,
                extractor=extractor,
                file_path=archived_path,
                original_name=src.name,
                mime_type=str(mtype or ""),
                doc_category=doc_category,
                telegram_file_id=telegram_file_id,
                metadata=metadata,
            )
        )
        logger.info(
            "archive_bridge: enqueued msg_id=%s thread_id=%s msg_type=%s extractor=%s",
            item["message_id"], item["thread_id"], msg_type, extractor,
        )


def should_skip_agent_for_archive_only(
    *,
    chat_id: str,
    is_group: bool,
    is_addressed: bool,
) -> bool:
    if not is_group or is_addressed:
        return False
    if not archive_enabled() or not silence_without_reply_enabled():
        return False
    return chat_should_archive(chat_id)
