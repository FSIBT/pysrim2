import json
import os
import warnings

import pytest

from srim.mcp.activity import ActivityEntry, ActivityLog, activity_path


def test_entries_round_trip(tmp_path):
    log = ActivityLog(tmp_path / "a.jsonl")
    log.append(ActivityEntry(1.0, "headless", "codex 1.2", "set_values", "set ion.energy_keV=40"))
    assert log.last(5)[0].summary == "set ion.energy_keV=40"


def test_the_log_rotates(tmp_path):
    log = ActivityLog(tmp_path / "a.jsonl", max_bytes=200)
    for i in range(20):
        log.append(ActivityEntry(float(i), "headless", "c", "t", "x" * 20))
    assert (tmp_path / "a.jsonl.1").is_file()
    assert (tmp_path / "a.jsonl").stat().st_size <= 200 + 200


@pytest.mark.skipif(os.name != "posix", reason="POSIX modes")
def test_the_log_is_private(tmp_path):
    ActivityLog(tmp_path / "a.jsonl").append(ActivityEntry(0.0, "headless", "c", "t", "s"))
    assert (tmp_path / "a.jsonl").stat().st_mode & 0o077 == 0


def test_an_unwritable_log_does_not_raise(tmp_path):
    log = ActivityLog(tmp_path / "missing-dir-file" / "x" / "a.jsonl")
    (tmp_path / "missing-dir-file").write_text("a file, not a folder")
    with pytest.warns(UserWarning):
        log.append(ActivityEntry(0.0, "headless", "c", "t", "s"))


def test_an_unwritable_log_warns_once(tmp_path):
    log = ActivityLog(tmp_path / "missing-dir-file" / "x" / "a.jsonl")
    (tmp_path / "missing-dir-file").write_text("a file, not a folder")
    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        log.append(ActivityEntry(0.0, "headless", "c", "t", "s"))
        log.append(ActivityEntry(1.0, "headless", "c", "t", "s"))
    assert len([w for w in caught if issubclass(w.category, UserWarning)]) == 1


def test_last_returns_the_newest_entries_oldest_first(tmp_path):
    log = ActivityLog(tmp_path / "a.jsonl")
    for i in range(5):
        log.append(ActivityEntry(float(i), "app", "c", "t", f"step {i}"))
    assert [e.summary for e in log.last(3)] == ["step 2", "step 3", "step 4"]


def test_last_skips_broken_lines(tmp_path):
    path = tmp_path / "a.jsonl"
    log = ActivityLog(path)
    log.append(ActivityEntry(0.0, "app", "c", "t", "first"))
    with open(path, "a", encoding="utf-8") as handle:
        handle.write("not json\n")
        handle.write(json.dumps({"unexpected": True}) + "\n")
    log.append(ActivityEntry(1.0, "app", "c", "t", "second"))
    assert [e.summary for e in log.last(10)] == ["first", "second"]


def test_last_of_a_missing_log_is_empty(tmp_path):
    assert ActivityLog(tmp_path / "none.jsonl").last(5) == []


def test_an_entry_keeps_its_optional_fields(tmp_path):
    log = ActivityLog(tmp_path / "a.jsonl")
    entry = ActivityEntry(
        2.5, "app", "claude 1", "start_run", "start run", ask="uses 4 cores", answer="denied"
    )
    log.append(entry)
    assert log.last(1) == [entry]


def test_the_default_path_is_under_the_data_directory(tmp_path, monkeypatch):
    monkeypatch.setenv("PYSRIM2_HOME", str(tmp_path))
    assert activity_path() == tmp_path / "agent" / "activity.jsonl"
    assert ActivityLog().path == tmp_path / "agent" / "activity.jsonl"
