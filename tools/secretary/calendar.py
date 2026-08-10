"""
Calendar reader for Secretary mode — ICS URL backend.

Env vars:
  SECRETARY_CAL_ICS_URL    URL to public .ics feed (or CalDAV private URL)

API:
  list_events(days_ahead=1) -> list[dict]
  format_calendar_list(events) -> str
  is_configured() -> bool
"""

from __future__ import annotations

import logging
import os
from datetime import datetime, timedelta, timezone
from typing import Any, Optional
from urllib.request import urlopen, Request

logger = logging.getLogger(__name__)


def _now_utc() -> datetime:
    return datetime.now(timezone.utc)


def _ics_url() -> str:
    return os.environ.get("SECRETARY_CAL_ICS_URL", "").strip()


def is_configured() -> bool:
    return bool(_ics_url())


def list_events(days_ahead: int = 1) -> list[dict[str, Any]]:
    """Fetch and parse .ics feed, return events within the window.

    Returns list of: {start, end, title, location, all_day}
    Raises RuntimeError if not configured.
    """
    url = _ics_url()
    if not url:
        raise RuntimeError("Calendar not configured. Set SECRETARY_CAL_ICS_URL.")

    raw = _fetch_ics(url)
    return _parse_ics(raw, days_ahead)


def _fetch_ics(url: str) -> str:
    """Fetch .ics file with 20s timeout."""
    req = Request(url, headers={"User-Agent": "Hermes-Secretary/1.0"})
    try:
        with urlopen(req, timeout=20) as resp:
            return resp.read().decode("utf-8", errors="replace")
    except Exception as e:
        raise RuntimeError(f"Failed to fetch calendar: {e}")


def _parse_ics(raw: str, days_ahead: int) -> list[dict[str, Any]]:
    """Parse .ics VEVENT blocks. Lights-out parser — no icalendar dep."""
    now = _now_utc()
    window_end = now + timedelta(days=days_ahead)
    window_start = now.replace(hour=0, minute=0, second=0, microsecond=0)

    events: list[dict[str, Any]] = []
    blocks = _split_vevents(raw)

    for block in blocks:
        props = _parse_vevent(block)
        if not props.get("dtstart"):
            continue

        start = _parse_ics_dt(props["dtstart"])
        end = _parse_ics_dt(props.get("dtend", props["dtstart"])) if props.get("dtend") else start + timedelta(hours=1)

        if not start:
            continue

        # Check if event overlaps our window
        if end < window_start or start > window_end:
            continue

        all_day = _is_all_day(props.get("dtstart", ""))
        events.append({
            "start": start,
            "end": end,
            "title": props.get("summary", "(без названия)"),
            "location": props.get("location", ""),
            "all_day": all_day,
        })

    events.sort(key=lambda e: e["start"])
    return events


def _split_vevents(raw: str) -> list[str]:
    """Split ICS into VEVENT blocks."""
    raw = raw.replace("\r\n", "\n").replace("\r", "\n")
    blocks = []
    in_event = False
    current: list[str] = []

    for line in raw.split("\n"):
        if line.startswith("BEGIN:VEVENT"):
            in_event = True
            current = [line]
        elif line.startswith("END:VEVENT") and in_event:
            current.append(line)
            blocks.append("\n".join(current))
            in_event = False
        elif in_event:
            current.append(line)

    return blocks


def _parse_vevent(block: str) -> dict[str, str]:
    """Parse properties from a VEVENT block. Handles folded lines."""
    # Unfold: lines starting with space/tab are continuations
    lines: list[str] = []
    for line in block.split("\n"):
        if lines and (line.startswith(" ") or line.startswith("\t")):
            lines[-1] += line[1:]
        else:
            lines.append(line)

    props: dict[str, str] = {}
    for line in lines:
        # Skip BEGIN/END lines
        if line.startswith("BEGIN:") or line.startswith("END:"):
            continue
        if ":" not in line:
            continue

        # Split on first colon (properties may have params before colon)
        # DTSTART;TZID=Europe/Moscow:20260810T090000
        colon_idx = line.index(":")
        key_part = line[:colon_idx]
        value = line[colon_idx + 1:]

        # Strip parameters from key (e.g. "DTSTART;TZID=..." → "DTSTART")
        key = key_part.split(";")[0].strip().lower()

        # Unescape ICS sequences
        value = value.replace("\\n", "\n").replace("\\,", ",").replace("\\;", ";")

        if key in ("dtstart", "dtend", "summary", "location"):
            props[key] = value

    return props


def _parse_ics_dt(val: str) -> Optional[datetime]:
    """Parse ICS datetime value."""
    if not val:
        return None
    val = val.strip()

    # Format: 20260810T090000 or 20260810T090000Z
    # or date-only: 20260810
    try:
        if len(val) >= 15 and "T" in val:
            # Strip trailing Z
            val_clean = val.rstrip("Z")
            return datetime.strptime(val_clean[:15], "%Y%m%dT%H%M%S").replace(tzinfo=timezone.utc)
        elif len(val) >= 8:
            return datetime.strptime(val[:8], "%Y%m%d").replace(tzinfo=timezone.utc)
    except ValueError:
        pass
    return None


def _is_all_day(val: str) -> bool:
    """Detect all-day events: VALUE=DATE param or 8-char date."""
    if "VALUE=DATE" in val.upper():
        return True
    stripped = val.strip()
    return len(stripped) == 8 and stripped.isdigit()


def format_calendar_list(events: list[dict[str, Any]]) -> str:
    """Format events for Telegram."""
    if not events:
        return "\u0001f4c5 Нет событий."

    lines = [f"\u0001f4c5 События ({len(events)}):", ""]
    for e in events[:12]:
        start = e["start"]
        all_day = e.get("all_day", False)
        title = e.get("title", "")

        if all_day:
            date_str = start.strftime("%d.%m")
            time_str = "весь день"
        else:
            date_str = start.strftime("%d.%m")
            time_str = start.strftime("%H:%M")

        line = f"  {date_str} {time_str} — {title}"
        if len(line) > 60:
            line = line[:57] + "..."
        lines.append(line)

        loc = e.get("location", "")
        if loc:
            loc_line = f"     \u0001f4cd {loc}"
            if len(loc_line) > 60:
                loc_line = loc_line[:57] + "..."
            lines.append(loc_line)

    if len(events) > 12:
        lines.append(f"\n  ... и ещё {len(events) - 12}")

    return "\n".join(lines)
