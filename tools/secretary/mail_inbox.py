"""
Mail inbox reader for Secretary mode.

Backends:
  imap     — pure Python imaplib (default, no deps)
  himalaya — CLI wrapper (requires `himalaya` binary + config)

Env vars:
  SECRETARY_MAIL_BACKEND       imap | himalaya  (default: imap)
  SECRETARY_MAIL_IMAP_HOST     IMAP server hostname
  SECRETARY_MAIL_IMAP_PORT     IMAP port (default: 993)
  SECRETARY_MAIL_EMAIL         Login email
  SECRETARY_MAIL_PASSWORD      Password or app password

API:
  list_recent(hours=12, limit=20) -> list[dict]
  list_awaiting_reply(days=3, limit=15) -> list[dict]
  fetch_body(mail_id) -> dict
  send_mail(to, subject, body) -> dict
  is_configured() -> bool
"""

from __future__ import annotations

import email as _email
import email.header
import email.utils
import logging
import os
import subprocess
from datetime import datetime, timedelta, timezone
from typing import Any, Optional

logger = logging.getLogger(__name__)

# ── Constants ───────────────────────────────────────────────────

_IMAP_TIMEOUT = 15  # seconds
_SMTP_TIMEOUT = 15
_DEFAULT_IMAP_PORT = 993
_DEFAULT_SMTP_PORT = 587

# Output dict shape: {id, from_addr, from_name, subject, date_iso, date_display, snippet}


def _now_utc() -> datetime:
    return datetime.now(timezone.utc)


# ── Public API ─────────────────────────────────────────────────


def is_configured() -> bool:
    """Return True if the mail backend can authenticate.

    For imap: requires host, email, and password.
    For himalaya: requires binary and config.
    """
    backend = _backend_name()
    if backend == "himalaya":
        return _himalaya_available()
    # IMAP: need at minimum host + email + password
    host = _imap_host()
    email = _imap_email()
    pwd = _imap_password()
    if not (host and email and pwd):
        return False
    # Basic format validation
    if "@" not in email or "." not in host:
        return False
    return True


def list_recent(hours: int = 12, limit: int = 20) -> list[dict[str, Any]]:
    """Return list of recent emails as dicts. Raises RuntimeError if not configured."""
    if not is_configured():
        raise RuntimeError(
            "Mail not configured. Set SECRETARY_MAIL_* env vars or configure himalaya."
        )

    backend = _backend_name()
    since = _now_utc() - timedelta(hours=hours)

    if backend == "himalaya":
        return _list_himalaya(since, limit)
    return _list_imap(since, limit)


# ── Backend: imaplib ───────────────────────────────────────────


def _imap_host() -> str:
    return os.environ.get("SECRETARY_MAIL_IMAP_HOST", "").strip()


def _imap_port() -> int:
    try:
        return int(os.environ.get("SECRETARY_MAIL_IMAP_PORT", "993"))
    except (ValueError, TypeError):
        return 993


def _imap_email() -> str:
    return os.environ.get("SECRETARY_MAIL_EMAIL", "").strip()


def _imap_password() -> str:
    return os.environ.get("SECRETARY_MAIL_PASSWORD", "").strip()


