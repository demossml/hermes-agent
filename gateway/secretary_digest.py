"""
Secretary digest builder — "morning brief" delivered via Telegram.

API:
  build_digest_text(telegram_id) -> str
  send_digest_to_user(bot, telegram_id) -> bool   (needs Telegram Bot instance)
  run_daily_digest(bot) -> dict                   (iterate all digest users)
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any, Optional

logger = logging.getLogger(__name__)


def _now_utc() -> datetime:
    return datetime.now(timezone.utc)


def build_digest_text(telegram_id: str) -> str:
    """Build digest text for one user. Returns empty string if digest is off."""
    from gateway.secretary_user_store import get_user, should_send_digest

    user = get_user(telegram_id)
    if not user:
        return ""
    if not should_send_digest(telegram_id):
        return ""

    name = user.get("display_name", "") or "друг"
    lines = [f"\u2600\ufe0f Доброе утро, {name}!", ""]

    # ── Mail section ──
    try:
        from tools.secretary.mail_inbox import is_configured, list_recent, format_mail_list

        if is_configured():
            emails = list_recent(hours=12, limit=10)
            if emails:
                # Classify and group
                from tools.secretary.mail_inbox import classify_mail
                important = [m for m in emails if classify_mail(
                    m.get("subject", ""), m.get("from_addr", ""), m.get("snippet", ""),
                ) == "important"]
                newsletters = [m for m in emails if classify_mail(
                    m.get("subject", ""), m.get("from_addr", ""), m.get("snippet", ""),
                ) == "newsletter"]

                if important:
                    lines.append(f"\u0001f534 Важное ({len(important)}):")
                    for i, m in enumerate(important[:5], 1):
                        sender = m.get("from_name", "") or m.get("from_addr", "")
                        subject = m.get("subject", "")
                        if len(subject) > 50:
                            subject = subject[:47] + "..."
                        lines.append(f"  {i}. {sender}")
                        lines.append(f"     {subject}")
                if newsletters:
                    lines.append(f"\u0001f4f0 Рассылки: {len(newsletters)}")
                remaining = len(emails) - len(important) - len(newsletters)
                if remaining > 0:
                    lines.append(f"\u26aa\ufe0f Прочее: {remaining}")
                if not important and not newsletters:
                    lines.append(f"\u0001f4e7 Всего писем: {len(emails)}")
            else:
                lines.append("\u0001f4ed Важных писем нет.")
        else:
            lines.append("\u0001f4e7 Почта не настроена.")
    except Exception as e:
        logger.warning("Digest mail section failed: %s", e)
        lines.append("\u0001f4e7 Почта недоступна.")

    # ── Mode / Project (optional, best effort) ──
    try:
        from modes.router import get_effective_mode
        mode = get_effective_mode("telegram", str(telegram_id), str(telegram_id))
        mode_label = "\u0001f6e0\ufe0f разработка" if mode == "dev" else "\u0001f4cb секретарь"
        lines.append("")
        lines.append(f"\u0001f500 Режим: {mode_label}")
    except Exception:
        pass

    return "\n".join(lines)


async def send_digest_to_user(bot, telegram_id: str) -> bool:
    """Send digest to one user via Telegram bot. Returns True if sent.

    Uses telegram_id as chat_id (DM — same number).
    Mark digest as sent in user store.
    """
    from gateway.secretary_user_store import mark_digest_sent

    text = build_digest_text(telegram_id)
    if not text:
        return False

    # Build inline keyboard
    try:
        from telegram import InlineKeyboardButton, InlineKeyboardMarkup
        keyboard = InlineKeyboardMarkup([
            [
                InlineKeyboardButton("\u0001f4e7 Почта", callback_data="menu:mail"),
                InlineKeyboardButton("\u0001f4cb Меню", callback_data="menu:home"),
            ],
        ])
    except ImportError:
        keyboard = None

    try:
        chat_id = int(telegram_id)
        await bot.send_message(
            chat_id=chat_id,
            text=text,
            reply_markup=keyboard,
        )
        mark_digest_sent(telegram_id)
        logger.info("Digest sent to user %s", telegram_id)
        return True
    except Exception as e:
        logger.warning("Failed to send digest to %s: %s", telegram_id, e)
        return False


async def run_daily_digest(bot) -> dict[str, Any]:
    """Iterate all digest-enabled users and send digests.

    Returns: {"sent": int, "skipped": int, "errors": int}
    """
    from gateway.secretary_user_store import get_digest_users, should_send_digest

    users = get_digest_users()
    sent = 0
    skipped = 0
    errors = 0

    for user in users:
        tid = user.get("telegram_id", "")
        if not tid:
            continue

        # Check timezone — only send if user's local hour matches digest_hour
        try:
            tz_name = user.get("timezone", "Europe/Moscow")
            digest_hour = int(user.get("digest_hour", 9))

            import zoneinfo
            try:
                tz = zoneinfo.ZoneInfo(tz_name)
            except Exception:
                tz = zoneinfo.ZoneInfo("Europe/Moscow")

            now = _now_utc()
            local_hour = now.astimezone(tz).hour

            if local_hour != digest_hour:
                skipped += 1
                continue
        except Exception:
            pass  # If timezone check fails, send anyway

        if not should_send_digest(tid):
            skipped += 1
            continue

        ok = await send_digest_to_user(bot, tid)
        if ok:
            sent += 1
        else:
            errors += 1

    return {"sent": sent, "skipped": skipped, "errors": errors}
