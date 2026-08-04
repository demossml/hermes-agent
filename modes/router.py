"""
Mode router — apply mode changes and resolve effective mode.

apply_mode_change()  — persist mode + return a user-facing reply (from prompts.py templates)
get_effective_mode() — resolve (platform, chat_id) -> mode string
format_status_reply() — build /mode status response with project & archive info
get_guidance()       — return the prompt block for the active mode
"""

from __future__ import annotations

import logging
from typing import Optional

from modes.state import get_mode, set_mode, _DEFAULT_MODE
from modes.policy import get_guidance as _policy_guidance

logger = logging.getLogger(__name__)

# ── Mode display labels ───────────────────────────────────────

_MODE_LABELS_RU: dict[str, str] = {
    "dev": "разработка",
    "secretary": "секретарь",
}

_MODE_LABELS_EN: dict[str, str] = {
    "dev": "dev",
    "secretary": "secretary",
}


def _get_active_project_name() -> Optional[str]:
    try:
        from projects.project_context import get_current_project_name
        name = get_current_project_name()
        if name:
            return name
    except Exception:
        pass
    return None


def _get_archive_enabled() -> bool:
    try:
        from hermes_cli.config import cfg_get, load_config
        cfg = load_config()
        return bool(cfg_get(cfg, "message_archive", "enabled", default=False))
    except Exception:
        return False


def _detect_lang(text: str) -> str:
    if not text:
        return "ru"
    return "ru" if any('Ѐ' <= c <= 'ӿ' for c in text) else "en"


def apply_mode_change(
    platform: str,
    chat_id: str,
    new_mode: str,
    user_id: Optional[str] = None,
    project_id: Optional[str] = None,
    user_text: str = "",
) -> str:
    """Persist a mode switch and return a user-facing confirmation message."""
    set_mode(
        platform=platform,
        chat_id=chat_id,
        mode=new_mode,
        user_id=user_id,
        project_id=project_id,
    )

    lang = _detect_lang(user_text)
    from modes import prompts

    if new_mode == "secretary":
        base = (
            prompts.REPLY_SECRETARY_RU if lang == "ru"
            else prompts.REPLY_SECRETARY_EN
        )
        if not _get_archive_enabled():
            warning = (
                "\n\nАрхив выключен. Включите message_archive.enabled "
                "в config или скажите «включи архив»."
                if lang == "ru"
                else "\n\nArchive is disabled. Enable message_archive.enabled "
                "in config or say \"enable archive\"."
            )
            return base + warning
        return base

    # dev mode
    project_name = _get_active_project_name()
    if lang == "ru":
        no_project = prompts.REPLY_DEV_NO_PROJECT_RU
        template = prompts.REPLY_DEV_RU
    else:
        no_project = prompts.REPLY_DEV_NO_PROJECT_EN
        template = prompts.REPLY_DEV_EN
    return template.format(project_name=project_name or no_project)


def format_status_reply(
    platform: str,
    chat_id: str,
    user_id: Optional[str] = None,
    user_text: str = "",
) -> str:
    """Build a /mode status response with project and archive info."""
    mode = get_mode(platform=platform, chat_id=chat_id, user_id=user_id)
    lang = _detect_lang(user_text)

    project_name = _get_active_project_name()
    archive_on = _get_archive_enabled()

    if lang == "ru":
        mode_label = _MODE_LABELS_RU.get(mode, mode)
        proj = project_name or "—"
        arch = "включен" if archive_on else "выключен"
        from modes import prompts
        return prompts.REPLY_STATUS_RU.format(
            mode_label=mode_label,
            project_name=proj,
            archive_status=arch,
        )
    else:
        mode_label = _MODE_LABELS_EN.get(mode, mode)
        proj = project_name or "—"
        arch = "enabled" if archive_on else "disabled"
        from modes import prompts
        return prompts.REPLY_STATUS_EN.format(
            mode_label=mode_label,
            project_name=proj,
            archive_status=arch,
        )


def format_already_reply(mode: str, user_text: str = "") -> str:
    """Build a 'you are already in this mode' message."""
    lang = _detect_lang(user_text)
    mode_label = (
        _MODE_LABELS_RU.get(mode, mode)
        if lang == "ru"
        else _MODE_LABELS_EN.get(mode, mode)
    )
    from modes import prompts
    if lang == "ru":
        return prompts.REPLY_ALREADY_RU.format(mode_label=mode_label)
    return prompts.REPLY_ALREADY_EN.format(mode_label=mode_label)


def get_effective_mode(
    platform: str,
    chat_id: str,
    user_id: Optional[str] = None,
) -> str:
    """Resolve the effective mode for a (platform, chat_id, user_id) triple."""
    return get_mode(platform=platform, chat_id=chat_id, user_id=user_id)


def get_guidance(mode: str) -> str:
    """Return the system-prompt guidance block for the given mode."""
    return _policy_guidance(mode)