def _list_imap(since: datetime, limit: int) -> list[dict[str, Any]]:
    import imaplib
    import ssl

    host = _imap_host()
    port = _imap_port()
    user = _imap_email()
    pwd = _imap_password()

    if not (host and user and pwd):
        raise RuntimeError("IMAP not configured: set SECRETARY_MAIL_IMAP_HOST, _EMAIL, _PASSWORD")

    ctx = ssl.create_default_context()
    try:
        conn = imaplib.IMAP4_SSL(host, port, ssl_context=ctx, timeout=_IMAP_TIMEOUT)
    except (OSError, imaplib.IMAP4.error) as e:
        raise RuntimeError(f"IMAP connection failed: {host}:{port} — {e}")
    try:
        try:
            conn.login(user, pwd)
        except imaplib.IMAP4.error as e:
            raise RuntimeError(f"IMAP login failed for {user}: {e}")
        typ, _ = conn.select("INBOX", readonly=True)
        if typ != "OK":
            raise RuntimeError(f"IMAP: cannot open INBOX")

        # IMAP search: SINCE DD-Mon-YYYY
        since_str = since.strftime("%d-%b-%Y")
        typ, data = conn.search(None, f"(SINCE {since_str})")
        if typ != "OK":
            return []

        ids = data[0].split()
        if not ids:
            return []

        # Fetch most recent (last N ids = newest)
        ids_to_fetch = ids[-limit:]
        results: list[dict[str, Any]] = []

        for mid in reversed(ids_to_fetch):  # newest first
            typ, msg_data = conn.fetch(mid, "(BODY.PEEK[HEADER.FIELDS (FROM SUBJECT DATE)])")
            if typ != "OK":
                continue
            raw = msg_data[0][1]
            if not raw:
                continue
            msg = _email.message_from_bytes(raw)

            from_raw = msg.get("From", "")
            from_name, from_addr = _parse_from(from_raw)
            subject = msg.get("Subject", "(без темы)")
            date_str = msg.get("Date", "")

            # Decode subject
            subject = _decode_header(subject)

            # Parse date
            date_dt = email.utils.parsedate_to_datetime(date_str) if date_str else None
            date_iso = date_dt.isoformat() if date_dt else ""
            date_display = _format_date_display(date_dt) if date_dt else ""

            results.append({
                "id": mid.decode() if isinstance(mid, bytes) else str(mid),
                "from_addr": from_addr,
                "from_name": from_name,
                "subject": subject,
                "date_iso": date_iso,
                "date_display": date_display,
                "snippet": "",
            })

            if len(results) >= limit:
                break

        return results
    finally:
        try:
            conn.logout()
        except Exception:
            pass


# ── Backend: himalaya CLI ──────────────────────────────────────


def _himalaya_available() -> bool:
    try:
        subprocess.run(
            ["himalaya", "--version"],
            capture_output=True, timeout=5,
        )
        return True
    except Exception:
        return False


def _list_himalaya(since: datetime, limit: int) -> list[dict[str, Any]]:
    """Use himalaya envelope list --output json."""
    # himalaya envelope list --page-size N --output json
    result = subprocess.run(
        ["himalaya", "envelope", "list", "--page-size", str(limit), "--output", "json"],
        capture_output=True, text=True, timeout=20,
    )
    if result.returncode != 0:
        raise RuntimeError(f"himalaya failed: {result.stderr.strip() or result.stdout.strip()}")

    import json
    try:
        raw = json.loads(result.stdout)
    except json.JSONDecodeError:
        return []

    if not isinstance(raw, list):
        return []

    since_ts = since.timestamp()
    results: list[dict[str, Any]] = []

    for env in raw:
        if not isinstance(env, dict):
            continue
        date_str = env.get("date", "")
        dt: Optional[datetime] = None
        try:
            dt = _parse_himalaya_date(date_str)
        except Exception:
            pass
        if dt and dt.timestamp() < since_ts:
            continue

        from_raw = env.get("from", "")
        from_name, from_addr = _parse_from(from_raw)

        results.append({
            "id": str(env.get("id", "")),
            "from_addr": from_addr,
            "from_name": from_name,
            "subject": env.get("subject", "(без темы)"),
            "date_iso": date_str,
            "date_display": _format_date_display(dt) if dt else date_str,
            "snippet": "",
        })

        if len(results) >= limit:
            break

    return results


# ── Helpers ────────────────────────────────────────────────────


def _backend_name() -> str:
    return os.environ.get("SECRETARY_MAIL_BACKEND", "imap").strip().lower()


def _parse_from(from_raw: str) -> tuple[str, str]:
    """Parse 'Name <addr>' or 'addr' into (name, addr)."""
    name, addr = email.utils.parseaddr(from_raw)
    return (name or "", addr or from_raw.strip())


def _decode_header(raw: str) -> str:
    """Decode RFC 2047 encoded headers."""
    parts = email.header.decode_header(raw or "")
    result = ""
    for text, charset in parts:
        if isinstance(text, bytes):
            try:
                result += text.decode(charset or "utf-8", errors="replace")
            except Exception:
                result += text.decode("utf-8", errors="replace")
        else:
            result += str(text)
    return result or "(без темы)"


