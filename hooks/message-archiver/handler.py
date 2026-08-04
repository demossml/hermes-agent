"""
Message Archiver Hook — agent:start subscriber.

INVARIANT: This hook fires on EVERY agent:start event when
message_archive.enabled = true AND the source chat_id matches
the configured allowlist. Mode-independent.

Uses plugins.message_archive.db.MessageArchiveDB + ArchiveRecord
for persistence. Extractors handle photo/audio/document.
"""

from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Optional

logger = logging.getLogger(__name__)


async def on_agent_start(ctx: Dict[str, Any]) -> None:
    """Handle agent:start — archive the inbound message using MessageArchiveDB."""
    try:
        from plugins.message_archive import is_enabled, chat_is_archived
    except Exception:
        return

    if not is_enabled():
        return

    chat_id = str(ctx.get("chat_id", "") or "")
    if not chat_is_archived(chat_id):
        return

    try:
        from plugins.message_archive.db import ArchiveRecord, get_db, utc_now_iso
        from plugins.message_archive.extractors import classify_extension

        media_urls = ctx.get("media_urls", []) or []
        media_types = ctx.get("media_types", []) or []
        full_msg = ctx.get("full_message", "") or ctx.get("message", "") or ""

        # Resolve project_id
        project_id = ""
        try:
            from modes.router import get_effective_mode
            mode = get_effective_mode(
                ctx.get("platform", "cli"),
                chat_id,
                ctx.get("user_id"),
            )
            if mode == "secretary":
                from modes.state import get_mode_record
                rec = get_mode_record(
                    ctx.get("platform", "cli"), chat_id, ctx.get("user_id"),
                )
                if rec and rec.get("project_id"):
                    project_id = str(rec["project_id"])
            else:
                from projects.project_context import get_current_project_id
                pid = get_current_project_id()
                if pid:
                    project_id = str(pid)
        except Exception:
            pass

        db = get_db()

        # Archive text part
        if full_msg.strip():
            db.enqueue(ArchiveRecord(
                platform=str(ctx.get("platform", "") or ""),
                chat_id=chat_id,
                thread_id=str(ctx.get("thread_id", "") or ""),
                user_id=str(ctx.get("user_id", "") or ""),
                username=str(ctx.get("user_name", "") or ""),
                message_id=str(ctx.get("message_id", "") or ""),
                ts_utc=utc_now_iso(),
                msg_type="text",
                raw_text=full_msg[:4000],
                project_id=project_id,
            ))

        # Archive media attachments
        for i, url in enumerate(media_urls):
            mtype = media_types[i] if i < len(media_types) else "document_other"
            # Download and extract
            extracted = ""
            extractor_name = ""
            try:
                if mtype.startswith("photo"):
                    from plugins.message_archive.extractors import extract_photo
                    extracted, extractor_name = await extract_photo(url)
                elif mtype.startswith("audio") or mtype.startswith("voice"):
                    from plugins.message_archive.extractors import extract_audio
                    extracted, extractor_name = await extract_audio(url)
                elif mtype.startswith("document"):
                    from plugins.message_archive.extractors import extract_document
                    extracted, extractor_name = await extract_document(url)
            except Exception as e:
                logger.debug("extraction failed for %s: %s", url, e)

            db.enqueue(ArchiveRecord(
                platform=str(ctx.get("platform", "") or ""),
                chat_id=chat_id,
                thread_id=str(ctx.get("thread_id", "") or ""),
                user_id=str(ctx.get("user_id", "") or ""),
                username=str(ctx.get("user_name", "") or ""),
                message_id=str(ctx.get("message_id", "") or ""),
                ts_utc=utc_now_iso(),
                msg_type=mtype,
                raw_text="",
                extracted_text=extracted[:4000],
                extractor=extractor_name,
                file_path=url,
                original_name=Path(url).name if url else "",
                project_id=project_id,
            ))

    except Exception as e:
        logger.debug("message-archiver: failed to archive: %s", e)


def register_hooks(hook_system) -> None:
    """Register the agent:start hook."""
    hook_system.on("agent:start", on_agent_start)
    logger.info(
        "message-archiver: hook registered on agent:start [MODE-INDEPENDENT]"
    )
