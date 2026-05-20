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
    # legacy mode = one button per row
    assert kb == [
        [{"text": "A", "callback_data": "A"}],
        [{"text": "B", "callback_data": "B"}],
    ]


def test_send_telegram_no_buttons(telegram_capture):
    app.send_telegram("just text")
    p = _last_payload(telegram_capture)
    assert "reply_markup" not in p


def test_send_slot_options_button_layout(telegram_capture):
    """8 time-groups → 4 rows of 2 buttons + 1 row of refresh/skip."""
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
    # 8 slots → 4 rows of 2, plus 1 control row = 5 rows
    assert len(kb) == 5
    for row in kb[:-1]:
        assert len(row) == 2  # two slots per row
    # Last row is refresh + skip
    last = kb[-1]
    assert last[0]["callback_data"] == "REFRESH"
    assert last[1]["callback_data"] == "SKIP"


def test_send_slot_options_odd_count(telegram_capture):
    """7 groups → 3 rows of 2 + 1 row of 1 + control row."""
    sat = datetime(2026, 5, 30)
    groups = [
        [make_slot("Saturday", sat.replace(hour=h), "Pickleball1A")]
        for h in range(8, 15)
    ]
    app.send_slot_options(groups, "Pick one:")
    kb = _last_payload(telegram_capture)["reply_markup"]["inline_keyboard"]
    # 3 full rows of 2 + 1 row of 1 + 1 control row = 5 rows
    assert len(kb) == 5
    assert len(kb[3]) == 1  # the orphan row


def test_send_slot_options_callbacks_are_indexed(telegram_capture):
    """Each slot button's callback_data is SLOT_<index>."""
    sat = datetime(2026, 5, 30)
    groups = [
        [make_slot("Saturday", sat.replace(hour=h), "Pickleball1A")]
        for h in [9, 10, 11]
    ]
    app.send_slot_options(groups, "Pick:")
    kb = _last_payload(telegram_capture)["reply_markup"]["inline_keyboard"]
    callbacks = [b["callback_data"] for row in kb[:-1] for b in row]
    assert callbacks == ["SLOT_0", "SLOT_1", "SLOT_2"]


def test_send_slot_options_button_labels_include_day_and_time(telegram_capture):
    sat = datetime(2026, 5, 30, 10, 0)
    sun = datetime(2026, 5, 31, 14, 30)
    groups = [
        [make_slot("Saturday", sat, "Pickleball1A")],
        [make_slot("Sunday", sun, "Pickleball1A")],
    ]
    app.send_slot_options(groups, "Pick:")
    kb = _last_payload(telegram_capture)["reply_markup"]["inline_keyboard"]
    labels = [b["text"] for row in kb[:-1] for b in row]
    assert labels == ["Sat 10:00 AM", "Sun 2:30 PM"]