def _format_date_display(dt: datetime) -> str:
    """Human-friendly date: 'Сегодня 14:30', 'Вчера 09:15', '10 авг 12:00'."""
    now = _now_utc()
    local_dt = dt.astimezone() if dt.tzinfo else dt.replace(tzinfo=timezone.utc).astimezone()

    if local_dt.date() == now.astimezone().date():
        return f"Сегодня {local_dt.strftime('%H:%M')}"
    yesterday = (now - timedelta(days=1)).astimezone().date()
    if local_dt.date() == yesterday:
        return f"Вчера {local_dt.strftime('%H:%M')}"

    months_ru = [
        "", "янв", "фев", "мар", "апр", "мая", "июн",
        "июл", "авг", "сен", "окт", "ноя", "дек",
    ]
    m = months_ru[local_dt.month]
    return f"{local_dt.day} {m} {local_dt.strftime('%H:%M')}"


def _parse_himalaya_date(raw: str) -> Optional[datetime]:
    """Parse himalaya's date format."""
    if not raw:
        return None
    try:
        # Try ISO format
        return datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError:
        pass
    # Try email.utils fallback
    try:
        return email.utils.parsedate_to_datetime(raw)
    except Exception:
        return None


# ── Follow-up: emails awaiting reply ───────────────────────────


def list_awaiting_reply(days: int = 3, limit: int = 15) -> list[dict[str, Any]]:
    """Return inbox emails older than `days` that likely need a reply.

    MVP heuristic:
      - Fetch inbox emails older than `days` (SINCE cutoff)
      - Exclude emails we've already replied to (check Sent folder for
        matching recipients in the same date range)
      - Return oldest first (most urgent)

    Future: full In-Reply-To / References thread tracking.
    """
    if not is_configured():
        raise RuntimeError("Mail not configured.")

    backend = _backend_name()
    if backend == "himalaya":
        return _awaiting_reply_himalaya(days, limit)
    return _awaiting_reply_imap(days, limit)


def _awaiting_reply_imap(days: int, limit: int) -> list[dict[str, Any]]:
    import imaplib, ssl

    host = _imap_host()
    port = _imap_port()
    user = _imap_email()
    pwd = _imap_password()

    if not (host and user and pwd):
        raise RuntimeError("IMAP not configured.")

    ctx = ssl.create_default_context()
    try:
        conn = imaplib.IMAP4_SSL(host, port, ssl_context=ctx, timeout=_IMAP_TIMEOUT)
    except (OSError, imaplib.IMAP4.error) as e:
        raise RuntimeError(f"IMAP connection failed: {host}:{port} — {e}")
    try:
        try:
            conn.login(user, pwd)
        except imaplib.IMAP4.error as e:
            raise RuntimeError(f"IMAP login failed for {user}: {e}")
        typ, _ = conn.select("INBOX", readonly=True)
        if typ != "OK":
            raise RuntimeError(f"IMAP: cannot open INBOX")

        # Get old inbox emails
        cutoff = _now_utc() - timedelta(days=days)
        since_str = cutoff.strftime("%d-%b-%Y")
        # Get emails BEFORE cutoff (older than N days)
        # IMAP: use BEFORE + NOT SINCE to get older emails
        typ, data = conn.search(None, f"(BEFORE {since_str})")
        if typ != "OK":
            return []

        all_ids = data[0].split()
        if not all_ids:
            return []

        # Get Sent folder sender addresses for quick exclusion
        sent_addrs: set[str] = set()
        try:
            conn.select("[Gmail]/Sent Mail", readonly=True)
        except Exception:
            try:
                conn.select("Sent", readonly=True)
            except Exception:
                pass  # Sent folder not available
        else:
            sent_since = (_now_utc() - timedelta(days=days + 7)).strftime("%d-%b-%Y")
            typ2, sent_data = conn.search(None, f"(SINCE {sent_since})")
            if typ2 == "OK" and sent_data[0]:
                sent_ids = sent_data[0].split()
                # Sample recent sent to get recipient addresses
                for sid in sent_ids[-30:]:
                    try:
                        typ3, sd = conn.fetch(sid, "(BODY.PEEK[HEADER.FIELDS (TO)])")
                        if typ3 == "OK" and sd and sd[0]:
                            raw_to = sd[0][1]
                            if raw_to:
                                msg_to = _email.message_from_bytes(raw_to)
                                to_raw = msg_to.get("To", "")
                                _, addr = email.utils.parseaddr(to_raw)
                                if addr:
                                    sent_addrs.add(addr.lower())
                    except Exception:
                        pass
            conn.select("INBOX", readonly=True)

        # Process oldest first
        results: list[dict[str, Any]] = []
        for mid in all_ids[-limit:]:
            typ, msg_data = conn.fetch(mid, "(BODY.PEEK[HEADER.FIELDS (FROM SUBJECT DATE)])")
            if typ != "OK" or not msg_data or not msg_data[0]:
                continue
            raw = msg_data[0][1]
            if not raw:
                continue
            msg = _email.message_from_bytes(raw)
            from_name, from_addr = _parse_from(msg.get("From", ""))
            subject = _decode_header(msg.get("Subject", "(без темы)"))
            date_str = msg.get("Date", "")

            # Skip if we already replied (sender in Sent)
            if from_addr.lower() in sent_addrs:
                continue

            results.append({
                "id": mid.decode() if isinstance(mid, bytes) else str(mid),
                "from_addr": from_addr,
                "from_name": from_name,
                "subject": subject,
                "date_iso": "",
                "date_display": date_str[:16] if date_str else "",
                "snippet": "",
            })

            if len(results) >= limit:
                break

        return results
    finally:
        try:
            conn.logout()
        except Exception:
            pass


