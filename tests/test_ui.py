"""The plain and JSON views of a scan, fed with events built by hand (no SRIM, no workers)."""

import io
import json

import pytest

from srim.scan.events import (
    PointCached,
    PointFailed,
    PointFinished,
    PointProgress,
    PointQueued,
    PointStarted,
    PointStopped,
    ScanFinished,
    ScanStarted,
)
from srim.ui import JsonLines, PlainProgress


class Clock:
    def __init__(self):
        self.now = 1000.0

    def __call__(self):
        return self.now


def start(n_points=4, workers=2):
    return ScanStarted(n_points, workers, 7, "scan-x")


def queue_all(n):
    return [PointQueued(i, f"id{i}") for i in range(n)]


@pytest.fixture
def view():
    clock = Clock()
    stream = io.StringIO()
    return PlainProgress(stream, interval=2.0, clock=clock), clock, stream


def lines(stream):
    return stream.getvalue().splitlines()


def test_plain_prints_the_start_and_the_counts(view):
    progress, clock, stream = view
    progress(start())
    for event in queue_all(4):
        progress(event)
    assert lines(stream) == [
        "done 0/4  running 0  queued 0  failed 0  ions 0  elapsed 0:00:00  eta ?"
    ]
    assert progress.status == {0: "queued", 1: "queued", 2: "queued", 3: "queued"}


def test_plain_throttles_and_prints_only_on_change(view):
    progress, clock, stream = view
    progress(start())
    for event in queue_all(4):
        progress(event)
    progress(PointStarted(0, "id0", 0, 1))  # a change, but the last line is under 2 s old
    assert len(lines(stream)) == 1
    clock.now += 2.5
    progress(PointStarted(1, "id1", 1, 1))  # the state is printed at the next event
    assert lines(stream)[-1].startswith("done 0/4  running 2  queued 2")
    count = len(lines(stream))
    clock.now += 5.0
    progress(PointProgress(0, 40, 100))  # ions changed: printed
    assert len(lines(stream)) == count + 1
    progress(PointProgress(0, 40, 100))  # nothing changed: not printed
    assert len(lines(stream)) == count + 1


def test_plain_eta_follows_the_points_finished(view):
    progress, clock, stream = view
    progress(start(n_points=4, workers=2))
    for event in queue_all(4):
        progress(event)
    progress(PointStarted(0, "id0", 0, 1))
    clock.now += 10.0
    progress(PointFinished(0, "id0", {}, 100, 9.0))
    assert lines(stream)[-1] == (
        "done 1/4  running 0  queued 3  failed 0  ions 100  elapsed 0:00:10  eta 0:00:30"
    )


def test_plain_counts_failed_and_stopped_points(view):
    progress, clock, stream = view
    progress(start(n_points=3))
    for event in queue_all(3):
        progress(event)
    progress(PointFailed(0, "id0", "boom", 1, final=False))  # retried: not failed yet
    progress(PointFailed(0, "id0", "boom", 2, final=True))
    clock.now += 5.0
    progress(PointStopped(1, "id1", None))
    progress(ScanFinished(n_done=0, n_failed=1, seconds=5.0, stopped=True))
    assert lines(stream)[-1] == (
        "finished 0/3 done, 1 failed, 1 stopped (stopped by the user)  ions 0  elapsed 0:00:05"
    )


def test_plain_final_line_is_always_printed(view):
    progress, clock, stream = view
    progress(start(n_points=1))
    progress(PointQueued(0, "id0"))
    progress(PointStarted(0, "id0", 0, 1))
    progress(PointFinished(0, "id0", {}, 5, 1.0))  # within 2 s of the start: not printed yet
    progress(ScanFinished(n_done=1, n_failed=0, seconds=1.0, stopped=False))
    assert lines(stream)[-1] == "finished 1/1 done, 0 failed, 0 stopped  ions 5  elapsed 0:00:00"


def test_plain_writes_no_escape_codes(view):
    progress, clock, stream = view
    progress(start())
    progress(ScanFinished(n_done=0, n_failed=0, seconds=0.0, stopped=False))
    assert "\x1b" not in stream.getvalue()


def test_plain_defaults_to_stdout_at_print_time(capsys):
    progress = PlainProgress()
    progress(start(n_points=1))
    assert capsys.readouterr().out.startswith("done 0/1")


def test_json_writes_each_event_as_one_line():
    stream = io.StringIO()
    consumer = JsonLines(stream)
    events = [start(), PointQueued(0, "id0"), PointFinished(0, "id0", {"x": 1.5}, 10, 0.5)]
    for event in events:
        consumer(event)
    rows = [json.loads(line) for line in lines(stream)]
    assert [row["kind"] for row in rows] == ["scan_started", "point_queued", "point_finished"]
    assert rows[2]["values"] == {"x": 1.5}
    assert all("time" in row for row in rows)


def test_json_turns_non_finite_numbers_into_null():
    stream = io.StringIO()
    JsonLines(stream)(PointFinished(0, "id0", {"x": float("nan")}, 1, 0.0))
    assert json.loads(stream.getvalue())["values"] == {"x": None}


def test_plain_final_line_counts_the_cached_points_as_done(view):
    progress, clock, stream = view
    progress(start(n_points=3))
    for event in queue_all(3):
        progress(event)
    progress(PointCached(0, "id0", 0))  # every unit of this point came from the cache
    progress(PointFinished(0, "id0", {}, 100, 0.0))
    progress(PointStarted(1, "id1", 0, 1))  # this one ran
    progress(PointFinished(1, "id1", {}, 100, 1.0))
    progress(PointCached(2, "id2", 0))
    progress(PointFinished(2, "id2", {}, 100, 0.0))
    progress(ScanFinished(n_done=3, n_failed=0, seconds=2.0, stopped=False))
    assert lines(stream)[-1] == (
        "finished 3/3 done (2 cached), 0 failed, 0 stopped  ions 300  elapsed 0:00:00"
    )
