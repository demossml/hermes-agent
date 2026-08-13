"""Secretary skill validation — run readiness checks against a profile's .env.

Each validator returns ``(status, message)`` where ``status`` is one of
``ready | error | needs_setup``. Values are read from the ACTIVE profile's
``.env`` (not the process environment), so the checks are per-profile and
work from the control gateway process.

Network checks are short-timeout and best-effort; any failure is reported as
``error`` with a human-readable message.
"""

from __future__ import annotations

import shutil
from typing import Optional

STATUS_READY = "ready"
STATUS_ERROR = "error"
STATUS_NEEDS_SETUP = "needs_setup"

_TIMEOUT = 10


def validate_mail(profile_home) -> tuple[str, str]:
    from gateway.secretary_skills_store import read_env_value

    host = read_env_value(profile_home, "SECRETARY_MAIL_IMAP_HOST") or ""
    email = read_env_value(profile_home, "SECRETARY_MAIL_EMAIL") or ""
    pwd = read_env_value(profile_home, "SECRETARY_MAIL_PASSWORD") or ""

    if not (host and email and pwd):
        return STATUS_ERROR, "не заданы IMAP host / email / пароль"

    import imaplib
    import ssl

    try:
        ctx = ssl.create_default_context()
        conn = imaplib.IMAP4_SSL(host, 993, ssl_context=ctx, timeout=_TIMEOUT)
    except Exception as e:
        return STATUS_ERROR, f"нет соединения с {host}: {e}"

    try:
        conn.login(email, pwd)
    except Exception as e:
        try:
            conn.logout()
        except Exception:
            pass
        return STATUS_ERROR, f"ошибка входа: {e}"

    try:
        conn.logout()
    except Exception:
        pass
    return STATUS_READY, "подключение успешно"


def validate_calendar(profile_home) -> tuple[str, str]:
    from gateway.secretary_skills_store import read_env_value

    url = read_env_value(profile_home, "SECRETARY_CAL_ICS_URL") or ""
    if not url:
        return STATUS_ERROR, "не задан ICS URL"

    import urllib.request

    try:
        req = urllib.request.Request(url, headers={"User-Agent": "hermes-secretary"})
        with urllib.request.urlopen(req, timeout=_TIMEOUT) as resp:
            resp.read(512)
        return STATUS_READY, "ICS доступен"
    except Exception as e:
        return STATUS_ERROR, f"не удалось получить ICS: {e}"


def validate_vision(profile_home) -> tuple[str, str]:
    path = shutil.which("vision-cli")
    if path:
        return STATUS_READY, "vision-cli найден"
    return STATUS_ERROR, "vision-cli не найден — установите его"


def validate_tgcli(profile_home) -> tuple[str, str]:
    path = shutil.which("tg")
    if not path:
        return STATUS_ERROR, "tg не найден — установите telegram-cli"

    # Optional session probe. If no session, keep needs_setup and ask the
    # user to authenticate on the host (interactive auth stays host-only MVP).
    import subprocess

    try:
        result = subprocess.run(
            ["tg", "chats", "--json"],
            capture_output=True,
            text=True,
            timeout=_TIMEOUT,
        )
        if result.returncode == 0:
            return STATUS_READY, "tg: сессия есть"
    except Exception:
        pass
    return STATUS_NEEDS_SETUP, "выполните tg auth на Mac (нет сессии)"


def validate_skill(skill_id: str, profile_home) -> tuple[str, str]:
    """Run the appropriate validator for a skill. Returns (status, message)."""
    skill_id = (skill_id or "").strip()
    if skill_id in ("groups", "tasks"):
        return STATUS_READY, ""
    if skill_id == "mail":
        return validate_mail(profile_home)
    if skill_id == "calendar":
        return validate_calendar(profile_home)
    if skill_id == "vision":
        return validate_vision(profile_home)
    if skill_id == "tgcli":
        return validate_tgcli(profile_home)
    return STATUS_ERROR, "неизвестное умение"
