"""
Archive Admin Tool - safe config management for message_archive.

Available only in secretary mode (check_fn gates on active mode).
Reads/writes ONLY the message_archive key in config.yaml.

Actions:
  list_chats    - show message_archive.chats + enabled status
  add_chat      - add chat_id to allowlist (2-step if migrating from "all chats")
  remove_chat   - remove chat_id from allowlist
  set_enabled   - toggle message_archive.enabled
  status        - db counts, config summary
"""

import json
import logging
import os as _os
from pathlib import Path
from typing import Any, Dict, Optional

logger = logging.getLogger(__name__)


def _load_archive_config() -> Dict[str, Any]:
    from hermes_cli.config import load_config
    cfg = load_config()
    archive = cfg.get("message_archive")
    return archive if isinstance(archive, dict) else {}


def _save_archive_config(archive: Dict[str, Any]) -> None:
    from hermes_cli.config import load_config, save_config
    cfg = load_config()
    if archive:
        cfg["message_archive"] = archive
    elif "message_archive" in cfg:
        del cfg["message_archive"]
    save_config(cfg)


def _validate_chat_id(chat_id: str) -> Optional[str]:
    if not chat_id or not str(chat_id).strip():
        return "chat_id is required and must be a non-empty string"
    if len(str(chat_id)) > 128:
        return "chat_id too long (max 128 chars)"
    return None


def _get_db_counts() -> Dict[str, Any]:
    try:
        from hermes_constants import get_hermes_home
        home = get_hermes_home()
        db_path = Path(home) / "archive.db"
        if not db_path.exists():
            return {"db_exists": False, "db_path": str(db_path)}
        import sqlite3
        conn = sqlite3.connect(str(db_path))
        cur = conn.cursor()
        cur.execute("SELECT COUNT(*) FROM messages")
        total = cur.fetchone()[0]
        cur.execute("SELECT COUNT(DISTINCT chat_id) FROM messages")
        chats = cur.fetchone()[0]
        # Project-aware: count messages with/without project_id
        project_stats = {}
        try:
            cur.execute(
                "SELECT project_id, COUNT(*) FROM messages "
                "WHERE project_id IS NOT NULL AND project_id != '' "
                "GROUP BY project_id ORDER BY COUNT(*) DESC LIMIT 10"
            )
            project_stats = {row[0]: row[1] for row in cur.fetchall()}
        except Exception:
            pass
        conn.close()
        result = {
            "db_exists": True, "db_path": str(db_path),
            "total_messages": total, "unique_chats": chats,
        }
        if project_stats:
            result["by_project"] = project_stats
        return result
    except Exception as e:
        return {"db_exists": False, "error": str(e)}