def _awaiting_reply_himalaya(days: int, limit: int) -> list[dict[str, Any]]:
    # himalaya doesn't have BEFORE search easily, just list recent and filter
    result = subprocess.run(
        ["himalaya", "envelope", "list", "--page-size", str(limit * 3), "--output", "json"],
        capture_output=True, text=True, timeout=20,
    )
    if result.returncode != 0:
        raise RuntimeError(f"himalaya failed: {result.stderr.strip()}")

    import json
    try:
        raw = json.loads(result.stdout)
    except json.JSONDecodeError:
        return []
    if not isinstance(raw, list):
        return []

    cutoff = _now_utc() - timedelta(days=days)
    cutoff_ts = cutoff.timestamp()
    results: list[dict[str, Any]] = []
    for env in raw:
        date_str = env.get("date", "")
        try:
            dt = _parse_himalaya_date(date_str)
            if not dt or dt.timestamp() > cutoff_ts:
                continue
        except Exception:
            continue

        from_raw = env.get("from", "")
        from_name, from_addr = _parse_from(from_raw)
        results.append({
            "id": str(env.get("id", "")),
            "from_addr": from_addr,
            "from_name": from_name,
            "subject": env.get("subject", "(без темы)"),
            "date_iso": date_str,
            "date_display": date_str[:16] if date_str else "",
            "snippet": "",
        })
        if len(results) >= limit:
            break
    return results


# ── Formatting for Telegram ────────────────────────────────────


# ── Classification ──────────────────────────────────────────────

_LABEL_EMOJI: dict[str, str] = {
    "important": "\u0001f534",
    "newsletter": "\u0001f4f0",
    "other": "\u26aa\ufe0f",
}

_RULES_NEWSLETTER_DOMAINS: frozenset[str] = frozenset({
    "noreply", "no-reply", "no_reply", "noreply@", "mailer", "bounce",
    "notifications", "notification", "updates@", "news@", "digest@",
    "marketing@", "team@slack", "calendar-notification",
})

_RULES_NEWSLETTER_WORDS: frozenset[str] = frozenset({
    "unsubscribe", "отписаться", "отписка", "рассылка",
    "newsletter", "bulletin",
})

