"""Tests for tools/secretary/calendar.py."""
from __future__ import annotations
import os
from unittest.mock import patch
from datetime import datetime, timezone

SAMPLE_ICS = """BEGIN:VCALENDAR
VERSION:2.0
BEGIN:VEVENT
DTSTART:20260810T090000Z
DTEND:20260810T100000Z
SUMMARY:Test Meeting
LOCATION:Room 1
END:VEVENT
BEGIN:VEVENT
DTSTART:20260811T140000Z
DTEND:20260811T150000Z
SUMMARY:All-hands
END:VEVENT
END:VCALENDAR"""


class TestCalendarParser:
    def test_parse_basic(self):
        from tools.secretary.calendar import _parse_ics
        import tools.secretary.calendar as cal
        # Freeze "now" to match the SAMPLE_ICS dates so the events fall inside
        # the [now, now+days_ahead] window regardless of when the suite runs.
        fixed_now = datetime(2026, 8, 10, 0, 0, tzinfo=timezone.utc)
        with patch.object(cal, "_now_utc", return_value=fixed_now):
            events = _parse_ics(SAMPLE_ICS, days_ahead=7)
        assert len(events) >= 1
        titles = {e["title"] for e in events}
        assert "Test Meeting" in titles or "All-hands" in titles

    def test_format_empty(self):
        from tools.secretary.calendar import format_calendar_list
        assert "Нет событий" in format_calendar_list([])

    def test_format_with_events(self):
        from tools.secretary.calendar import format_calendar_list
        events = [{
            "start": datetime(2026, 8, 10, 14, 0, tzinfo=timezone.utc),
            "end": datetime(2026, 8, 10, 15, 0, tzinfo=timezone.utc),
            "title": "Test", "location": "", "all_day": False,
        }]
        result = format_calendar_list(events)
        assert "Test" in result

    def test_is_configured_false(self):
        from tools.secretary.calendar import is_configured
        with patch.dict(os.environ, {}, clear=True):
            assert not is_configured()

    def test_is_configured_true(self):
        from tools.secretary.calendar import is_configured
        with patch.dict(os.environ, {"SECRETARY_CAL_ICS_URL": "https://x.com/cal.ics"}, clear=True):
            assert is_configured()

    def test_not_configured_raises(self):
        from tools.secretary.calendar import list_events
        with patch.dict(os.environ, {}, clear=True):
            try:
                list_events()
                assert False
            except RuntimeError as e:
                assert "not configured" in str(e).lower()
