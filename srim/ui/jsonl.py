"""``--ui json``: every scan event as one line of JSON (the events' ``to_json()``)."""

import sys


class JsonLines:
    """Write each event to ``stream`` (default: ``sys.stdout``, looked up at each call) as one
    line of JSON, and flush it, so a reader sees the events as they happen."""

    def __init__(self, stream=None):
        self.stream = stream

    def __call__(self, event) -> None:
        print(event.to_json(), file=self.stream or sys.stdout, flush=True)