_RULES_IMPORTANT_DOMAINS: frozenset[str] = frozenset({
    "gmail.com", "yandex.ru", "mail.ru", "icloud.com", "proton.me",
})

_RULES_IMPORTANT_KEYWORDS: frozenset[str] = frozenset({
    "срочно", "urgent", "asap", "важно", "important",
    "договор", "contract", "счёт", "invoice", "оплата", "payment",
    "подпись", "sign", "согласование", "approval",
})


def classify_mail(subject: str, from_addr: str, snippet: str = "") -> str:
    """Classify an email as important, newsletter, or other.

    Uses rules-based detection. LLM mode deferred to S16-llm.
    """
    subj_lower = subject.lower()
    addr_lower = from_addr.lower()

    # ── Newsletter detection ──
    # Unsubscribe link in snippet
    if any(w in snippet.lower() for w in _RULES_NEWSLETTER_WORDS if len(snippet) > 0):
        return "newsletter"
    if any(w in subj_lower for w in _RULES_NEWSLETTER_WORDS):
        return "newsletter"
    # Common newsletter sender patterns
    for nd in _RULES_NEWSLETTER_DOMAINS:
        if nd in addr_lower:
            return "newsletter"
    # Subject patterns
    if any(subj_lower.startswith(p) for p in ("fw:", "fwd:", "re:", "авто:")):
        pass  # forwarded — might still be important

    # ── Important detection ──
    # Keywords in subject
    for kw in _RULES_IMPORTANT_KEYWORDS:
        if kw in subj_lower:
            return "important"
    # Personal domains (if not newsletter)
    domain = addr_lower.split("@")[-1] if "@" in addr_lower else ""
    if domain in _RULES_IMPORTANT_DOMAINS and addr_lower not in _RULES_NEWSLETTER_DOMAINS:
        return "important"

    return "other"


def format_mail_list(emails: list[dict[str, Any]], hours: int = 12) -> str:
    """Format mail list with classification prefixes."""
    if not emails:
        return f"\u0001f4ed Нет новых писем за {hours} ч."

    # Group by label
    important = [m for m in emails if m.get("label", classify_mail(
        m.get("subject", ""), m.get("from_addr", ""), m.get("snippet", ""),
    )) == "important"]
    newsletters = [m for m in emails if m.get("label", classify_mail(
        m.get("subject", ""), m.get("from_addr", ""), m.get("snippet", ""),
    )) == "newsletter"]
    other = [m for m in emails if m.get("label", classify_mail(
        m.get("subject", ""), m.get("from_addr", ""), m.get("snippet", ""),
    )) not in ("important", "newsletter")]

    lines = [f"\u0001f4e7 Почта за {hours} ч ({len(emails)}):", ""]
    idx = 0

    if important:
        lines.append("\u0001f534 Важное:")
        for m in important[:5]:
            idx += 1
            lines.append(_format_one(idx, m))
    if newsletters:
        lines.append(f"\u0001f4f0 Рассылки ({len(newsletters)}):")
        for m in newsletters[:3]:
            idx += 1
            lines.append(_format_one(idx, m))
    if other:
        lines.append("\u26aa\ufe0f Прочее:")
        for m in other[:7]:
            idx += 1
            lines.append(_format_one(idx, m))

    remaining = len(emails) - idx
    if remaining > 0:
        lines.append(f"\n  ... и ещё {remaining}")

    return "\n".join(lines)


def _format_one(idx: int, m: dict) -> str:
    date = m.get("date_display", "") or m.get("date_iso", "") or "?"
    sender = m.get("from_name", "") or m.get("from_addr", "")
    subject = m.get("subject", "")
    if len(subject) > 55:
        subject = subject[:52] + "..."
    return f"{idx}. [{date}] {sender}\n   {subject}"


def fetch_body(mail_id: str) -> dict[str, str]:
    """Fetch full email body by IMAP UID.

    Returns: {subject, from_addr, from_name, date, body_text}
    Raises RuntimeError if not configured.
    """
    if not is_configured():
        raise RuntimeError("Mail not configured.")

    backend = _backend_name()
    if backend == "himalaya":
        return _fetch_body_himalaya(mail_id)
    return _fetch_body_imap(mail_id)


