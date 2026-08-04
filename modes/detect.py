
"""
Natural-language + slash-command mode-intent detector.

Parses user text into ModeIntent(mode, confidence, raw).
No heavy NLP — regex + alias dictionary.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Optional

_VALID_MODES = frozenset({"dev", "secretary"})


@dataclass
class ModeIntent:
    """Detected intent to switch or query mode."""

    mode: str           # "dev" | "secretary" | "status"
    confidence: float   # 0.0–1.0
    raw: str            # original user text
    is_slash: bool = False


# ── Slash command ─────────────────────────────────────────────

_SLASH_RE = re.compile(
    r"^/mode\s+(dev|secretary|status)\s*$",
    re.IGNORECASE,
)

# ── Natural-language triggers ─────────────────────────────────

# Ordered by specificity: longer/imperative patterns first
_NL_PATTERNS: list[tuple[re.Pattern, str, float]] = [
    # Explicit switch commands
    (re.compile(r"перейди\s+(?:в\s+)?режим\s+(разработк[иу]|проекта|dev)", re.IGNORECASE), "dev", 0.95),
    (re.compile(r"перейди\s+(?:в\s+)?режим\s+(секретар[яь]|менеджер[а]|архив[а]|secretary)", re.IGNORECASE), "secretary", 0.95),
    (re.compile(r"переключись?\s+(?:в\s+)?режим\s+(разработк[иу]|проекта|dev)", re.IGNORECASE), "dev", 0.95),
    (re.compile(r"переключись?\s+(?:в\s+)?режим\s+(секретар[яь]|менеджер[а]|архив[а]|secretary)", re.IGNORECASE), "secretary", 0.95),
    (re.compile(r"смени\s+режим\s+(?:на\s+)?(разработк[уи]|проект|dev)", re.IGNORECASE), "dev", 0.95),
    (re.compile(r"смени\s+режим\s+(?:на\s+)?(секретар[яь]|менеджер[а]|архив|secretary)", re.IGNORECASE), "secretary", 0.95),

    # Switch to mode
    (re.compile(r"switch\s+(?:to\s+)?(?:the\s+)?(dev|developer|coding)\s*mode", re.IGNORECASE), "dev", 0.95),
    (re.compile(r"switch\s+(?:to\s+)?(?:the\s+)?(secretary|manager|archive)\s*mode", re.IGNORECASE), "secretary", 0.95),

    # Short imperative
    (re.compile(r"^(?:стань|будь|работай\s+как)\s+(секретар[ёе]м|менеджером|архивариусом)", re.IGNORECASE), "secretary", 0.90),
    (re.compile(r"^(?:стань|будь|работай\s+как)\s+(разработчик[а-я]*|dev)", re.IGNORECASE), "dev", 0.90),
    (re.compile(r"^режим\s+(разработк[иа]|проекта|dev)\b", re.IGNORECASE), "dev", 0.90),
    (re.compile(r"^режим\s+(секретар[яь]|менеджер[а]|архив[а]|secretary)\b", re.IGNORECASE), "secretary", 0.90),
    (re.compile(r"^mode\s+(dev|developer|coding)\b", re.IGNORECASE), "dev", 0.90),
    (re.compile(r"^mode\s+(secretary|manager|archive)\b", re.IGNORECASE), "secretary", 0.90),

    # "откройся как"
    (re.compile(r"откройся\s+как\s+(секретарь|менеджер|архивариус)", re.IGNORECASE), "secretary", 0.88),
    (re.compile(r"откройся\s+как\s+(разработчик|dev)", re.IGNORECASE), "dev", 0.88),

    # "вернись в"
    (re.compile(r"вернись\s+в\s+(?:режим\s+)?разработк[уи]", re.IGNORECASE), "dev", 0.90),
    (re.compile(r"вернись\s+в\s+(?:режим\s+)?секретар[яь]", re.IGNORECASE), "secretary", 0.90),
    (re.compile(r"go\s+back\s+to\s+(?:the\s+)?dev\s*mode", re.IGNORECASE), "dev", 0.88),
    (re.compile(r"go\s+back\s+to\s+(?:the\s+)?secretary\s*mode", re.IGNORECASE), "secretary", 0.88),

    # Alias-based: single word match + context
    (re.compile(r"^(?:хочу\s+)?(?:в\s+)?режим\s+(разработчик[а]?)", re.IGNORECASE), "dev", 0.85),
    (re.compile(r"^(?:хочу\s+)?(?:в\s+)?режим\s+(секретар[яь]|менеджер[а]?)", re.IGNORECASE), "secretary", 0.85),
]

# Status check patterns
_STATUS_PATTERNS: list[tuple[re.Pattern, float]] = [
    (re.compile(r"какой\s+(?:сейчас\s+)?режим", re.IGNORECASE), 0.95),
    (re.compile(r"текущий\s+режим", re.IGNORECASE), 0.95),
    (re.compile(r"что\s+по\s+режиму", re.IGNORECASE), 0.90),
    (re.compile(r"в\s+каком\s+(?:я\s+)?режиме", re.IGNORECASE), 0.90),
    (re.compile(r"current\s+mode", re.IGNORECASE), 0.95),
    (re.compile(r"which\s+mode", re.IGNORECASE), 0.90),
    (re.compile(r"what\s+mode\s+(?:am\s+I\s+)?in", re.IGNORECASE), 0.90),
]


# ── Anti-patterns — must NOT trigger ──────────────────────────

_ANTI_PATTERNS: list[re.Pattern] = [
    # Descriptive/narrative — not commands
    re.compile(r"в\s+режиме\s+(разработки|секретаря)\s+(?:мы|я|они)\s", re.IGNORECASE),
    re.compile(r"in\s+(dev|secretary)\s+mode\s+(?:we|I|you|they)\s", re.IGNORECASE),
    # Past tense / reporting
    re.compile(r"(?:был[ао]?|работал[ао]?)\s+в\s+режиме", re.IGNORECASE),
    re.compile(r"(?:was|worked)\s+in\s+(?:dev|secretary)\s+mode", re.IGNORECASE),
]


def _normalize(text: str) -> str:
    """Casefold + replace ё -> е for fuzzy matching."""
    return text.casefold().replace("ё", "е")


def detect_mode_intent(text: str) -> Optional[ModeIntent]:
    """Detect a mode-switch or mode-query intent from user text.

    Returns None if no mode-related intent detected.
    """
    if not text or not text.strip():
        return None

    raw = text.strip()
    normalized = _normalize(raw)

    # 1. Slash command (highest priority)
    m = _SLASH_RE.match(normalized)
    if m:
        mode = m.group(1).lower()
        if mode == "status":
            return ModeIntent(mode="status", confidence=1.0, raw=raw, is_slash=True)
        if mode in _VALID_MODES:
            return ModeIntent(mode=mode, confidence=1.0, raw=raw, is_slash=True)

    # 2. Status check (no slash)
    for pattern, conf in _STATUS_PATTERNS:
        if pattern.search(normalized):
            return ModeIntent(mode="status", confidence=conf, raw=raw)

    # 3. Anti-patterns — bail out early
    for pattern in _ANTI_PATTERNS:
        if pattern.search(normalized):
            return None

    # 4. NL switch patterns — first match wins (ordered by specificity)
    for pattern, mode, conf in _NL_PATTERNS:
        if pattern.search(normalized):
            return ModeIntent(mode=mode, confidence=conf, raw=raw)

    return None
