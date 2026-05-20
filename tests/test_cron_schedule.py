"""Tests that pin the cron schedule to exactly what the user requested:

- Sun–Thu: ATTEMPT booking for `upcoming_weekend`
- Fri/Sat:  ASK YES/NO for `next_booking_weekend` (8 / 7 days away)
- Decline (via NO or "Don't book this weekend") silences every subsequent day
  for that weekend.

The cron logic lives inside `main()`, so we exercise the two pure functions
`upcoming_weekend` and `next_booking_weekend` against a reference cycle, and
then drive the decision/lock helpers to confirm what would short-circuit the
flow on each day.
"""
from datetime import datetime

import app


WEEKDAY_NAMES = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]


def _day(year, month, day):
    return datetime(year, month, day)


def test_friday_asks_about_weekend_8_days_away():
    """Fri May 22 — next_booking_weekend should target Sat May 30 / Sun May 31."""
    fri = _day(2026, 5, 22)
    assert fri.weekday() == 4
    sat, sun = app.next_booking_weekend(fri)
    assert (sat.date().isoformat(), sun.date().isoformat()) == (
        "2026-05-30", "2026-05-31"
    )


def test_saturday_asks_about_same_weekend_as_friday():
    """Sat May 23 — next_booking_weekend should target the same May 30/31."""
    sat_day = _day(2026, 5, 23)
    assert sat_day.weekday() == 5
    sat, sun = app.next_booking_weekend(sat_day)
    assert (sat.date().isoformat(), sun.date().isoformat()) == (
        "2026-05-30", "2026-05-31"
    )


def test_saturday_does_not_ask_about_current_weekend():
    """Critical: Sat May 23 must NOT ask about May 23/24 (today/tomorrow);
    it must ask about May 30/31 (a week out)."""
    sat_day = _day(2026, 5, 23)
    target_sat, _ = app.next_booking_weekend(sat_day)
    assert target_sat.date().isoformat() != "2026-05-23"
    assert target_sat.date().isoformat() == "2026-05-30"


def test_sunday_through_thursday_target_upcoming_weekend():
    """Sun–Thu attempts should all target the same Sat May 30 / Sun May 31."""
    for d in [
        _day(2026, 5, 24),  # Sun
        _day(2026, 5, 25),  # Mon
        _day(2026, 5, 26),  # Tue
        _day(2026, 5, 27),  # Wed
        _day(2026, 5, 28),  # Thu
    ]:
        sat, sun = app.upcoming_weekend(d)
        assert (sat.date().isoformat(), sun.date().isoformat()) == (
            "2026-05-30", "2026-05-31"
        ), f"on {WEEKDAY_NAMES[d.weekday()]} {d.date()} expected May 30/31, got {sat.date()}/{sun.date()}"


def test_full_weekly_cycle_target_alignment():
    """The weekend the Fri/Sat YES/NO asks about is the same weekend the
    following Sun–Thu booking attempts target. Locks the user→bot contract."""
    fri = _day(2026, 5, 22)
    sat_ask = _day(2026, 5, 23)
    asked_sat, asked_sun = app.next_booking_weekend(fri)
    # Saturday asks about the same weekend
    assert app.next_booking_weekend(sat_ask) == (asked_sat, asked_sun)
    # Sun–Thu of the following week book for that exact weekend
    for booking_day in [
        _day(2026, 5, 24), _day(2026, 5, 25), _day(2026, 5, 26),
        _day(2026, 5, 27), _day(2026, 5, 28),
    ]:
        assert app.upcoming_weekend(booking_day) == (asked_sat, asked_sun)


def test_decline_silences_all_subsequent_days(tmp_state):
    """Declining via Fri/Sat NO (or 'Don't book this weekend') marks the
    weekend declined; every Sun–Thu booking attempt then short-circuits."""
    target_sat, target_sun = _day(2026, 5, 30), _day(2026, 5, 31)
    app.set_weekend_decision(target_sat, target_sun, "declined")
    # Each Sun–Thu attempt would see this decision and exit.
    assert app.get_weekend_decision(target_sat, target_sun) == "declined"
    # Other weekends are unaffected (next week's run still asks normally).
    other_sat, other_sun = _day(2026, 6, 6), _day(2026, 6, 7)
    assert app.get_weekend_decision(other_sat, other_sun) is None


def test_confirmed_persists_through_the_week(tmp_state):
    """Once the user taps YES on Fri/Sat, the decision sticks so Sun–Thu
    runs proceed without re-asking."""
    target_sat, target_sun = _day(2026, 5, 30), _day(2026, 5, 31)
    app.set_weekend_decision(target_sat, target_sun, "confirmed")
    assert app.get_weekend_decision(target_sat, target_sun) == "confirmed"


def test_booking_lock_short_circuits_all_remaining_days(tmp_state, monkeypatch):
    """Once a slot is booked (lock set), no remaining Sun–Thu run does anything."""
    # The lock helper refuses to write in dry-run mode (so testing doesn't
    # poison real state); force live mode for this test.
    monkeypatch.setenv("DRY_RUN", "false")
    sat, sun = _day(2026, 5, 30), _day(2026, 5, 31)
    assert not app.has_weekend_booking_lock(sat, sun)
    fake_slot = app.Slot(
        day_label="Saturday",
        label="Pickleball1A 10:00 AM",
        start=sat.replace(hour=10),
        end=sat.replace(hour=11),
        locator_hint="Pickleball1A",
        resource_name="Pickleball1A",
        row_index=0,
        col_index=0,
    )
    app.set_weekend_booking_lock(sat, sun, fake_slot, "TEST")
    assert app.has_weekend_booking_lock(sat, sun)
