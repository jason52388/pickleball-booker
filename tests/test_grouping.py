"""Tests for grouping slots by (day, start time)."""
from datetime import datetime

from tests.conftest import make_slot

import app


def test_groups_same_day_and_time():
    sat = datetime(2026, 5, 30, 10, 0)
    slots = [
        make_slot("Saturday", sat, "Pickleball1A"),
        make_slot("Saturday", sat, "Pickleball1B"),
        make_slot("Saturday", sat, "Pickleball2A"),
    ]
    groups = app.group_slots_by_time(slots)
    assert len(groups) == 1
    assert len(groups[0]) == 3


def test_different_times_split_groups():
    sat = datetime(2026, 5, 30, 10, 0)
    sat_11 = datetime(2026, 5, 30, 11, 0)
    slots = [
        make_slot("Saturday", sat, "Pickleball1A"),
        make_slot("Saturday", sat_11, "Pickleball1A"),
    ]
    groups = app.group_slots_by_time(slots)
    assert len(groups) == 2


def test_saturday_and_sunday_same_time_split():
    sat_10 = datetime(2026, 5, 30, 10, 0)
    sun_10 = datetime(2026, 5, 31, 10, 0)
    slots = [
        make_slot("Saturday", sat_10, "Pickleball1A"),
        make_slot("Sunday", sun_10, "Pickleball1A"),
    ]
    groups = app.group_slots_by_time(slots)
    assert len(groups) == 2


def test_groups_sorted_by_start():
    sat = datetime(2026, 5, 30)
    slots = [
        make_slot("Saturday", sat.replace(hour=15), "Pickleball1A"),
        make_slot("Saturday", sat.replace(hour=9), "Pickleball1A"),
        make_slot("Saturday", sat.replace(hour=12), "Pickleball1A"),
    ]
    groups = app.group_slots_by_time(slots)
    starts = [g[0].start.hour for g in groups]
    assert starts == [9, 12, 15]


def test_courts_within_group_sorted_by_name():
    sat = datetime(2026, 5, 30, 10, 0)
    slots = [
        make_slot("Saturday", sat, "Pickleball2B"),
        make_slot("Saturday", sat, "Pickleball1A"),
        make_slot("Saturday", sat, "Pickleball1B"),
    ]
    groups = app.group_slots_by_time(slots)
    names = [c.resource_name for c in groups[0]]
    assert names == sorted(names)


def test_realistic_63_slots_across_both_days():
    """Simulate ~63 slots (10 courts x ~6 times x 2 days) and confirm grouping."""
    slots = []
    for day_label, day_date in [("Saturday", datetime(2026, 5, 30)),
                                 ("Sunday", datetime(2026, 5, 31))]:
        for hour in [9, 10, 11, 12, 13, 14]:
            for court in ["Pickleball1A", "Pickleball1B", "Pickleball2A",
                          "Pickleball2B", "Pickleball3A"]:
                slots.append(make_slot(day_label, day_date.replace(hour=hour), court))
    groups = app.group_slots_by_time(slots)
    # 6 hours x 2 days = 12 time-groups
    assert len(groups) == 12
    # Each group should have 5 courts
    for g in groups:
        assert len(g) == 5


def test_time_group_label_format():
    sat = datetime(2026, 5, 30, 10, 30)
    slots = [make_slot("Saturday", sat, "Pickleball1A")]
    label = app._time_group_label(slots)
    assert label == "Sat 10:30 AM"


def test_time_group_label_pm():
    sun = datetime(2026, 5, 31, 14, 0)
    slots = [make_slot("Sunday", sun, "Pickleball1A")]
    label = app._time_group_label(slots)
    assert label == "Sun 2:00 PM"
