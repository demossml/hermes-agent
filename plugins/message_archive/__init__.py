"""Message Archive Plugin — background archiving + extraction."""

import logging

logger = logging.getLogger(__name__)


# ── Helpers ───────────────────────────────────────────────────

def is_enabled() -> bool:
    """Check if message_archive is enabled in config."""
    try:
        from hermes_cli.config import cfg_get, load_config
        cfg = load_config()
        archive = cfg.get("message_archive", {})
        return bool(archive.get("enabled", False)) if isinstance(archive, dict) else False
    except Exception:
        return False


def files_dir() -> str:
    """Return archive files directory from config, or default."""
    try:
        from hermes_cli.config import cfg_get, load_config
        cfg = load_config()
        archive = cfg.get("message_archive", {}) or {}
        return str(archive.get("files_dir", "~/.hermes/archive/files"))
    except Exception:
        return "~/.hermes/archive/files"


def max_file_bytes() -> int:
    """Return max_file_mb from config converted to bytes."""
    mb = 40
    try:
        from hermes_cli.config import cfg_get, load_config
        cfg = load_config()
        archive = cfg.get("message_archive", {}) or {}
        mb = int(archive.get("max_file_mb", 40))
    except Exception:
        pass
    return mb * 1024 * 1024


def allowed_chats() -> list:
    """Return allowlist from config, or empty list (= all chats)."""
    try:
        from hermes_cli.config import cfg_get, load_config
        cfg = load_config()
        archive = cfg.get("message_archive", {}) or {}
        chats = archive.get("chats")
        return [str(c) for c in chats] if isinstance(chats, list) else []
    except Exception:
        return []


def chat_is_archived(chat_id: str) -> bool:
    """Check if a specific chat_id is in the allowlist."""
    chats = allowed_chats()
    if not chats:
        return True  # empty = all
    return str(chat_id) in chats


def get_archive_db():
    """Return singleton MessageArchiveDB."""
    from plugins.message_archive.db import get_db
    return get_db()


# ── Plugin lifecycle ──────────────────────────────────────────

def register(plugin_manager):
    """Register the plugin with the Hermes plugin system."""
    plugin_manager.register_hook("agent:start", _on_agent_start)
    logger.info("message_archive plugin: registered agent:start hook")


def _on_agent_start(ctx: dict):
    """Handle agent:start — archive the inbound message."""
    if not is_enabled():
        return

    try:
        chat_id = str(ctx.get("chat_id", "") or "")
        if not chat_is_archived(chat_id):
            return

        from plugins.message_archive.db import ArchiveRecord, utc_now_iso

        record = ArchiveRecord(
            platform=str(ctx.get("platform", "") or ""),
            chat_id=chat_id,
            thread_id=str(ctx.get("thread_id", "") or ""),
            user_id=str(ctx.get("user_id", "") or ""),
            username=str(ctx.get("user_name", "") or ""),
            message_id=str(ctx.get("message_id", "") or ""),
            ts_utc=utc_now_iso(),
            msg_type="text",
            raw_text=(ctx.get("full_message", "") or ctx.get("message", "") or "")[:4000],
            project_id=_resolve_project_id(ctx),
        )

        db = get_archive_db()
        db.enqueue(record)
    except Exception as e:
        logger.debug("message_archive: failed to archive: %s", e)


def _resolve_project_id(ctx: dict) -> str:
    """Resolve project_id for the archived message."""
    try:
        from modes.router import get_effective_mode
        mode = get_effective_mode(
            ctx.get("platform", "cli"),
            ctx.get("chat_id", ""),
            ctx.get("user_id"),
        )
        if mode == "secretary":
            from modes.state import get_mode_record
            rec = get_mode_record(
                ctx.get("platform", "cli"),
                ctx.get("chat_id", ""),
                ctx.get("user_id"),
            )
            if rec and rec.get("project_id"):
                return str(rec["project_id"])
        else:
            from projects.project_context import get_current_project_id
            pid = get_current_project_id()
            if pid:
                return str(pid)
    except Exception:
        pass
    return ""