def archive_admin_tool(
    action: str,
    chat_id: Optional[str] = None,
    enabled: Optional[bool] = None,
    confirmed: bool = False,
) -> str:
    action = str(action or "").strip().lower()
    archive = _load_archive_config()

    if action == "list_chats":
        arch_enabled = archive.get("enabled", False)
        chats = archive.get("chats")
        if chats is None:
            return json.dumps({
                "action": "list_chats", "enabled": arch_enabled,
                "mode": "all_chats", "chats": None,
                "message": "Архивируются ВСЕ чаты (message_archive.chats не задан).",
            }, ensure_ascii=False)
        return json.dumps({
            "action": "list_chats", "enabled": arch_enabled,
            "mode": "allowlist",
            "chat_count": len(chats) if isinstance(chats, list) else 0,
            "chats": [str(c) for c in chats] if isinstance(chats, list) else [],
        }, ensure_ascii=False)

    if action == "add_chat":
        err = _validate_chat_id(chat_id)
        if err:
            return json.dumps({"action": "add_chat", "error": err}, ensure_ascii=False)
        chat_id = str(chat_id).strip()
        current = archive.get("chats")
        if current is None and not confirmed:
            return json.dumps({
                "action": "add_chat", "status": "needs_confirmation",
                "chat_id": chat_id,
                "message": (
                    "Сейчас архивируются ВСЕ чаты (message_archive.chats не задан). "
                    "Перейти на allowlist и добавить только этот чат? "
                    "Вызови archive_admin с action=add_chat, chat_id={} "
                    "и confirmed=true для подтверждения."
                ).format(chat_id),
            }, ensure_ascii=False)
        if current is None:
            archive["chats"] = [chat_id]
        elif isinstance(current, list):
            if chat_id in [str(c) for c in current]:
                return json.dumps({
                    "action": "add_chat", "chat_id": chat_id,
                    "status": "already_present",
                    "chats": [str(c) for c in current],
                }, ensure_ascii=False)
            archive["chats"] = current + [chat_id]
        else:
            archive["chats"] = [chat_id]
        _save_archive_config(archive)
        return json.dumps({
            "action": "add_chat", "chat_id": chat_id, "status": "added",
            "total_chats": len(archive["chats"]),
            "chats": [str(c) for c in archive["chats"]],
        }, ensure_ascii=False)
    if action == "remove_chat":
        err = _validate_chat_id(chat_id)
        if err:
            return json.dumps({"action": "remove_chat", "error": err}, ensure_ascii=False)
        chat_id = str(chat_id).strip()
        current = archive.get("chats")
        if current is None:
            return json.dumps({
                "action": "remove_chat", "chat_id": chat_id,
                "status": "all_chats_mode",
                "message": "Архивируются все чаты - не из чего удалять.",
            }, ensure_ascii=False)
        if not isinstance(current, list):
            return json.dumps({"action": "remove_chat", "error": "Unexpected format"}, ensure_ascii=False)
        str_chats = [str(c) for c in current]
        if chat_id not in str_chats:
            return json.dumps({
                "action": "remove_chat", "chat_id": chat_id,
                "status": "not_found", "chats": str_chats,
            }, ensure_ascii=False)
        archive["chats"] = [c for c in current if str(c) != chat_id]
        _save_archive_config(archive)
        return json.dumps({
            "action": "remove_chat", "chat_id": chat_id, "status": "removed",
            "total_chats": len(archive["chats"]),
            "chats": [str(c) for c in archive["chats"]],
        }, ensure_ascii=False)

    if action == "set_enabled":
        if enabled is None:
            return json.dumps({
                "action": "set_enabled",
                "error": "enabled (bool) is required for set_enabled",
            }, ensure_ascii=False)
        was = archive.get("enabled", False)
        archive["enabled"] = bool(enabled)
        _save_archive_config(archive)
        return json.dumps({
            "action": "set_enabled", "enabled": bool(enabled), "was": was,
        }, ensure_ascii=False)

    if action == "set_max_file_mb":
        if chat_id is None and not isinstance(chat_id, (int, float, str)):
            return json.dumps({
                "action": "set_max_file_mb",
                "error": "chat_id (int, size in MB) is required for set_max_file_mb",
            }, ensure_ascii=False)
        try:
            mb = int(chat_id)
        except (ValueError, TypeError):
            return json.dumps({
                "action": "set_max_file_mb",
                "error": f"chat_id must be an integer (size in MB), got: {chat_id!r}",
            }, ensure_ascii=False)
        if mb < 1 or mb > 2000:
            return json.dumps({
                "action": "set_max_file_mb",
                "error": "max_file_mb must be between 1 and 2000",
            }, ensure_ascii=False)
        was = archive.get("max_file_mb")
        archive["max_file_mb"] = mb
        _save_archive_config(archive)
        return json.dumps({
            "action": "set_max_file_mb", "max_file_mb": mb, "was": was,
        }, ensure_ascii=False)

    if action == "status":
        db = _get_db_counts()
        chats = archive.get("chats")
        chat_mode = "all" if chats is None else ("allowlist" if isinstance(chats, list) else "custom")
        chat_count = len(chats) if isinstance(chats, list) else 0
        return json.dumps({
            "action": "status",
            "enabled": archive.get("enabled", False),
            "chat_mode": chat_mode,
            "monitored_chats": chat_count,
            "db": db,
            "config_keys": sorted(archive.keys()),
        }, ensure_ascii=False)

    return json.dumps({
        "error": "Unknown action: {!r}. Valid: list_chats, add_chat, remove_chat, set_enabled, status".format(action),
    }, ensure_ascii=False)


def check_archive_admin_requirements() -> bool:
    """Only available in secretary mode."""
    try:
        from modes.state import get_current_turn_mode
        mode = get_current_turn_mode()
        if mode is not None:
            return mode == "secretary"
        from modes.router import get_effective_mode
        mode = get_effective_mode(
            _os.environ.get("HERMES_PLATFORM", "cli"),
            _os.environ.get("HERMES_CHAT_ID", ""),
        )
        return mode == "secretary"
    except Exception:
        return False

ARCHIVE_ADMIN_SCHEMA = {
    "name": "archive_admin",
    "description": (
        "Manage message_archive configuration safely. Only available in "
        "secretary mode. Actions: list_chats (show monitored chats), "
        "add_chat (add chat_id to allowlist; two-step confirmation if "
        "migrating from all chats), remove_chat (remove from allowlist), "
        "set_enabled (toggle archive on/off), set_max_file_mb (max attachment size in MB), status (config + db summary)."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "action": {
                "type": "string",
                "enum": ["list_chats", "add_chat", "remove_chat", "set_enabled", "set_max_file_mb", "status"],
                "description": "Action to perform.",
            },
            "chat_id": {
                "type": "string",
                "description": "Chat ID (required for add_chat, remove_chat). Telegram: -100...",
            },
            "enabled": {
                "type": "boolean",
                "description": "Enable/disable archive (required for set_enabled).",
            },
            "confirmed": {
                "type": "boolean",
                "description": "Set to true on second call to confirm migrating from all-chats to allowlist.",
            },
        },
        "required": ["action"],
    },
}


from tools.registry import registry, tool_error

registry.register(
    name="archive_admin",
    toolset="messaging",
    schema=ARCHIVE_ADMIN_SCHEMA,
    handler=lambda args, **kw: archive_admin_tool(
        action=args.get("action", ""),
        chat_id=args.get("chat_id"),
        enabled=args.get("enabled"),
        confirmed=args.get("confirmed", False),
    ),
    check_fn=check_archive_admin_requirements,
    emoji="📦",
)
