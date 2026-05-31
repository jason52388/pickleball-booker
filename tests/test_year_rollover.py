"""Year rollover must not break date handling the way the month switch did.

The month-boundary bug was a naive/aware comparison TypeError; its fix compares
(year, month) tuples parsed from a full "Mon YYYY" header, so the year dimension
is covered. These pin that behavior for a weekend that straddles New Year's
(Sat Dec 31 2033 / Sun Jan 1 2034) so a future regression surfaces here.
"""
from datetime import datetime
from zoneinfo import ZoneInfo

import app
import bot_listener

CT = ZoneInfo("America/Chicago")


def test_upcoming_weekend_straddles_year_boundary():
    ref = datetime(2033, 12, 28, tzinfo=CT)  # Wed before Sat Dec 31
    sat, sun = app.upcoming_weekend(ref)
    assert sat.date().isoformat() == "2033-12-31"
    assert sun.date().isoformat() == "2034-01-01"


def test_parse_slot_time_stamps_each_days_year():
    sat = datetime(2033, 12, 31)
    sun = datetime(2034, 1, 1)
    s_start, _ = app.parse_slot_time("10:00 AM - 11:00 AM", sat)
    u_start, _ = app.parse_slot_time("10:00 AM - 11:00 AM", sun)
    assert s_start.year == 2033
    assert u_start.year == 2034


def test_weekend_key_does_not_collide_across_years():
    sat = datetime(2033, 12, 31)
    sun = datetime(2034, 1, 1)
    key = app.weekend_key(sat, sun)
    assert "2033-12-31" in key and "2034-01-01" in key


def test_calendar_nav_steps_across_dec_to_jan():
    assert app.calendar_nav_arrow("Dec 2033", datetime(2034, 1, 1, tzinfo=CT)) == ".icon-chevron-right"
    assert app.calendar_nav_arrow("Jan 2034", datetime(2033, 12, 31, tzinfo=CT)) == ".icon-chevron-left"


def test_parse_calendar_header_across_year():
    assert app.parse_calendar_header("Dec 2033").year == 2033
    assert app.parse_calendar_header("Jan 2034").year == 2034


def test_weekend_for_sunday_jan_1_derives_prior_year_saturday():
    sat, sun = bot_listener._weekend_for(datetime(2034, 1, 1, 10, 0))
    assert sat.date().isoformat() == "2033-12-31"
    assert sun.date().isoformat() == "2034-01-01"


def test_next_booking_weekend_rolls_into_january():
    ref = datetime(2025, 12, 26, tzinfo=CT)  # Friday
    nsat, nsun = app.next_booking_weekend(ref)
    assert nsat.weekday() == 5
    assert nsun.weekday() == 6
    assert nsat.year == 2026
