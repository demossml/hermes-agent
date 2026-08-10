"""
Draft generator for secretary mail replies.

Lightweight template — no LLM call in control-layer mode.
LLM-based drafting can be added as an optional upgrade.
"""

from __future__ import annotations

from datetime import datetime, timezone


def generate_draft(
    mail_dict: dict[str, str],
    sender_name: str = "",
) -> str:
    """Generate a draft reply.

    mail_dict: {subject, from_addr, from_name, body_text}
    sender_name: display_name of the secretary user

    Returns a plain-text draft.
    """
    from_name = mail_dict.get("from_name", "") or mail_dict.get("from_addr", "")
    original_subject = mail_dict.get("subject", "")
    body = mail_dict.get("body_text", "")[:500]

    # Build greeting
    first_name = from_name.split()[0] if from_name else "коллега"
    greeting = f"Здравствуйте, {first_name}!"

    # Build subject
    reply_subject = original_subject
    if not reply_subject.lower().startswith("re:"):
        reply_subject = f"Re: {reply_subject}"

    # Generate body based on original content
    draft_body = _build_body(body, from_name)

    # Signature
    sig = sender_name or "—"
    if sig and not sig.startswith("—"):
        sig = f"—\n{sig}"

    return (
        f"Тема: {reply_subject}\n\n"
        f"{greeting}\n\n"
        f"{draft_body}\n\n"
        f"С уважением,\n{sig}"
    )


def _build_body(original_body: str, from_name: str) -> str:
    """Build reply body from original message content."""
    first_name = from_name.split()[0] if from_name else ""

    # If original body is very short or empty
    if len(original_body.strip()) < 10:
        return "Спасибо за ваше письмо. Я ознакомился и отвечу подробнее в ближайшее время."

    # Detect intent from original body
    lower = original_body.lower()
    has_question = "?" in original_body
    has_meeting = any(w in lower for w in ["встреч", "meeting", "созвон", "call", "обсуд"])
    has_docs = any(w in lower for w in ["файл", "file", "документ", "doc", "pdf", "прилагаю", "attach"])
    has_thanks = any(w in lower for w in ["спасиб", "thank", "благодар"])

    if has_meeting:
        return (
            f"Спасибо за информацию о встрече. "
            f"Я посмотрю своё расписание и вернусь с подтверждением времени."
        )
    elif has_docs:
        return (
            f"Спасибо, документы получил. "
            f"Я изучу материалы и отвечу с комментариями."
        )
    elif has_thanks:
        return (
            f"Пожалуйста{', ' + first_name if first_name else ''}! "
            f"Рад был помочь. Если появятся ещё вопросы — обращайтесь."
        )
    elif has_question:
        return (
            f"Спасибо за ваш вопрос. "
            f"Я подготовлю ответ и вернусь к вам в ближайшее время."
        )
    else:
        return (
            f"Спасибо за ваше письмо. "
            f"Я принял информацию к сведению и отвечу при необходимости."
        )


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()
