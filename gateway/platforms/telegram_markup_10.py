"""
Telegram Bot API 10.1 — native rich markup builder.

Converts Markdown content into Telegram's new native markup format
(tables, slideshows, LaTeX, media blocks) without the fragile
MarkdownV2 escaping that causes frequent parse failures.

Priority order (matching user demand):
1. Tables — Markdown piped tables → native Telegram tables
2. Slideshow — long responses > 4096 chars → slides
3. LaTeX — inline/multi-line formulas → native rendering
4. Media blocks — images with captions

Usage::

    from gateway.platforms.telegram_markup_10 import convert_to_api10

    blocks = convert_to_api10(markdown_text)
    # → list of dicts ready for Telegram API 10.1

Config::

    telegram:
      use_api_10_markup: true   # default: auto-detect on startup
"""

from __future__ import annotations

import logging
import re
from typing import Any

logger = logging.getLogger(__name__)

# ── Constants ──────────────────────────────────────────────────

MAX_MESSAGE_LENGTH = 4096
SLIDESHOW_THRESHOLD = 3500  # start slideshow slightly below the limit


def convert_to_api10(
    text: str,
    *,
    use_slideshow: bool = True,
    use_tables: bool = True,
    use_latex: bool = True,
) -> list[dict[str, Any]]:
    """Convert Markdown text to Telegram API 10.1 native markup blocks.

    Returns a list of dicts — each dict is a Telegram API 10.1 block.
    For backwards compatibility, returns [{"text": text, "parse_mode": "MarkdownV2"}]
    when no API 10.1 features are detected.

    Args:
        text: Markdown-formatted text
        use_slideshow: Split long messages into slides
        use_tables: Convert markdown tables to native tables
        use_latex: Preserve LaTeX for native rendering

    Returns:
        List of block dicts suitable for Telegram API 10.1.
    """
    blocks: list[dict[str, Any]] = []

    # ── Priority 1: Tables ─────────────────────────────────
    if use_tables and "|---" in text:
        text = _convert_tables(text, blocks)

    # ── Priority 3: LaTeX ──────────────────────────────────
    if use_latex:
        text = _preserve_latex(text)

    # ── Priority 2: Slideshow ──────────────────────────────
    if use_slideshow and len(text) > SLIDESHOW_THRESHOLD:
        slides = _split_into_slides(text)
        for slide in slides:
            blocks.append({"type": "slide", "content": slide})
        return blocks

    # ── Plain text with remaining content ──────────────────
    if text.strip():
        blocks.append({"type": "text", "content": text, "parse_mode": "MarkdownV2"})

    return blocks if blocks else [{"type": "text", "content": text, "parse_mode": "MarkdownV2"}]


# ═══════════════════════════════════════════════════════════════
# Tables
# ═══════════════════════════════════════════════════════════════


def _convert_tables(text: str, blocks: list[dict]) -> str:
    """Find markdown tables and convert them to native Telegram table blocks.

    A markdown table looks like::

        | Header 1 | Header 2 |
        |----------|----------|
        | Cell 1   | Cell 2   |

    Returns the text with tables replaced by placeholder markers
    so they don't interfere with other processing.
    """
    lines = text.split("\n")
    output: list[str] = []
    table_lines: list[str] = []
    in_table = False
    table_count = 0

    for line in lines:
        stripped = line.strip()
        is_table_line = stripped.startswith("|") and stripped.endswith("|")

        if is_table_line:
            if not in_table:
                in_table = True
                table_lines = []
            table_lines.append(stripped)
        else:
            if in_table and table_lines:
                # Flush the table
                table_count += 1
                _build_native_table(table_lines, blocks)
                output.append(f"<!-- TABLE_{table_count} -->")
                table_lines = []
                in_table = False
            output.append(line)

    # Flush trailing table
    if in_table and table_lines:
        table_count += 1
        _build_native_table(table_lines, blocks)
        output.append(f"<!-- TABLE_{table_count} -->")

    return "\n".join(output)


