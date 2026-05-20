"""Tests for the weekend-confirmation gating + decision persistence."""
from datetime import datetime

import app


def test_next_booking_weekend_on_friday():
    """On Friday May 22 2026, the next Sunday cron run (May 24) targets May 30/31."""
    friday = datetime(2026, 5, 22)
    assert friday.weekday() == 4
    sat, sun = app.next_booking_weekend(friday)
    assert sat.date().isoformat() == "2026-05-30"
    assert sun.date().isoformat() == "2026-05-31"


def test_next_booking_weekend_on_saturday():
    saturday = datetime(2026, 5, 23)
    assert saturday.weekday() == 5
    sat, sun = app.next_booking_weekend(saturday)
    assert sat.date().isoformat() == "2026-05-30"
    assert sun.date().isoformat() == "2026-05-31"


def test_next_booking_weekend_on_sunday():
    """On Sunday May 24, the cron run today targets May 30/31."""
    sunday = datetime(2026, 5, 24)
    assert sunday.weekday() == 6
    sat, sun = app.next_booking_weekend(sunday)
    assert sat.date().isoformat() == "2026-05-30"
    assert sun.date().isoformat() == "2026-05-31"


def test_next_booking_weekend_on_monday():
    """On Monday, the upcoming Sunday is 6 days away → weekend 12 days out."""
    monday = datetime(2026, 5, 25)
    assert monday.weekday() == 0
    sat, sun = app.next_booking_weekend(monday)
    assert sat.date().isoformat() == "2026-06-06"
    assert sun.date().isoformat() == "2026-06-07"


def test_set_and_get_decision_confirmed(tmp_state):
    sat = datetime(2026, 5, 30)
    sun = datetime(2026, 5, 31)
    assert app.get_weekend_decision(sat, sun) is None
    app.set_weekend_decision(sat, sun, "confirmed")
    assert app.get_weekend_decision(sat, sun) == "confirmed"


def test_set_and_get_decision_declined(tmp_state):
    sat = datetime(2026, 6, 6)
    sun = datetime(2026, 6, 7)
    app.set_weekend_decision(sat, sun, "declined")
    assert app.get_weekend_decision(sat, sun) == "declined"


def test_decisions_isolated_per_weekend(tmp_state):
    sat1, sun1 = datetime(2026, 5, 30), datetime(2026, 5, 31)
    sat2, sun2 = datetime(2026, 6, 6), datetime(2026, 6, 7)
    app.set_weekend_decision(sat1, sun1, "confirmed")
    app.set_weekend_decision(sat2, sun2, "declined")
    assert app.get_weekend_decision(sat1, sun1) == "confirmed"
    assert app.get_weekend_decision(sat2, sun2) == "declined"


def test_pending_weekend_roundtrip(tmp_state):
    sat = datetime(2026, 5, 30)
    sun = datetime(2026, 5, 31)
    assert app.load_pending_weekend() is None
    app.save_pending_weekend(sat, sun)
    loaded = app.load_pending_weekend()
    assert loaded is not None
    assert loaded["saturday"] == sat.isoformat()
    assert loaded["sunday"] == sun.isoformat()
    app.clear_pending_weekend()
    assert app.load_pending_weekend() is None


def test_clear_pending_weekend_safe_when_missing(tmp_state):
    """Should not raise if the file isn't there."""
    app.clear_pending_weekend()  # noqa: no exception expected
