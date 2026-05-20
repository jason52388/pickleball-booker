"""Tests for the multi-court booking-retry behavior in bot_listener.handle_booking."""
from datetime import datetime
from unittest.mock import MagicMock

from tests.conftest import make_slot

import app
import bot_listener


def _stub_booker(scrape_returns, book_returns):
    """Build a fake CPDBooker whose scrape_slots returns the given lists and
    whose book_slot returns successive values from `book_returns`."""
    booker = MagicMock()
    # scrape_returns is a list keyed by day: {"Saturday": [...], "Sunday": [...]}
    booker.scrape_slots.side_effect = lambda day_date, day_label: scrape_returns.get(day_label, [])
    book_calls = iter(book_returns)
    booker.book_slot.side_effect = lambda slot: next(book_calls)
    return booker


def _pending(saturday, sunday):
    return {
        "run_id": "TESTRUN",
        "saturday": saturday.isoformat(),
        "sunday": sunday.isoformat(),
        "time_groups": [],
    }


def test_books_first_court_when_available(tmp_state, telegram_capture, monkeypatch):
    monkeypatch.setenv("DRY_RUN", "true")  # skip calendar invites
    monkeypatch.setattr(bot_listener, "set_weekend_booking_lock", lambda *a, **kw: None)
    monkeypatch.setattr(bot_listener, "send_calendar_invite", lambda *a, **kw: None)

    sat = datetime(2026, 5, 30)
    sun = datetime(2026, 5, 31)
    court1 = make_slot("Saturday", sat.replace(hour=10), "Pickleball1A")
    court2 = make_slot("Saturday", sat.replace(hour=10), "Pickleball1B")

    booker = _stub_booker(
        scrape_returns={"Saturday": [court1, court2], "Sunday": []},
        book_returns=[True],  # first attempt succeeds
    )

    result = bot_listener.handle_booking(booker, _pending(sat, sun), [court1, court2])

    assert result is None, "successful booking should return None"
    assert booker.book_slot.call_count == 1


def test_falls_through_to_next_court_when_first_fails(tmp_state, telegram_capture, monkeypatch):
    monkeypatch.setenv("DRY_RUN", "true")
    monkeypatch.setattr(bot_listener, "set_weekend_booking_lock", lambda *a, **kw: None)
    monkeypatch.setattr(bot_listener, "send_calendar_invite", lambda *a, **kw: None)

    sat = datetime(2026, 5, 30)
    sun = datetime(2026, 5, 31)
    court1 = make_slot("Saturday", sat.replace(hour=10), "Pickleball1A")
    court2 = make_slot("Saturday", sat.replace(hour=10), "Pickleball1B")
    court3 = make_slot("Saturday", sat.replace(hour=10), "Pickleball2A")

    booker = _stub_booker(
        scrape_returns={"Saturday": [court1, court2, court3], "Sunday": []},
        book_returns=[False, False, True],  # first two fail, third works
    )

    result = bot_listener.handle_booking(
        booker, _pending(sat, sun), [court1, court2, court3]
    )

    assert result is None
    assert booker.book_slot.call_count == 3


def test_returns_fresh_groups_when_requested_courts_gone(tmp_state, telegram_capture, monkeypatch):
    """If none of the user's chosen courts are still available, return new options."""
    monkeypatch.setattr(bot_listener, "set_weekend_booking_lock", lambda *a, **kw: None)
    monkeypatch.setattr(bot_listener, "send_calendar_invite", lambda *a, **kw: None)

    sat = datetime(2026, 5, 30)
    sun = datetime(2026, 5, 31)
    requested = make_slot("Saturday", sat.replace(hour=10), "PickleballGone")
    # Site has different slots now
    other = make_slot("Saturday", sat.replace(hour=11), "Pickleball1A")

    booker = _stub_booker(
        scrape_returns={"Saturday": [other], "Sunday": []},
        book_returns=[],
    )

    result = bot_listener.handle_booking(booker, _pending(sat, sun), [requested])

    assert result is not None, "should return fresh groups, not None"
    assert booker.book_slot.call_count == 0, "must not attempt to book a missing court"
    assert len(result) == 1
    assert result[0][0].resource_name == "Pickleball1A"


def test_returns_fresh_groups_when_all_attempts_fail(tmp_state, telegram_capture, monkeypatch):
    monkeypatch.setattr(bot_listener, "set_weekend_booking_lock", lambda *a, **kw: None)
    monkeypatch.setattr(bot_listener, "send_calendar_invite", lambda *a, **kw: None)

    sat = datetime(2026, 5, 30)
    sun = datetime(2026, 5, 31)
    court1 = make_slot("Saturday", sat.replace(hour=10), "Pickleball1A")
    later = make_slot("Saturday", sat.replace(hour=14), "Pickleball2A")

    booker = _stub_booker(
        scrape_returns={"Saturday": [court1, later], "Sunday": []},
        book_returns=[False],  # the only available court refuses to book
    )

    result = bot_listener.handle_booking(booker, _pending(sat, sun), [court1])

    assert result is not None
    # Fresh groups should include `later` as a fallback option
    flat = [c for g in result for c in g]
    assert any(c.resource_name == "Pickleball2A" for c in flat)


def test_book_slot_exception_falls_through(tmp_state, telegram_capture, monkeypatch):
    """A crash on one court shouldn't poison the others — try the next one."""
    monkeypatch.setenv("DRY_RUN", "true")
    monkeypatch.setattr(bot_listener, "set_weekend_booking_lock", lambda *a, **kw: None)
    monkeypatch.setattr(bot_listener, "send_calendar_invite", lambda *a, **kw: None)

    sat = datetime(2026, 5, 30)
    sun = datetime(2026, 5, 31)
    court1 = make_slot("Saturday", sat.replace(hour=10), "Pickleball1A")
    court2 = make_slot("Saturday", sat.replace(hour=10), "Pickleball1B")

    booker = MagicMock()
    booker.scrape_slots.side_effect = lambda d, lbl: {"Saturday": [court1, court2], "Sunday": []}.get(lbl, [])
    booker.book_slot.side_effect = [RuntimeError("captcha"), True]

    result = bot_listener.handle_booking(booker, _pending(sat, sun), [court1, court2])

    assert result is None  # second attempt succeeded
    assert booker.book_slot.call_count == 2
