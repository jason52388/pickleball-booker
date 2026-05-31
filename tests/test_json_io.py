"""State-file I/O must survive a crash mid-write.

write_json_file writes to a temp file and atomically replaces the target, and
read_json_file returns the default on truncated/corrupt JSON instead of raising
(which previously crashed every subsequent run after a kill-switch interruption).
"""
import app


def test_round_trip(tmp_path):
    p = tmp_path / "state.json"
    app.write_json_file(p, {"a": 1, "b": [2, 3]})
    assert app.read_json_file(p, None) == {"a": 1, "b": [2, 3]}


def test_no_temp_file_left_behind(tmp_path):
    p = tmp_path / "state.json"
    app.write_json_file(p, {"a": 1})
    assert not (tmp_path / "state.json.tmp").exists()


def test_corrupt_file_returns_default(tmp_path):
    p = tmp_path / "state.json"
    p.write_text("{ this is not valid json", encoding="utf-8")
    assert app.read_json_file(p, {"fallback": True}) == {"fallback": True}


def test_missing_file_returns_default(tmp_path):
    p = tmp_path / "does_not_exist.json"
    assert app.read_json_file(p, []) == []


def test_overwrite_is_atomic_replace(tmp_path):
    p = tmp_path / "state.json"
    app.write_json_file(p, {"v": 1})
    app.write_json_file(p, {"v": 2})
    assert app.read_json_file(p, None) == {"v": 2}
