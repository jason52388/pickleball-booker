"""Tests for the self-contained `book_at_time` flow in bot_listener.

The booker no longer relies on persisted state — every callback encodes the
target slot's timestamp directly. These tests verify the multi-court retry
and the weekend-derivation logic behind that.
"""
from datetime import datetime
from unittest.mock import MagicMock, patch

from tests.conftest import make_slot

import app
import bot_listener


def test_weekend_for_saturday_input():
    sat_dt = datetime(2026, 5, 30, 10, 0)
    sat, sun = bot_listener._weekend_for(sat_dt)
    assert sat.date().isoformat() == "2026-05-30"
    assert sun.date().isoformat() == "2026-05-31"


def test_weekend_for_sunday_input():
    sun_dt = datetime(2026, 5, 31, 14, 0)
    sat, sun = bot_listener._weekend_for(sun_dt)
    assert sat.date().isoformat() == "2026-05-30"
    assert sun.date().isoformat() == "2026-05-31"


def test_weekend_for_weekday_falls_back_to_upcoming():
    """A target_dt on a weekday (shouldn't happen in practice) snaps to the
    upcoming Saturday rather than crashing."""
    tue_dt = datetime(2026, 5, 19, 10, 0)
    sat, sun = bot_listener._weekend_for(tue_dt)
    assert sat.weekday() == 5
    assert sun.weekday() == 6


def _booker_with(slots_to_return, book_results):
    booker = MagicMock()
    booker.scrape_slots.side_effect = lambda d, lbl: slots_to_return.get(lbl, [])
    booker.book_slot.side_effect = list(book_results)
    return booker


def test_book_at_time_books_first_available_court(tmp_state, telegram_capture, monkeypatch):
    monkeypatch.setenv("DRY_RUN", "true")
    target = datetime(2026, 5, 30, 10, 0)
    courts = [
        make_slot("Saturday", target, "Pickleball1A"),
        make_slot("Saturday", target, "Pickleball1B"),
    ]
    booker = _booker_with(
        slots_to_return={"Saturday": courts, "Sunday": []},
        book_results=[True],
    )

    @bot_listener.contextmanager
    def fake_open_booker():
        yield booker

    monkeypatch.setattr(bot_listener, "open_booker", fake_open_booker)
    monkeypatch.setattr(bot_listener, "set_weekend_booking_lock", lambda *a, **kw: None)
    monkeypatch.setattr(bot_listener, "send_calendar_invite", lambda *a, **kw: None)

    bot_listener.book_at_time(target)

    assert booker.book_slot.call_count == 1
    msgs = [c["payload"]["text"] for c in telegram_capture]
    assert any("Would book" in m for m in msgs)


def test_book_at_time_falls_through_courts(tmp_state, telegram_capture, monkeypatch):
    """If the first court fails, the next is tried — the booking succeeds
    overall, no refreshed-options message is sent."""
    monkeypatch.setenv("DRY_RUN", "true")
    target = datetime(2026, 5, 30, 10, 0)
    courts = [
        make_slot("Saturday", target, "Pickleball1A"),
        make_slot("Saturday", target, "Pickleball1B"),
        make_slot("Saturday", target, "Pickleball2A"),
    ]
    booker = _booker_with(
        slots_to_return={"Saturday": courts, "Sunday": []},
        book_results=[False, False, True],
    )

    @bot_listener.contextmanager
    def fake_open_booker():
        yield booker

    monkeypatch.setattr(bot_listener, "open_booker", fake_open_booker)
    monkeypatch.setattr(bot_listener, "set_weekend_booking_lock", lambda *a, **kw: None)
    monkeypatch.setattr(bot_listener, "send_calendar_invite", lambda *a, **kw: None)

    bot_listener.book_at_time(target)

    assert booker.book_slot.call_count == 3
    msgs = [c["payload"]["text"] for c in telegram_capture]
    assert any("Would book" in m for m in msgs)