def _fetch_body_imap(mail_id: str) -> dict[str, str]:
    import imaplib, ssl

    host = _imap_host()
    port = _imap_port()
    user = _imap_email()
    pwd = _imap_password()

    ctx = ssl.create_default_context()
    try:
        conn = imaplib.IMAP4_SSL(host, port, ssl_context=ctx, timeout=_IMAP_TIMEOUT)
    except (OSError, imaplib.IMAP4.error) as e:
        raise RuntimeError(f"IMAP connection failed: {host}:{port} — {e}")
    try:
        try:
            conn.login(user, pwd)
        except imaplib.IMAP4.error as e:
            raise RuntimeError(f"IMAP login failed for {user}: {e}")
        typ, _ = conn.select("INBOX", readonly=True)
        if typ != "OK":
            raise RuntimeError(f"IMAP: cannot open INBOX")

        # Fetch by UID
        mid = mail_id.encode() if isinstance(mail_id, str) else mail_id
        typ, msg_data = conn.fetch(mid, "(BODY.PEEK[])")
        if typ != "OK" or not msg_data or not msg_data[0]:
            raise RuntimeError(f"Message not found: {mail_id}")

        raw = msg_data[0][1]
        if not raw:
            raise RuntimeError(f"Empty message body: {mail_id}")

        msg = _email.message_from_bytes(raw)
        from_name, from_addr = _parse_from(msg.get("From", ""))
        subject = _decode_header(msg.get("Subject", "(без темы)"))
        date_str = msg.get("Date", "")

        # Extract plain text body
        body = _extract_body(msg)

        return {
            "subject": subject,
            "from_addr": from_addr,
            "from_name": from_name,
            "date": date_str,
            "body_text": body[:3000],  # limit for LLM
        }
    finally:
        try:
            conn.logout()
        except Exception:
            pass


def _fetch_body_himalaya(mail_id: str) -> dict[str, str]:
    result = subprocess.run(
        ["himalaya", "envelope", "read", mail_id, "--output", "json"],
        capture_output=True, text=True, timeout=20,
    )
    if result.returncode != 0:
        raise RuntimeError(f"himalaya read failed: {result.stderr.strip()}")

    import json
    try:
        raw = json.loads(result.stdout)
    except json.JSONDecodeError:
        raise RuntimeError("himalaya: invalid JSON")

    if not isinstance(raw, dict):
        raise RuntimeError("himalaya: unexpected output")

    from_raw = raw.get("from", "")
    from_name, from_addr = _parse_from(from_raw)

    body = raw.get("body", "") or raw.get("text_body", "") or ""
    if isinstance(body, list):
        body = "\n".join(str(b) for b in body)

    return {
        "subject": raw.get("subject", "(без темы)"),
        "from_addr": from_addr,
        "from_name": from_name,
        "date": raw.get("date", ""),
        "body_text": str(body)[:3000],
    }


def _extract_body(msg) -> str:
    """Extract plain text from email.Message, preferring text/plain."""
    if msg.is_multipart():
        for part in msg.walk():
            ct = part.get_content_type()
            if ct == "text/plain":
                try:
                    payload = part.get_payload(decode=True)
                    if payload:
                        charset = part.get_content_charset() or "utf-8"
                        return payload.decode(charset, errors="replace")
                except Exception:
                    pass
        # Fallback: take first text part
        for part in msg.walk():
            if part.get_content_maintype() == "text":
                try:
                    payload = part.get_payload(decode=True)
                    if payload:
                        charset = part.get_content_charset() or "utf-8"
                        return payload.decode(charset, errors="replace")
                except Exception:
                    pass
    else:
        try:
            payload = msg.get_payload(decode=True)
            if payload:
                charset = msg.get_content_charset() or "utf-8"
                return payload.decode(charset, errors="replace")
        except Exception:
            pass
    return "(тело письма недоступно)"


# ── Send (with DRY_RUN) ────────────────────────────────────────


def _is_dry_run() -> bool:
    return os.environ.get("SECRETARY_MAIL_DRY_RUN", "1").strip() != "0"


