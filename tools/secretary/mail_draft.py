"""
Draft generator for secretary mail replies.

Modes:
  llm      — call configured LLM (SECRETARY_MAIL_DRAFT_MODE=llm, default)
  template — 5-rule template (fallback on LLM error)

Env:
  SECRETARY_MAIL_DRAFT_MODE         llm | template  (default: llm)
  SECRETARY_LLM_API_KEY             API key (falls back to OPENAI_API_KEY)
  SECRETARY_LLM_BASE_URL            Base URL (falls back to https://api.openai.com/v1)
  SECRETARY_LLM_MODEL               Model (falls back to gpt-4o-mini)
"""

from __future__ import annotations

import json
import logging
import os
from datetime import datetime, timezone
from typing import Any
from urllib.request import Request, urlopen

logger = logging.getLogger(__name__)

_LLM_TIMEOUT = 20  # seconds for draft generation
_MAX_BODY_CHARS = 6000


def _draft_mode() -> str:
    return os.environ.get("SECRETARY_MAIL_DRAFT_MODE", "llm").strip().lower()


def _llm_api_key() -> str:
    return os.environ.get("SECRETARY_LLM_API_KEY", "").strip() or os.environ.get("OPENAI_API_KEY", "").strip()


def _llm_base_url() -> str:
    url = os.environ.get("SECRETARY_LLM_BASE_URL", "").strip()
    if url:
        return url.rstrip("/")
    return "https://api.openai.com/v1"


def _llm_model() -> str:
    return os.environ.get("SECRETARY_LLM_MODEL", "gpt-4o-mini").strip()


def generate_draft(
    mail_dict: dict[str, str],
    sender_name: str = "",
) -> str:
    """Generate a reply draft. Uses LLM if configured, falls back to template."""
    if _draft_mode() == "template" or not _llm_api_key():
        return _template_draft(mail_dict, sender_name)

    try:
        return _llm_draft(mail_dict, sender_name)
    except Exception as e:
        logger.warning("LLM draft failed, falling back to template: %s", e)
        return _template_draft(mail_dict, sender_name)


def _llm_draft(mail_dict: dict[str, str], sender_name: str) -> str:
    """Call LLM API to generate a draft reply."""
    from_name = mail_dict.get("from_name", "") or mail_dict.get("from_addr", "")
    original_subject = mail_dict.get("subject", "")
    body = (mail_dict.get("body_text", "") or "")[:_MAX_BODY_CHARS]

    # Detect language of original
    has_cyrillic = any("а" <= c.lower() <= "я" or c == "ё" for c in body)
    lang_hint = "Russian" if has_cyrillic else "English"

    system_prompt = (
        f"You are an executive secretary. Write a reply email on behalf of your boss.\n"
        f"Rules:\n"
        f"- Be concise (3-6 sentences max)\n"
        f"- Match the tone: business-like but warm\n"
        f"- Reply in {lang_hint}\n"
        f"- Sign with: {sender_name or '—'}\n"
        f"- For 'Re:' subjects, keep the prefix\n"
        f"- Do NOT include 'Subject:' line — just the body\n"
        f"- Do NOT use placeholders like [Name]"
    )

    user_prompt = (
        f"Original email from: {from_name}\n"
        f"Subject: {original_subject}\n\n"
        f"Body:\n{body[:4000]}\n\n"
        f"Write a reply."
    )

    reply = _call_llm(system_prompt, user_prompt)

    # Build final draft
    reply_subject = original_subject
    if not reply_subject.lower().startswith("re:"):
        reply_subject = f"Re: {reply_subject}"

    sig = sender_name or ""
    full = f"Тема: {reply_subject}\n\n{reply.strip()}"
    if sig:
        full += f"\n\n—\n{sig}"
    return full


