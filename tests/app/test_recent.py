"""The recent setups and runs (Qt-free): the file, its entries, and what is wrong with an entry."""

import json
from datetime import UTC, datetime

from srim.app.recent import (
    RECENT_LIMIT,
    RUN,
    SETUP,
    RecentEntry,
    add_recent,
    entry_problem,
    load_recent,
    new_entry,
    recent_path,
)


def test_the_file_is_in_the_app_folder_of_the_data_dir(app_home):
    assert recent_path() == app_home / "app" / "recent.json"


def test_adding_the_same_path_twice_keeps_one_entry_at_the_front(tmp_path):
    path = tmp_path / "recent.json"
    setup = tmp_path / "a" / "run.yaml"
    other = tmp_path / "b" / "run.yaml"
    add_recent(new_entry(setup, SETUP), path)
    add_recent(new_entry(other, SETUP), path)
    add_recent(new_entry(setup, SETUP), path)
    entries = load_recent(path)
    assert [entry.path for entry in entries] == [str(setup), str(other)]


def test_eleven_entries_keep_the_newest_ten(tmp_path):
    path = tmp_path / "recent.json"
    for number in range(11):
        add_recent(new_entry(tmp_path / f"{number}" / "run.yaml", SETUP), path)
    entries = load_recent(path)
    assert RECENT_LIMIT == 10
    assert len(entries) == 10
    assert entries[0].path == str(tmp_path / "10" / "run.yaml")
    assert entries[-1].path == str(tmp_path / "1" / "run.yaml")


def test_a_broken_file_reads_as_no_entries(tmp_path):
    path = tmp_path / "recent.json"
    path.write_text("{ not json", encoding="utf-8")
    assert load_recent(path) == []
    path.write_text('{"path": "x"}', encoding="utf-8")
    assert load_recent(path) == []


def test_a_missing_file_reads_as_no_entries(tmp_path):
    assert load_recent(tmp_path / "absent.json") == []


def test_entries_that_are_not_recent_entries_are_skipped(tmp_path):
    path = tmp_path / "recent.json"
    good = {
        "path": str(tmp_path / "run.yaml"),
        "kind": SETUP,
        "opened": "2026-11-12T00:00:00+00:00",
    }
    bad_kind = {**good, "kind": "elsewhere"}
    no_path = {"kind": RUN, "opened": good["opened"]}
    path.write_text(json.dumps(["nonsense", bad_kind, no_path, good]), encoding="utf-8")
    assert load_recent(path) == [RecentEntry(**good)]


def test_a_write_leaves_no_temporary_file_behind(tmp_path):
    path = tmp_path / "recent.json"
    add_recent(new_entry(tmp_path / "run.yaml", SETUP), path)
    assert sorted(p.name for p in tmp_path.iterdir()) == ["recent.json"]


def test_new_entry_stamps_the_time_in_utc(tmp_path):
    when = datetime(2026, 11, 12, 9, 30, 5, tzinfo=UTC)
    entry = new_entry(tmp_path / "run.yaml", RUN, now=when)
    assert entry.kind == RUN
    assert entry.opened == "2026-11-12T09:30:05+00:00"


def test_a_missing_setup_file_is_a_problem(tmp_path):
    entry = RecentEntry(str(tmp_path / "gone.yaml"), SETUP, "")
    assert entry_problem(entry) == "the file was moved or deleted"


def test_an_existing_setup_file_has_no_problem(tmp_path):
    setup = tmp_path / "run.yaml"
    setup.write_text("x", encoding="utf-8")
    assert entry_problem(RecentEntry(str(setup), SETUP, "")) is None


def test_a_missing_run_folder_is_a_problem(tmp_path):
    entry = RecentEntry(str(tmp_path / "gone"), RUN, "")
    assert entry_problem(entry) == "the folder was moved or deleted"


def test_a_folder_without_the_scan_marker_is_not_a_run_folder(tmp_path):
    folder = tmp_path / "plain"
    folder.mkdir()
    assert entry_problem(RecentEntry(str(folder), RUN, "")) == "not a pysrim2 run folder"


def test_a_scan_folder_has_no_problem(tmp_path):
    folder = tmp_path / "scan"
    folder.mkdir()
    (folder / ".pysrim2-scan").write_text("pysrim2 scan folder\n", encoding="utf-8")
    assert entry_problem(RecentEntry(str(folder), RUN, "")) is None
