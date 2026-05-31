"""Cross-process state writes must not lose updates.

The cron job (daily_runner) and the long-running bot_listener both
read-modify-write booked_weekends.json / weekend_decisions.json. Without
serialization, two processes can each read the old dict and the second write
clobbers the first key. set_weekend_decision / set_weekend_booking_lock now go
through update_json_file, which holds an exclusive fcntl lock across the
read-modify-write.

These tests fork real processes (not threads) so the GIL can't hide a missing
lock, each writing a distinct key for a distinct weekend. With locking, every
key must survive.
"""
import os
from datetime import datetime, timedelta

import pytest

import app

pytestmark = pytest.mark.skipif(
    app.fcntl is None, reason="POSIX fcntl required for cross-process locking"
)


def _weekend(n: int):
    """A distinct (saturday, sunday) pair per worker index."""
    base = datetime(2026, 1, 3)  # a Saturday
    sat = base + timedelta(days=7 * n)
    return sat, sat + timedelta(days=1)


def _run_workers(target, n_workers, state_file_attr, state_path):
    # Point app's state-file constant at the tmp path in *each* child (the
    # monkeypatch from the fixture only applies in the parent process).
    pids = []
    for i in range(n_workers):
        pid = os.fork()
        if pid == 0:  # child
            setattr(app, state_file_attr, state_path)
            try:
                target(i)
            finally:
                os._exit(0)
        pids.append(pid)
    for pid in pids:
        _, status = os.waitpid(pid, 0)
        assert status == 0


def test_concurrent_decisions_no_lost_updates(tmp_path, monkeypatch):
    state = tmp_path / "weekend_decisions.json"
    monkeypatch.setattr(app, "WEEKEND_DECISIONS_FILE", state)
    n = 20

    def worker(i):
        sat, sun = _weekend(i)
        app.set_weekend_decision(sat, sun, "confirmed")

    _run_workers(worker, n, "WEEKEND_DECISIONS_FILE", state)

    final = app.read_json_file(state, {})
    assert len(final) == n, f"lost updates: only {len(final)}/{n} keys survived"
    for i in range(n):
        sat, sun = _weekend(i)
        assert final[app.weekend_key(sat, sun)] == "confirmed"


def test_concurrent_booking_locks_no_lost_updates(tmp_path, monkeypatch):
    state = tmp_path / "booked_weekends.json"
    monkeypatch.setattr(app, "BOOKED_WEEKENDS_FILE", state)
    monkeypatch.setenv("BOOKING_LOCK_ENABLED", "true")
    monkeypatch.delenv("DRY_RUN", raising=False)
    n = 20

    def worker(i):
        sat, sun = _weekend(i)
        slot = app.Slot(
            day_label="Saturday", label="x", start=sat, end=None,
            locator_hint="x", resource_name="Pickleball1A",
            row_index=0, col_index=0,
        )
        app.set_weekend_booking_lock(sat, sun, slot, f"RUN{i}")

    _run_workers(worker, n, "BOOKED_WEEKENDS_FILE", state)

    final = app.read_json_file(state, {})
    assert len(final) == n, f"lost updates: only {len(final)}/{n} keys survived"
    for i in range(n):
        sat, sun = _weekend(i)
        assert app.weekend_key(sat, sun) in final


def test_update_json_file_applies_mutation(tmp_path):
    state = tmp_path / "s.json"
    app.update_json_file(state, lambda d: {**d, "a": 1}, {})
    app.update_json_file(state, lambda d: {**d, "b": 2}, {})
    assert app.read_json_file(state, {}) == {"a": 1, "b": 2}