def send_mail(to_addr: str, subject: str, body: str) -> dict[str, Any]:
    """Send email. If SECRETARY_MAIL_DRY_RUN=1 (default), logs only.

    Returns: {sent: bool, dry_run: bool, details: str}
    """
    dry = _is_dry_run()
    if dry:
        logger.info(
            "[DRY_RUN] Would send to=%s subject=%s body_len=%d",
            to_addr, subject, len(body),
        )
        return {
            "sent": True,
            "dry_run": True,
            "details": f"[DRY_RUN] would send to {to_addr}: «{subject}»",
        }

    backend = _backend_name()
    if backend == "himalaya":
        return _send_himalaya(to_addr, subject, body)
    return _send_smtp(to_addr, subject, body)


def _send_smtp(to_addr: str, subject: str, body: str) -> dict[str, Any]:
    import smtplib
    import ssl
    from email.mime.text import MIMEText

    host = os.environ.get("SECRETARY_MAIL_SMTP_HOST", "").strip()
    port = int(os.environ.get("SECRETARY_MAIL_SMTP_PORT", "587"))
    user = _imap_email()  # reuse IMAP email as SMTP login
    pwd = os.environ.get("SECRETARY_MAIL_SMTP_PASSWORD", "").strip() or _imap_password()

    if not host:
        # Auto-derive from IMAP host
        imap_host = _imap_host()
        if "gmail" in imap_host:
            host, port = "smtp.gmail.com", 587
        elif "yandex" in imap_host:
            host, port = "smtp.yandex.ru", 587
        elif "mail.ru" in imap_host:
            host, port = "smtp.mail.ru", 587
        else:
            return {"sent": False, "dry_run": False, "details": "SMTP host not configured. Set SECRETARY_MAIL_SMTP_HOST."}

    msg = MIMEText(body, "plain", "utf-8")
    msg["From"] = user
    msg["To"] = to_addr
    msg["Subject"] = subject

    ctx = ssl.create_default_context()
    try:
        conn = smtplib.SMTP(host, port, timeout=_SMTP_TIMEOUT)
        conn.starttls(context=ctx)
        conn.login(user, pwd)
        conn.sendmail(user, [to_addr], msg.as_string())
        conn.quit()
        logger.info("Sent mail to=%s subject=%s", to_addr, subject)
        return {"sent": True, "dry_run": False, "details": f"Sent to {to_addr}: «{subject}»"}
    except smtplib.SMTPAuthenticationError:
        return {"sent": False, "dry_run": False, "details": f"SMTP auth failed for {user}"}
    except smtplib.SMTPConnectError as e:
        return {"sent": False, "dry_run": False, "details": f"SMTP connection failed: {host}:{port} — {e}"}
    except (smtplib.SMTPException, OSError) as e:
        logger.error("SMTP send failed: %s", e)
        return {"sent": False, "dry_run": False, "details": f"SMTP error: {e}"}


def _send_himalaya(to_addr: str, subject: str, body: str) -> dict[str, Any]:
    """Send via himalaya CLI."""
    import tempfile
    import os as _os

    with tempfile.NamedTemporaryFile(mode="w", suffix=".eml", delete=False, encoding="utf-8") as f:
        f.write(f"From: {_imap_email()}\n")
        f.write(f"To: {to_addr}\n")
        f.write(f"Subject: {subject}\n")
        f.write("Content-Type: text/plain; charset=utf-8\n\n")
        f.write(body)
        tmp_path = f.name

    try:
        result = subprocess.run(
            ["himalaya", "message", "send", "--raw", tmp_path],
            capture_output=True, text=True, timeout=30,
        )
        if result.returncode != 0:
            return {"sent": False, "dry_run": False, "details": f"himalaya: {result.stderr.strip()}"}
        logger.info("himalaya sent to=%s subject=%s", to_addr, subject)
        return {"sent": True, "dry_run": False, "details": f"Sent to {to_addr}: «{subject}»"}
    finally:
        try:
            _os.unlink(tmp_path)
        except Exception:
            pass
