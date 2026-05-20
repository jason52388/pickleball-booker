"""Tests for slot filtering (6 AM / 7 PM exclusions)."""
from datetime import datetime

from tests.conftest import make_slot

import app


def test_filter_excludes_before_7am():
    sat = datetime(2026, 5, 30)
    slots = [
        make_slot("Saturday", sat.replace(hour=5), "Pickleball1A"),
        make_slot("Saturday", sat.replace(hour=6), "Pickleball1A"),
        make_slot("Saturday", sat.replace(hour=7), "Pickleball1A"),
    ]
    result = app.filter_display_slots(slots)
    starts = sorted(s.start.hour for s in result)
    assert starts == [7], f"expected only 7AM, got {starts}"


def test_filter_excludes_7pm_and_later():
    sat = datetime(2026, 5, 30)
    slots = [
        make_slot("Saturday", sat.replace(hour=18), "Pickleball1A"),
        make_slot("Saturday", sat.replace(hour=19), "Pickleball1A"),
        make_slot("Saturday", sat.replace(hour=20), "Pickleball1A"),
    ]
    result = app.filter_display_slots(slots)
    starts = sorted(s.start.hour for s in result)
    assert starts == [18]


def test_filter_keeps_full_window():
    sat = datetime(2026, 5, 30)
    slots = [
        make_slot("Saturday", sat.replace(hour=h), f"Court{h}")
        for h in [7, 9, 12, 15, 18]
    ]
    result = app.filter_display_slots(slots)
    assert len(result) == 5


def test_filter_falls_back_when_empty():
    """If filtering removes everything, return original — never silently drop all."""
    sat = datetime(2026, 5, 30)
    slots = [
        make_slot("Saturday", sat.replace(hour=5), "Pickleball1A"),
        make_slot("Saturday", sat.replace(hour=22), "Pickleball1A"),
    ]
    result = app.filter_display_slots(slots)
    assert len(result) == 2  # falls back to original


def test_filter_empty_input():
    assert app.filter_display_slots([]) == []
