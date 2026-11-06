"""``--ui plain``: a status line of a scan, printed when its state changes.

A line looks like::

    done 3/10  running 2  queued 5  failed 0  ions 1200  elapsed 0:01:02  eta 0:02:10

The line is printed when the counts or the ions change, and at most once every ``interval``
seconds (2 by default); the last change is printed when the scan ends, always. Lines are
appended, never redrawn, and no escape codes are written, so the output is the same in a
terminal, over SSH, in tmux or in a log file.
"""

import sys
import time

#: seconds between two status lines (a change inside this time is printed at the next event)
INTERVAL_SECONDS = 2.0


def _hms(seconds: float) -> str:
    total = int(seconds)
    return f"{total // 3600}:{total % 3600 // 60:02d}:{total % 60:02d}"


class PlainProgress:
    """Event consumer that prints the status of a scan to ``stream`` (default: ``sys.stdout``,
    looked up at each print). ``clock`` gives seconds (for the elapsed time and the throttle)."""

    def __init__(self, stream=None, *, interval=INTERVAL_SECONDS, clock=time.monotonic):
        self.stream = stream
        self.interval = interval
        self.clock = clock
        self.n_points = 0
        self.workers = 0
        self.started = clock()
        self.status: dict[int, str] = {}  # point index -> queued, running, done, failed, stopped
        self.ions: dict[int, int] = {}  # point index -> ions run so far
        self._last_key = None
        self._last_printed = None
        self._handlers = {
            "scan_started": self._scan_started,
            "point_queued": self._queued,
            "point_started": self._started,
            "point_cached": self._started,
            "point_progress": self._progress,
            "point_finished": self._finished,
            "point_failed": self._failed,
            "point_stopped": self._stopped,
            "scan_finished": self._scan_finished,
        }

    # -- event handling ----------------------------------------------------------------------

    def __call__(self, event) -> None:
        handler = self._handlers.get(event.kind)
        if handler is not None:
            handler(event)
        if event.kind == "scan_started":
            self._print(force=True)
        elif event.kind != "scan_finished":
            self._print()

    def _scan_started(self, event):
        self.n_points = event.n_points
        self.workers = event.workers
        self.started = self.clock()

    def _queued(self, event):
        self.status[event.index] = "queued"

    def _started(self, event):
        self.status[event.index] = "running"

    def _progress(self, event):
        self.ions[event.index] = event.ions_done

    def _finished(self, event):
        self.status[event.index] = "done"
        self.ions[event.index] = event.n_ions

    def _failed(self, event):
        if event.final:
            self.status[event.index] = "failed"

    def _stopped(self, event):
        self.status[event.index] = "stopped"

    def _scan_finished(self, event):
        self._print(force=True, final=True, stopped=event.stopped)

    # -- the line -----------------------------------------------------------------------------

    def _count(self, state: str) -> int:
        return sum(1 for s in self.status.values() if s == state)

    def _key(self) -> tuple:
        return tuple(self._count(s) for s in ("queued", "running", "done", "failed", "stopped")) + (
            sum(self.ions.values()),
        )

    def _print(self, *, force=False, final=False, stopped=False):
        key = self._key()
        if not force and key == self._last_key:
            return
        now = self.clock()
        if (
            not force
            and self._last_printed is not None
            and now - self._last_printed < self.interval
        ):
            return
        self._last_key = key
        self._last_printed = now
        elapsed = now - self.started
        queued, running, done, failed, stopped_n, ions = key
        finished = done + failed
        remaining = self.n_points - finished - stopped_n
        if final:
            text = (
                f"finished {done}/{self.n_points} done, {failed} failed, {stopped_n} stopped"
                f"{' (stopped by the user)' if stopped else ''}  ions {ions}  "
                f"elapsed {_hms(elapsed)}"
            )
        else:
            if finished and remaining > 0:
                eta = _hms(elapsed / finished * remaining)
            elif remaining <= 0:
                eta = "0:00:00"
            else:
                eta = "?"
            text = (
                f"done {done}/{self.n_points}  running {running}  queued {queued}  "
                f"failed {failed}  ions {ions}  elapsed {_hms(elapsed)}  eta {eta}"
            )
        print(text, file=self.stream or sys.stdout, flush=True)