def _call_llm(system: str, user: str) -> str:
    """Single-turn chat completion via OpenAI-compatible API."""
    api_key = _llm_api_key()
    if not api_key:
        raise RuntimeError("LLM API key not configured")

    url = f"{_llm_base_url()}/chat/completions"
    body = json.dumps({
        "model": _llm_model(),
        "messages": [
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ],
        "max_tokens": 500,
        "temperature": 0.7,
    }).encode("utf-8")

    req = Request(url, data=body, headers={
        "Content-Type": "application/json",
        "Authorization": f"Bearer {api_key}",
    })

    try:
        with urlopen(req, timeout=_LLM_TIMEOUT) as resp:
            data = json.loads(resp.read().decode("utf-8"))
    except Exception as e:
        raise RuntimeError(f"LLM API call failed: {e}")

    choices = data.get("choices", [])
    if not choices:
        raise RuntimeError("LLM returned no choices")

    content = choices[0].get("message", {}).get("content", "")
    if not content:
        raise RuntimeError("LLM returned empty content")

    return content.strip()


# ── Template fallback ──────────────────────────────────────────


def _template_draft(mail_dict: dict[str, str], sender_name: str = "") -> str:
    """Original 5-template draft generator (S8)."""
    from_name = mail_dict.get("from_name", "") or mail_dict.get("from_addr", "")
    original_subject = mail_dict.get("subject", "")
    body = (mail_dict.get("body_text", "") or "")[:500]

    first_name = from_name.split()[0] if from_name else "коллега"
    greeting = f"Здравствуйте, {first_name}!"

    reply_subject = original_subject
    if not reply_subject.lower().startswith("re:"):
        reply_subject = f"Re: {reply_subject}"

    draft_body = _build_template_body(body, from_name)

    sig = sender_name or "—"
    if sig and not sig.startswith("—"):
        sig = f"—\n{sig}"

    return (
        f"Тема: {reply_subject}\n\n"
        f"{greeting}\n\n"
        f"{draft_body}\n\n"
        f"С уважением,\n{sig}"
    )


def _build_template_body(original_body: str, from_name: str) -> str:
    first_name = from_name.split()[0] if from_name else ""
    if len(original_body.strip()) < 10:
        return "Спасибо за ваше письмо. Я ознакомился и отвечу подробнее в ближайшее время."

    lower = original_body.lower()
    has_question = "?" in original_body
    has_meeting = any(w in lower for w in ["встреч", "meeting", "созвон", "call", "обсуд"])
    has_docs = any(w in lower for w in ["файл", "file", "документ", "doc", "pdf", "прилагаю", "attach"])
    has_thanks = any(w in lower for w in ["спасиб", "thank", "благодар"])

    if has_meeting:
        return f"Спасибо за информацию о встрече. Я посмотрю своё расписание и вернусь с подтверждением времени."
    elif has_docs:
        return f"Спасибо, документы получил. Я изучу материалы и отвечу с комментариями."
    elif has_thanks:
        return f"Пожалуйста{'!' if first_name else '!'} Рад был помочь. Если появятся ещё вопросы — обращайтесь."
    elif has_question:
        return f"Спасибо за ваш вопрос. Я подготовлю ответ и вернусь к вам в ближайшее время."
    else:
        return f"Спасибо за ваше письмо. Я принял информацию к сведению и отвечу при необходимости."


# ── Trust policy ────────────────────────────────────────────────


def is_trusted(to_addr: str, prefs: dict) -> bool:
    """Check if to_addr is in trusted_emails or trusted_domains."""
    addr = to_addr.strip().lower()
    if not addr or "@" not in addr:
        return True  # Can't determine — allow

    trusted_emails = prefs.get("trusted_emails", []) or []
    trusted_domains = prefs.get("trusted_domains", []) or []

    if addr in (e.strip().lower() for e in trusted_emails if e):
        return True

    domain = addr.split("@")[-1].lower()
    if domain in (d.strip().lower() for d in trusted_domains if d):
        return True

    return False


def trust_domain(to_addr: str, prefs: dict) -> dict:
    """Add to_addr's domain to trusted_domains. Returns updated prefs dict."""
    addr = to_addr.strip().lower()
    if "@" not in addr:
        return prefs

    domain = addr.split("@")[-1].lower()
    domains: list[str] = list(prefs.get("trusted_domains", []) or [])
    if domain not in domains:
        domains.append(domain)
    prefs["trusted_domains"] = domains
    return prefs