def test_book_at_time_no_courts_sends_refreshed_options(tmp_state, telegram_capture, monkeypatch):
    """User taps an old button whose time is no longer in the grid — we send
    the current available times instead of saying 'session expired'."""
    target = datetime(2026, 5, 30, 10, 0)
    # Site returns no slot at 10am, but does have an 11am slot
    later = make_slot("Saturday", datetime(2026, 5, 30, 11, 0), "Pickleball1A")
    booker = _booker_with(
        slots_to_return={"Saturday": [later], "Sunday": []},
        book_results=[],
    )

    @bot_listener.contextmanager
    def fake_open_booker():
        yield booker

    monkeypatch.setattr(bot_listener, "open_booker", fake_open_booker)

    bot_listener.book_at_time(target)

    assert booker.book_slot.call_count == 0
    # Last message should carry an inline keyboard with the 11am option
    last = telegram_capture[-1]["payload"]
    assert "reply_markup" in last
    callbacks = [
        b["callback_data"]
        for row in last["reply_markup"]["inline_keyboard"]
        for b in row
    ]
    assert any(cb == f"BOOK_{later.start.isoformat()}" for cb in callbacks)


def test_book_at_time_all_fail_sends_refreshed_options(tmp_state, telegram_capture, monkeypatch):
    monkeypatch.setenv("DRY_RUN", "true")
    target = datetime(2026, 5, 30, 10, 0)
    courts = [make_slot("Saturday", target, "Pickleball1A")]
    later = make_slot("Saturday", datetime(2026, 5, 30, 14, 0), "Pickleball2A")
    # First scrape returns courts + later; book_slot returns False; second scrape
    # for refresh returns later only.
    scrape_calls = {"count": 0}

    def fake_scrape(d, lbl):
        scrape_calls["count"] += 1
        if lbl != "Saturday":
            return []
        # First two calls (saturday + sunday for initial), then refresh
        if scrape_calls["count"] <= 2:
            return [courts[0], later]
        return [later]

    booker = MagicMock()
    booker.scrape_slots.side_effect = fake_scrape
    booker.book_slot.side_effect = [False]

    @bot_listener.contextmanager
    def fake_open_booker():
        yield booker

    monkeypatch.setattr(bot_listener, "open_booker", fake_open_booker)
    monkeypatch.setattr(bot_listener, "set_weekend_booking_lock", lambda *a, **kw: None)
    monkeypatch.setattr(bot_listener, "send_calendar_invite", lambda *a, **kw: None)

    bot_listener.book_at_time(target)

    assert booker.book_slot.call_count == 1
    last = telegram_capture[-1]["payload"]
    assert "reply_markup" in last  # refreshed options sent


def test_book_at_time_exception_falls_through_to_next_court(tmp_state, telegram_capture, monkeypatch):
    """A crash on one court shouldn't poison the rest — keep going."""
    monkeypatch.setenv("DRY_RUN", "true")
    target = datetime(2026, 5, 30, 10, 0)
    courts = [
        make_slot("Saturday", target, "Pickleball1A"),
        make_slot("Saturday", target, "Pickleball1B"),
    ]
    booker = MagicMock()
    booker.scrape_slots.side_effect = lambda d, lbl: courts if lbl == "Saturday" else []
    booker.book_slot.side_effect = [RuntimeError("captcha"), True]

    @bot_listener.contextmanager
    def fake_open_booker():
        yield booker

    monkeypatch.setattr(bot_listener, "open_booker", fake_open_booker)
    monkeypatch.setattr(bot_listener, "set_weekend_booking_lock", lambda *a, **kw: None)
    monkeypatch.setattr(bot_listener, "send_calendar_invite", lambda *a, **kw: None)

    bot_listener.book_at_time(target)

    assert booker.book_slot.call_count == 2  # both attempted
    msgs = [c["payload"]["text"] for c in telegram_capture]
    assert any("Would book" in m for m in msgs)
