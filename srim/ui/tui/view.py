"""The browser viewer of a scan: ``python -m srim.ui.tui.view SCAN_FOLDER [--plot X,Y[,HUE]]``.

textual-serve runs this once per browser tab (see :mod:`srim.ui.web`). It shows the scan of the
folder in the same app as the terminal views, fed from the folder's ``events.jsonl``: it replays
the file from the start and then follows it, so a tab opened late shows what has happened. The
keys ``p`` and ``q`` write requests to ``control.jsonl``, which the CLI acts on; the pause state
of the scan comes back to the viewer in its ``scan_paused`` events. The viewer does not exit
when the scan ends, so the final state stays on screen until the server is stopped.

A new run in the same folder truncates the events file; a page left open on the old run keeps
its state, so reopen the page to see the new one.
"""

from __future__ import annotations

import argparse
import json
import queue
import sys
import threading
from dataclasses import dataclass
from pathlib import Path

from srim.scan import Scan
from srim.scan.events import ScanPaused, event_from_dict
from srim.spec import SpecError
from srim.ui.web import EVENTS_FILE, RUN_KIND, exit_with_parent, send_request

from .app import ScanApp
from .plot import check_plot
from .state import ScanState

#: seconds between two reads of the events file
POLL_SECONDS = 0.2


@dataclass(frozen=True)
class NewRun:
    """Not an event: the events file started a new run (``run_id``; None when it was
    truncated), so the viewer starts its state again."""

    run_id: str | None


class EventFollower(threading.Thread):
    """Puts every event of the events file into ``events``, from the start of the file and
    then as lines are appended. A line is taken only when it is complete (ends with a newline);
    a line that does not parse is skipped. A ``scan_paused`` event sets or clears
    ``pause_event``, so that the viewer's ``p`` key toggles the scan's actual state.

    A run header (``{"kind": "run", "run_id": ...}``) with a new id, and a file that shrank
    (truncated by a new run), put a :class:`NewRun` in the queue first."""

    def __init__(self, path, events: queue.Queue, pause_event, *, poll=POLL_SECONDS):
        super().__init__(name="pysrim2-events", daemon=True)
        self.path = Path(path)
        self.events = events
        self.pause_event = pause_event
        self.poll = poll
        self.stopped = threading.Event()
        self._offset = 0
        self._pending = b""
        self._run_id = None

    def run(self) -> None:
        while not self.stopped.is_set():
            self.pump()
            self.stopped.wait(self.poll)

    def pump(self) -> int:
        """Take what the file has gained since the last call; returns the events taken."""
        try:
            size = self.path.stat().st_size
        except FileNotFoundError:  # the scan has not started yet
            return 0
        if size < self._offset:  # truncated by a new run: read it again from the start
            self._offset, self._pending = 0, b""
            self._run_id = None
            self.events.put(NewRun(None))
        if size == self._offset:
            return 0
        with self.path.open("rb") as file:
            file.seek(self._offset)
            data = file.read(size - self._offset)
        self._offset += len(data)
        *lines, self._pending = (self._pending + data).split(b"\n")
        taken = 0
        for line in lines:
            if self._take(line):
                taken += 1
        return taken

    def _take(self, line: bytes) -> bool:
        text = line.strip()
        if not text:
            return False
        try:
            data = json.loads(text)
            if data.get("kind") == RUN_KIND:
                if data.get("run_id") != self._run_id:
                    self._run_id = data.get("run_id")
                    self.events.put(NewRun(self._run_id))
                return False
            event = event_from_dict(data)
        except (ValueError, TypeError, AttributeError):  # not an event this version knows
            return False
        if isinstance(event, ScanPaused):
            if event.paused:
                self.pause_event.set()
            else:
                self.pause_event.clear()
        self.events.put(event)
        return True


class WebScanApp(ScanApp):
    """:class:`~srim.ui.tui.app.ScanApp` whose ``p`` and ``q`` keys are requests to the CLI
    (``control.jsonl``) instead of changes of the flags of this process."""

    def __init__(self, scan, events, *, folder, stop_event, pause_event, view="plot", plot=None):
        super().__init__(
            scan,
            events,
            stop_event=stop_event,
            pause_event=pause_event,
            view=view,
            plot=plot,
        )
        self.scan = scan
        self.folder = Path(folder)

    def take(self, event) -> None:
        if isinstance(event, NewRun):  # a new run: the state and the screens start again
            self.state = ScanState(self.scan)
            for name in ("plot", "dashboard"):
                self.get_screen(name).forget()
            return
        super().take(event)

    def action_toggle_pause(self) -> None:
        super().action_toggle_pause()
        self._request("pause" if self.pause_event.is_set() else "resume")

    def action_stop(self) -> None:
        was_stopping = self.stop_event.is_set()
        super().action_stop()
        if not was_stopping:
            self._request("stop")

    def _request(self, word: str) -> None:
        try:
            send_request(self.folder, word)
        except OSError as exc:
            self.notify(f"could not send {word!r} to the scan: {exc}", severity="error")


def main(argv: list[str] | None = None) -> int:
    """Run the viewer of the scan in the folder given (exit 2 if it is not a scan folder)."""
    parser = argparse.ArgumentParser(prog="python -m srim.ui.tui.view")
    parser.add_argument("folder", help="the scan folder")
    parser.add_argument("--plot", default=None, help="X,Y[,HUE] (see pysrim2 scan run)")
    args = parser.parse_args(argv)
    folder = Path(args.folder)
    try:
        scan = Scan.from_yaml(folder / "scan.yaml")
        check_plot(scan.param_names, args.plot)
    except (OSError, SpecError, ValueError) as exc:
        print(f"{folder}: not a scan folder ({exc})", file=sys.stderr)
        return 2
    exit_with_parent()  # the server (or the CLI) is gone: so is this viewer
    events: queue.Queue = queue.Queue()
    pause_event, stop_event = threading.Event(), threading.Event()
    follower = EventFollower(folder / EVENTS_FILE, events, pause_event)
    follower.start()
    try:
        WebScanApp(
            scan,
            events,
            folder=folder,
            stop_event=stop_event,
            pause_event=pause_event,
            plot=args.plot,
        ).run()
    finally:
        follower.stopped.set()
    return 0


__all__ = ["EventFollower", "WebScanApp", "main"]


if __name__ == "__main__":
    raise SystemExit(main())
