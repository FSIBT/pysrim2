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


# --- the entries the agent sends (untrusted) and the tail read ----------------------------------

from srim.mcp.activity import FIELD_CAP, TAIL_BYTES, entry_from_untrusted  # noqa: E402


def good(**changes):
    data = {
        "time": 1700000000.0,
        "mode": "headless",
        "client": "claude 1",
        "tool": "set_values",
        "summary": "set ion.energy_keV=40",
        "ask": None,
        "answer": None,
        "error": None,
    }
    data.update(changes)
    return data


def test_a_valid_entry_is_kept_as_it_is():
    entry = entry_from_untrusted(good(ask="uses 4 cores", answer="denied"))
    assert entry == ActivityEntry(
        1700000000.0,
        "headless",
        "claude 1",
        "set_values",
        "set ion.energy_keV=40",
        ask="uses 4 cores",
        answer="denied",
    )


def test_the_optional_fields_may_be_left_out():
    data = good()
    for name in ("ask", "answer", "error"):
        del data[name]
    assert entry_from_untrusted(data).error is None


@pytest.mark.parametrize(
    "data",
    [
        "not an object",
        None,
        [],
        good(time="12"),
        good(time=True),
        good(time=float("nan")),
        good(time=float("inf")),
        good(time=-1),
        good(time=1e30),
        good(mode="remote"),
        good(client=3),
        good(summary=None),
        good(answer=["x"]),
        good(unknown=1),
    ],
)
def test_a_bad_shape_is_refused(data):
    with pytest.raises(ValueError):
        entry_from_untrusted(data)


def test_a_missing_required_field_is_refused():
    data = good()
    del data["tool"]
    with pytest.raises(ValueError):
        entry_from_untrusted(data)


def test_a_huge_field_is_cut_to_the_cap():
    entry = entry_from_untrusted(good(summary="x" * 1_000_000))
    assert len(entry.summary) == FIELD_CAP
    assert entry.summary.endswith("…")


def test_markup_is_kept_as_plain_text_in_the_entry():
    text = "<b>bold</b> <a href='https://example.com'>link</a>"
    assert entry_from_untrusted(good(summary=text)).summary == text


def test_last_reads_only_the_tail_of_a_large_log(tmp_path):
    path = tmp_path / "a.jsonl"
    log = ActivityLog(path, max_bytes=10**9)
    for i in range(3000):
        log.append(ActivityEntry(float(i), "headless", "c", "t", f"step {i} " + "y" * 300))
    assert path.stat().st_size > 2 * TAIL_BYTES
    assert [e.summary[:9] for e in log.last(3)] == ["step 2997", "step 2998", "step 2999"]


def test_last_reads_a_log_with_bad_bytes(tmp_path):
    path = tmp_path / "a.jsonl"
    path.write_bytes(b"\xff\xfe not text\n")
    log = ActivityLog(path)
    log.append(ActivityEntry(0.0, "app", "c", "t", "after the bad bytes"))
    assert [e.summary for e in log.last(5)] == ["after the bad bytes"]


def test_a_tail_that_starts_exactly_on_a_line_keeps_that_line(tmp_path):
    # the cut falls on a line start: the byte before the tail is a newline, so no line is lost
    def line(text):
        return (
            json.dumps(ActivityEntry(1.0, "app", "c", "t", text).to_dict(), sort_keys=True) + "\n"
        )

    head = line("head")
    first = line("first")
    padding = TAIL_BYTES - len(first) - len(line(""))
    path = tmp_path / "a.jsonl"
    path.write_bytes((head + first + line("x" * padding)).encode("utf-8"))
    assert path.stat().st_size == len(head.encode()) + TAIL_BYTES
    summaries = [entry.summary for entry in ActivityLog(path).last(2)]
    assert summaries == ["first", "x" * padding]
