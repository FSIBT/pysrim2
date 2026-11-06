"""``--ui json``: every scan event as one line of JSON (the events' ``to_json()``)."""

import contextlib
import os
import sys


def silence_stdout() -> None:
    """Point file descriptor 1 at the null device, after its reader has gone (a closed pipe).

    Later writes to stdout then succeed silently, and the interpreter's final flush does not
    report a broken pipe either.
    """
    with contextlib.suppress(OSError, ValueError):
        null = os.open(os.devnull, os.O_WRONLY)
        os.dup2(null, sys.stdout.fileno())


class JsonLines:
    """Write each event to ``stream`` (default: ``sys.stdout``, looked up at each call) as one
    line of JSON, and flush it, so a reader sees the events as they happen.

    When the reader has closed the pipe (``pysrim2 scan run --ui json | head``), the lines
    stop, quietly: the scan itself is not affected by its output.
    """

    def __init__(self, stream=None):
        self.stream = stream
        self.closed = False

    def __call__(self, event) -> None:
        if self.closed:
            return
        try:
            print(event.to_json(), file=self.stream or sys.stdout, flush=True)
        except BrokenPipeError:
            self.closed = True
            silence_stdout()
