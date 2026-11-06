"""The json and plain views when the reader has closed the pipe (``... | head``): they stop
writing quietly, without a warning per event, and the scan's events go on to the other
consumers. Each test replaces the stdout redirection, so the test process keeps its stdout.
"""

import pytest

from srim.scan.events import PointQueued, ScanFinished, ScanStarted
from srim.ui import plain
from srim.ui.jsonl import JsonLines


class ClosedPipe:
    """A stream whose reader has gone: every write fails like a closed pipe."""

    def __init__(self):
        self.writes = 0

    def write(self, text):
        self.writes += 1
        raise BrokenPipeError(32, "Broken pipe")

    def flush(self):
        pass


@pytest.fixture
def silenced(monkeypatch):
    calls = []
    monkeypatch.setattr(plain, "silence_stdout", lambda: calls.append(True))
    monkeypatch.setattr("srim.ui.jsonl.silence_stdout", lambda: calls.append(True))
    return calls


def test_json_lines_stop_after_a_closed_pipe_without_an_error(silenced):
    stream = ClosedPipe()
    consumer = JsonLines(stream)
    events = [PointQueued(0, "a"), PointQueued(1, "b"), ScanFinished(0, 0, 0.0, False)]
    for event in events:
        consumer(event)  # no exception reaches the caller
    assert stream.writes == 1
    assert consumer.closed
    assert silenced == [True]


def test_plain_progress_stops_after_a_closed_pipe_without_an_error(silenced):
    stream = ClosedPipe()
    progress = plain.PlainProgress(stream, interval=0.0)
    progress(ScanStarted(2, 1, 7, "x"))
    progress(PointQueued(0, "a"))
    progress(ScanFinished(0, 0, 0.0, False))
    assert stream.writes == 1
    assert progress.closed
    assert silenced == [True]
