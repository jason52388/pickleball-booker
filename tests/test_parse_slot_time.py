"""parse_slot_time must extract BOTH the start and end time.

Regression: the original regex used a lazy `.*?` before an optional end group,
so the end time was always dropped (group 2 matched empty). That made every
slot look like it had no end, which silently defaulted calendar invites to a
flat 1-hour duration regardless of the real slot length.
"""
from datetime import datetime

import app

TARGET = datetime(2026, 6, 6)


def test_extracts_start_and_end():
    start, end = app.parse_slot_time("10:00 AM - 11:00 AM", TARGET)
    assert (start.hour, start.minute) == (10, 0)
    assert end is not None
    assert (end.hour, end.minute) == (11, 0)


def test_extracts_with_resource_prefix():
    # aria-label form: "Pickleball1A 8:00 AM - 9:30 AM Available"
    start, end = app.parse_slot_time("Pickleball1A 8:00 AM - 9:30 AM Available", TARGET)
    assert (start.hour, start.minute) == (8, 0)
    assert (end.hour, end.minute) == (9, 30)


def test_start_only_has_no_end():
    start, end = app.parse_slot_time("9:00 AM", TARGET)
    assert (start.hour, start.minute) == (9, 0)
    assert end is None


def test_no_time_returns_none():
    assert app.parse_slot_time("no time here", TARGET) is None


def test_end_carries_target_date():
    start, end = app.parse_slot_time("10:00 AM - 11:00 AM", TARGET)
    assert end.date() == TARGET.date()