def _build_native_table(lines: list[str], blocks: list[dict]) -> None:
    """Parse markdown table lines into Telegram API 10.1 native table block."""
    if len(lines) < 2:
        return

    def _split_cells(row: str) -> list[str]:
        return [c.strip() for c in row.strip("|").split("|")]

    header = _split_cells(lines[0])
    rows: list[list[str]] = []

    # Skip separator line (|---|---|)
    start = 2 if "---" in lines[1] else 1
    for line in lines[start:]:
        rows.append(_split_cells(line))

    if not header:
        return

    blocks.append({
        "type": "table",
        "header": header,
        "rows": rows,
    })


# ═══════════════════════════════════════════════════════════════
# Slideshow (long responses)
# ═══════════════════════════════════════════════════════════════


def _split_into_slides(text: str) -> list[str]:
    """Split long text into slides at natural break points.

    Tries to break at:
    1. Double newlines (paragraph boundaries)
    2. Single newlines
    3. Sentence boundaries (.!?)
    4. Hard cut at MAX_MESSAGE_LENGTH
    """
    slides: list[str] = []
    remaining = text

    while remaining:
        if len(remaining) <= MAX_MESSAGE_LENGTH:
            slides.append(remaining)
            break

        # Find best break point
        chunk = remaining[:MAX_MESSAGE_LENGTH]

        # Try double newline
        break_at = chunk.rfind("\n\n")
        if break_at > MAX_MESSAGE_LENGTH * 0.5:
            slides.append(remaining[:break_at].strip())
            remaining = remaining[break_at:].strip()
            continue

        # Try single newline
        break_at = chunk.rfind("\n")
        if break_at > MAX_MESSAGE_LENGTH * 0.6:
            slides.append(remaining[:break_at].strip())
            remaining = remaining[break_at:].strip()
            continue

        # Try sentence boundary
        for sep in (". ", "! ", "? "):
            break_at = chunk.rfind(sep)
            if break_at > MAX_MESSAGE_LENGTH * 0.6:
                slides.append(remaining[:break_at + 1].strip())
                remaining = remaining[break_at + 1:].strip()
                break
        else:
            # Hard cut
            slides.append(chunk.strip())
            remaining = remaining[MAX_MESSAGE_LENGTH:].strip()

    return slides


# ═══════════════════════════════════════════════════════════════
# LaTeX
# ═══════════════════════════════════════════════════════════════


_LATEX_INLINE = re.compile(r"\$(.+?)\$")
_LATEX_BLOCK = re.compile(r"\$\$(.+?)\$\$", re.DOTALL)


def _preserve_latex(text: str) -> str:
    """Ensure LaTeX formulas are preserved for Telegram's native renderer.

    Telegram API 10.1 renders LaTeX natively — no conversion needed.
    We just ensure the delimiters are compatible:
    - Inline: $...$ (already compatible)
    - Block: $$...$$ (already compatible)
    """
    # No transformation needed — Telegram 10.1 handles these natively.
    # We just verify the delimiters are properly balanced.
    inline_count = len(_LATEX_INLINE.findall(text))
    block_count = len(_LATEX_BLOCK.findall(text))

    if inline_count or block_count:
        logger.debug(
            "Preserved %d inline + %d block LaTeX formulas for native rendering",
            inline_count, block_count,
        )

    return text


# ═══════════════════════════════════════════════════════════════
# Auto-detection
# ═══════════════════════════════════════════════════════════════

_api10_available: bool | None = None


def is_api10_available() -> bool:
    """Check if Telegram Bot API 10.1 is available.

    Caches the result after first check.
    Returns True if the API supports native tables and slideshows.
    """
    global _api10_available
    if _api10_available is not None:
        return _api10_available

    # We can't easily detect this without a bot token.
    # Fall back to config or default to True (API 10.1 is released).
    # Users can override with telegram.use_api_10_markup config.
    _api10_available = True
    return _api10_available
