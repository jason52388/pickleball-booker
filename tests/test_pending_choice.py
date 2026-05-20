"""Tests for save/load of pending_choice with time groups."""
from datetime import datetime

from tests.conftest import make_slot

import app


def test_save_load_roundtrip(tmp_state):
    sat = datetime(2026, 5, 30)
    sun = datetime(2026, 5, 31)
    slots = [
        make_slot("Saturday", sat.replace(hour=10), "Pickleball1A"),
        make_slot("Saturday", sat.replace(hour=10), "Pickleball1B"),
        make_slot("Sunday", sun.replace(hour=11), "Pickleball2A"),
    ]
    groups = app.group_slots_by_time(slots)
    app.save_pending_choice("RUN123", sat, sun, groups)

    loaded = app.load_pending_choice()
    assert loaded["run_id"] == "RUN123"
    assert loaded["saturday"] == sat.isoformat()
    assert loaded["sunday"] == sun.isoformat()
    assert "time_groups" in loaded
    assert len(loaded["time_groups"]) == 2  # Saturday 10am group + Sunday 11am group

    # Saturday group should hold both 1A and 1B courts
    saturday_group = loaded["time_groups"][0]
    assert len(saturday_group) == 2
    court_names = sorted(s["resource_name"] for s in saturday_group)
    assert court_names == ["Pickleball1A", "Pickleball1B"]


def test_save_then_clear(tmp_state):
    sat = datetime(2026, 5, 30)
    sun = datetime(2026, 5, 31)
    groups = app.group_slots_by_time(
        [make_slot("Saturday", sat.replace(hour=10), "Pickleball1A")]
    )
    app.save_pending_choice("R", sat, sun, groups)
    assert app.load_pending_choice() is not None
    app.clear_pending_choice()
    assert app.load_pending_choice() is None


def test_load_when_missing(tmp_state):
    assert app.load_pending_choice() is None


def test_clear_missing_safe(tmp_state):
    app.clear_pending_choice()


def test_save_empty_groups(tmp_state):
    sat = datetime(2026, 5, 30)
    sun = datetime(2026, 5, 31)
    app.save_pending_choice("R", sat, sun, [])
    loaded = app.load_pending_choice()
    assert loaded["time_groups"] == []


def test_slot_from_dict_roundtrip(tmp_state):
    """Each court inside a group can be reconstructed back into a Slot."""
    sat = datetime(2026, 5, 30, 10, 0)
    slot = make_slot("Saturday", sat, "Pickleball1A")
    app.save_pending_choice("R", sat, sat, [[slot]])
    loaded = app.load_pending_choice()
    restored = app.slot_from_dict(loaded["time_groups"][0][0])
    assert restored.resource_name == "Pickleball1A"
    assert restored.day_label == "Saturday"
    assert restored.start == sat
