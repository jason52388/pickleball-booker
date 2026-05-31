"""Month-picker navigation must work when the target date is tz-aware.

`open_target_day` parses the calendar header into a naive datetime and compares
it to the target weekend, which flows from datetime.now(America/Chicago) and is
tz-aware. Comparing the two directly raised "can't compare offset-naive and
offset-aware datetimes" whenever a booking crossed a month boundary (e.g. a
May run targeting a June weekend). These pin the tz-safe comparison.
"""
from datetime import datetime
from zoneinfo import ZoneInfo

import app

CHICAGO = ZoneInfo("America/Chicago")


def test_aware_target_does_not_raise_and_steps_forward():
    # Regression: this exact shape (naive header vs aware June target) crashed.
    target = datetime(2026, 6, 6, tzinfo=CHICAGO)
    assert app.calendar_nav_arrow("May 2026", target) == ".icon-chevron-right"


def test_aware_target_steps_back_when_header_is_later():
    target = datetime(2026, 6, 6, tzinfo=CHICAGO)
    assert app.calendar_nav_arrow("July 2026", target) == ".icon-chevron-left"


def test_naive_target_still_works():
    target = datetime(2026, 6, 6)
    assert app.calendar_nav_arrow("May 2026", target) == ".icon-chevron-right"
    assert app.calendar_nav_arrow("July 2026", target) == ".icon-chevron-left"


def test_crosses_year_boundary_forward():
    target = datetime(2027, 1, 3, tzinfo=CHICAGO)
    assert app.calendar_nav_arrow("December 2026", target) == ".icon-chevron-right"
