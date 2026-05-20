"""Shared test fixtures."""
import sys
from datetime import datetime, timedelta
from pathlib import Path

import pytest

# Make the project root importable from tests/
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import app  # noqa: E402


@pytest.fixture
def tmp_state(tmp_path, monkeypatch):
    """Redirect every state-file path in app.py to a fresh tmp dir."""
    monkeypatch.setattr(app, "DATA_DIR", tmp_path)
    monkeypatch.setattr(app, "BOOKED_WEEKENDS_FILE", tmp_path / "booked_weekends.json")
    monkeypatch.setattr(app, "PENDING_CHOICE_FILE", tmp_path / "pending_choice.json")
    monkeypatch.setattr(app, "WEEKEND_DECISIONS_FILE", tmp_path / "weekend_decisions.json")
    monkeypatch.setattr(app, "PENDING_WEEKEND_FILE", tmp_path / "pending_weekend.json")
    return tmp_path


@pytest.fixture
def telegram_capture(monkeypatch):
    """Capture every payload sent to _telegram_api so tests can inspect it."""
    sent = []

    def _fake(method, payload):
        sent.append({"method": method, "payload": payload})
        return {"ok": True, "result": {}}

    monkeypatch.setattr(app, "_telegram_api", _fake)
    monkeypatch.setenv("TELEGRAM_CHAT_ID", "1234567890")
    return sent


def make_slot(day_label: str, start: datetime, resource_name: str,
              end: datetime = None) -> app.Slot:
    if end is None:
        end = start + timedelta(hours=1)
    return app.Slot(
        day_label=day_label,
        label=f"{resource_name} {start.strftime('%-I:%M %p')}",
        start=start,
        end=end,
        locator_hint=resource_name,
        resource_name=resource_name,
        row_index=0,
        col_index=0,
    )
