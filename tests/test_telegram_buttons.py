"""Tests that send_telegram emits correct inline-keyboard payloads."""
from datetime import datetime

from tests.conftest import make_slot

import app


def _last_payload(sent):
    assert sent, "no telegram calls captured"
    return sent[-1]["payload"]


def test_send_telegram_inline_keyboard_2d(telegram_capture):
    app.send_telegram(
        "pick one",
        inline_keyboard=[
            [("YES", "WEEK_YES"), ("NO", "WEEK_NO")],
        ],
    )
    p = _last_payload(telegram_capture)
    assert p["text"] == "pick one"
    keyboard = p["reply_markup"]["inline_keyboard"]
    assert keyboard == [
        [{"text": "YES", "callback_data": "WEEK_YES"},
         {"text": "NO",  "callback_data": "WEEK_NO"}],
    ]


def test_send_telegram_legacy_flat_buttons(telegram_capture):
    app.send_telegram("legacy", buttons=["A", "B"])
    p = _last_payload(telegram_capture)
    kb = p["reply_markup"]["inline_keyboard"]
    assert kb == [
        [{"text": "A", "callback_data": "A"}],
        [{"text": "B", "callback_data": "B"}],
    ]


def test_send_telegram_no_buttons(telegram_capture):
    app.send_telegram("just text")
    p = _last_payload(telegram_capture)
    assert "reply_markup" not in p


def test_send_slot_options_button_layout(telegram_capture):
    """8 time-groups → 4 rows of 2 buttons + 1 row Refresh/Dismiss + 1 Decline row."""
    sat = datetime(2026, 5, 30)
    sun = datetime(2026, 5, 31)
    groups = []
    for hour in [8, 10, 12, 14]:
        groups.append([make_slot("Saturday", sat.replace(hour=hour), "Pickleball1A")])
    for hour in [9, 11, 13, 15]:
        groups.append([make_slot("Sunday", sun.replace(hour=hour), "Pickleball1A")])

    app.send_slot_options(groups, "Pick one:")

    p = _last_payload(telegram_capture)
    kb = p["reply_markup"]["inline_keyboard"]
    assert len(kb) == 6  # 4 slot rows + 2 control rows
    for row in kb[:-2]:
        assert len(row) == 2
    refresh_row = kb[-2]
    assert refresh_row[0]["callback_data"].startswith("REFRESH_")
    assert refresh_row[1]["callback_data"] == "SKIP"


def test_send_slot_options_odd_count(telegram_capture):
    """7 groups → 3 rows of 2 + 1 row of 1 + 2 control rows."""
    sat = datetime(2026, 5, 30)
    groups = [
        [make_slot("Saturday", sat.replace(hour=h), "Pickleball1A")]
        for h in range(8, 15)
    ]
    app.send_slot_options(groups, "Pick one:")
    kb = _last_payload(telegram_capture)["reply_markup"]["inline_keyboard"]
    assert len(kb) == 6  # 3 full slot rows + 1 orphan + Refresh row + Decline row
    assert len(kb[3]) == 1  # the orphan slot


def test_send_slot_options_callbacks_encode_iso_timestamp(telegram_capture):
    """Each slot button's callback_data is BOOK_<iso start datetime>, so the
    listener can act on a tap with no persisted state."""
    sat = datetime(2026, 5, 30)
    groups = [
        [make_slot("Saturday", sat.replace(hour=h), "Pickleball1A")]
        for h in [9, 10, 11]
    ]
    app.send_slot_options(groups, "Pick:")
    kb = _last_payload(telegram_capture)["reply_markup"]["inline_keyboard"]
    # Slot rows are everything except the last two control rows
    callbacks = [b["callback_data"] for row in kb[:-2] for b in row]
    assert callbacks == [
        "BOOK_2026-05-30T09:00:00",
        "BOOK_2026-05-30T10:00:00",
        "BOOK_2026-05-30T11:00:00",
    ]


def test_send_slot_options_refresh_carries_saturday_date(telegram_capture):
    sat = datetime(2026, 5, 30)
    sun = datetime(2026, 5, 31)
    groups = [
        [make_slot("Saturday", sat.replace(hour=10), "Pickleball1A")],
        [make_slot("Sunday", sun.replace(hour=10), "Pickleball1A")],
    ]
    app.send_slot_options(groups, "Pick:")
    kb = _last_payload(telegram_capture)["reply_markup"]["inline_keyboard"]
    refresh_cb = kb[-2][0]["callback_data"]
    assert refresh_cb == "REFRESH_2026-05-30"


def test_send_slot_options_button_labels_include_day_and_time(telegram_capture):
    sat = datetime(2026, 5, 30, 10, 0)
    sun = datetime(2026, 5, 31, 14, 30)
    groups = [
        [make_slot("Saturday", sat, "Pickleball1A")],
        [make_slot("Sunday", sun, "Pickleball1A")],
    ]
    app.send_slot_options(groups, "Pick:")
    kb = _last_payload(telegram_capture)["reply_markup"]["inline_keyboard"]
    labels = [b["text"] for row in kb[:-2] for b in row]
    assert labels == ["Sat 10:00 AM", "Sun 2:30 PM"]


def test_send_slot_options_skip_callback_is_global(telegram_capture):
    """SKIP doesn't need any weekend info — same callback regardless of run."""
    sat = datetime(2026, 5, 30, 10, 0)
    groups = [[make_slot("Saturday", sat, "Pickleball1A")]]
    app.send_slot_options(groups, "Pick:")
    kb = _last_payload(telegram_capture)["reply_markup"]["inline_keyboard"]
    # SKIP lives in the first control row (Refresh + Dismiss)
    assert kb[-2][1]["callback_data"] == "SKIP"


def test_send_slot_options_includes_decline_button(telegram_capture):
    """The slot list now includes a 'Don't book this weekend' row whose callback
    encodes the Saturday date so the listener can mark the weekend declined."""
    sat = datetime(2026, 5, 30, 10, 0)
    groups = [[make_slot("Saturday", sat, "Pickleball1A")]]
    app.send_slot_options(groups, "Pick:")
    kb = _last_payload(telegram_capture)["reply_markup"]["inline_keyboard"]
    # Final row is the standalone Decline button
    last = kb[-1]
    assert len(last) == 1
    assert last[0]["callback_data"] == "DECLINE_2026-05-30"
    assert "Don't book" in last[0]["text"]
