"""Auto-book slot selection prioritizes start hours 10am -> 9am -> 11am -> 8am.

`choose_auto_book_slot` picks one slot from the scraped weekend. Within the
preferred window it must prefer 10am, then 9am, then 11am, then 8am, ahead of
day-of-week and ahead of any in-window hour that isn't on the priority list.
"""
from datetime import datetime

import app
from tests.conftest import make_slot


SAT = datetime(2026, 6, 6)
SUN = datetime(2026, 6, 7)


def _at(day: datetime, hour: int, court: str = "Court1") -> app.Slot:
    return make_slot(
        "Saturday" if day == SAT else "Sunday",
        day.replace(hour=hour),
        court,
    )


def test_prefers_10am_over_earlier_hours():
    sat = [_at(SAT, 8), _at(SAT, 9), _at(SAT, 10)]
    picked = app.choose_auto_book_slot(sat, [])
    assert picked.start.hour == 10


def test_falls_back_to_9am_when_no_10am():
    sat = [_at(SAT, 8), _at(SAT, 9)]
    picked = app.choose_auto_book_slot(sat, [])
    assert picked.start.hour == 9


def test_falls_back_to_8am_when_only_8am():
    sat = [_at(SAT, 8)]
    picked = app.choose_auto_book_slot(sat, [])
    assert picked.start.hour == 8


def test_hour_priority_beats_day_preference():
    # 10am is Sunday, 8am is Saturday; the 10am time wins despite being Sunday.
    sat = [_at(SAT, 8)]
    sun = [_at(SUN, 10)]
    picked = app.choose_auto_book_slot(sat, sun)
    assert picked.start.hour == 10
    assert picked.day_label == "Sunday"


def test_saturday_wins_tiebreak_at_same_hour():
    sat = [_at(SAT, 10)]
    sun = [_at(SUN, 10)]
    picked = app.choose_auto_book_slot(sat, sun)
    assert picked.day_label == "Saturday"


def test_11am_beats_9am_is_false():
    # 9am outranks 11am in the priority list (10 -> 9 -> 11 -> 8).
    sat = [_at(SAT, 11), _at(SAT, 9)]
    picked = app.choose_auto_book_slot(sat, [])
    assert picked.start.hour == 9


def test_11am_beats_8am():
    # 11am now ranks ahead of 8am (10 -> 9 -> 11 -> 8).
    sat = [_at(SAT, 11), _at(SAT, 8)]
    picked = app.choose_auto_book_slot(sat, [])
    assert picked.start.hour == 11


def test_8am_booked_when_only_option():
    sat = [_at(SAT, 8)]
    picked = app.choose_auto_book_slot(sat, [])
    assert picked.start.hour == 8


def test_unlisted_in_window_hour_is_last_resort(monkeypatch):
    # An hour inside the window but absent from the priority list ranks after
    # every listed hour. Drop 11am from the list so 11am is the unlisted hour.
    monkeypatch.setenv("PREFERRED_HOUR_PRIORITY", "10,9,8")
    sat = [_at(SAT, 11), _at(SAT, 8)]
    picked = app.choose_auto_book_slot(sat, [])
    assert picked.start.hour == 8


def test_no_preferred_slots_returns_none():
    # 6am and 7pm are outside the preferred window entirely.
    sat = [_at(SAT, 6), _at(SAT, 19)]
    assert app.choose_auto_book_slot(sat, []) is None


def test_priority_is_env_overridable(monkeypatch):
    monkeypatch.setenv("PREFERRED_HOUR_PRIORITY", "8,9,10")
    sat = [_at(SAT, 8), _at(SAT, 9), _at(SAT, 10)]
    picked = app.choose_auto_book_slot(sat, [])
    assert picked.start.hour == 8
