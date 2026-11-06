"""The state of a scan as the live views show it, built from the scan's events.

:class:`ScanState` is a pure function of the events it is given (:meth:`ScanState.apply`):
no terminal, no thread and no Textual, so the logic is tested without a UI. The app
(:mod:`srim.ui.tui.app`) applies the events it drains from the scan thread, then reads the
state to draw its views. Statuses of a point: ``queued``, ``running``, ``done`` (a worker
ran it), ``cached`` (every unit came from the cache), ``failed`` and ``stopped``.
"""

from __future__ import annotations

import math
from collections import Counter, deque
from dataclasses import dataclass, field
from pathlib import Path

from ...scan.precision import Precision, goal_of, initial_batches, is_met

#: the statuses of a point, in the order the views list them
STATUSES = ("queued", "running", "done", "cached", "failed", "stopped")
#: the glyphs of :func:`sparkline`, lowest to highest
_BLOCKS = "▁▂▃▄▅▆▇█"
#: events that change a point's row in the points table
_ROW_EVENTS = frozenset(
    {
        "point_queued",
        "point_started",
        "point_cached",
        "point_retried",
        "point_finished",
        "point_failed",
        "point_stopped",
        "batch_finished",
    }
)
#: events that change what the plot shows (values and terminal statuses)
_DATA_EVENTS = frozenset(
    {"point_finished", "point_failed", "point_stopped", "point_cached", "batch_finished"}
)
#: most recent warnings and errors kept
WARNINGS_KEPT = 100
#: most recent throughput samples kept for the sparkline
THROUGHPUT_KEPT = 120


def is_number(value) -> bool:
    """True for a real number that is not NaN or infinite (booleans do not count)."""
    return isinstance(value, int | float) and not isinstance(value, bool) and math.isfinite(value)


def format_hms(seconds: float) -> str:
    """``h:mm:ss`` of a number of seconds (the way the plain status line writes time)."""
    total = int(seconds)
    return f"{total // 3600}:{total % 3600 // 60:02d}:{total % 60:02d}"


def format_value(value, err=None) -> str:
    """``value ± err`` with four and two significant digits; ``—`` when there is no value."""
    if not is_number(value):
        return "—"
    if is_number(err):
        return f"{value:.4g} ± {err:.2g}"
    return f"{value:.4g}"


def status_line(state: ScanState, now: float, note: str = "") -> str:
    """The status strip of the views, written like the plain status line (see
    :mod:`srim.ui.plain`), with ``[paused]`` and the ``note`` (e.g. ``stopping…``) added."""
    counts = state.counts()
    done = counts["done"] + counts["cached"]
    elapsed = format_hms(state.elapsed(now))
    ions = state.total_ions()
    if state.finished:
        text = (
            f"finished {done}/{state.n_points}  failed {counts['failed']}  "
            f"stopped {counts['stopped']}  ions {ions}  elapsed {elapsed}"
        )
    else:
        eta = state.eta_seconds()
        eta_text = "?" if eta is None else format_hms(eta)
        text = (
            f"done {done}/{state.n_points}  running {counts['running']}  "
            f"queued {counts['queued']}  failed {counts['failed']}  ions {ions}  "
            f"elapsed {elapsed}  eta {eta_text}"
        )
    if state.paused:
        text += "  [paused]"
    if note:
        text += f"  {note}"
    return text


def sparkline(values, width: int = 40) -> str:
    """The last ``width`` numbers as a line of block glyphs (empty when there are none).

    Missing values (None or NaN) are skipped; a flat series is drawn at mid height.
    """
    numbers = [float(v) for v in values if is_number(v)][-width:]
    if not numbers:
        return ""
    low, high = min(numbers), max(numbers)
    if high == low:
        return _BLOCKS[len(_BLOCKS) // 2] * len(numbers)
    top = len(_BLOCKS) - 1
    return "".join(_BLOCKS[round((v - low) / (high - low) * top)] for v in numbers)


@dataclass
class PointRow:
    """What the views show of one point. ``ions`` are the ions done (the finished batches of
    a precision point), ``planned`` the ions planned so far (``None`` when not known yet),
    ``values`` the latest values by column (``<name>_err`` for their errors)."""

    index: int
    id: str
    params: dict
    status: str = "queued"
    started: bool = False
    ions: int = 0
    planned: int | None = None
    values: dict = field(default_factory=dict)
    error: str | None = None
    seconds: float = 0.0
    reached: bool | None = None
    worker: int | None = None
    cached_units: int = 0


@dataclass
class WorkerRow:
    """What the views show of one worker: the point it runs (``None`` when idle)."""

    ident: int
    point: int | None = None
    ions_per_s: float | None = None


def _orderable(value):
    """A sort key that puts numbers first (in order), then text, then missing values."""
    if value is None or (isinstance(value, float) and math.isnan(value)):
        return (2, 0.0, "")
    if is_number(value):
        return (0, float(value), "")
    return (1, 0.0, str(value))


class ScanState:
    """The scan as the views see it. ``scan`` gives the points (their params and ions).

    Columns of the points table are :meth:`table_columns`: ``index``, one ``param:<name>``
    per scanned key, ``status``, ``ions``, one ``value:<name>`` per extracted value (in the
    order they were first seen) and ``reached`` (the precision target met; ``—`` otherwise).
    """

    def __init__(self, scan):
        self.scan = scan
        self.precision: Precision | None = scan.ions if isinstance(scan.ions, Precision) else None
        self.param_names = list(scan.param_names)
        self.points: dict[int, PointRow] = {}
        for point in scan.points():
            planned = None if self.precision is not None else point.spec.number_ions
            self.points[point.index] = PointRow(
                point.index, point.id, dict(point.params), planned=planned
            )
        self.n_points = len(self.points)
        self.workers: dict[int, WorkerRow] = {}
        self.n_workers = 0
        self.base_seed: int | None = None
        self.scan_dir: Path | None = None
        self.started_at: float | None = None
        self.finished_at: float | None = None
        self.stopped = False
        self.finished = False
        self.paused = False
        self.last_failed: int | None = None
        self.warnings: deque = deque(maxlen=WARNINGS_KEPT)  # (level, text)
        self.throughput: deque = deque(maxlen=THROUGHPUT_KEPT)  # ions/s of finished units
        self.value_names: list[str] = []  # extracted value columns, in the order seen
        #: bumped by every event (the strip and the workers panel follow this one)
        self.live_version = 0
        #: bumped by the events that change a point's row (the points table follows this)
        self.rows_version = 0
        #: bumped by the events that change the plotted data (the plot follows this one)
        self.data_version = 0
        self._handlers = {
            "scan_started": self._on_scan_started,
            "point_queued": self._on_queued,
            "point_started": self._on_started,
            "point_cached": self._on_cached,
            "point_progress": self._on_progress,
            "point_finished": self._on_finished,
            "point_failed": self._on_failed,
            "point_retried": self._on_retried,
            "point_stopped": self._on_stopped,
            "batch_finished": self._on_batch,
            "worker_stats": self._on_worker_stats,
            "scan_paused": self._on_paused,
            "scan_finished": self._on_scan_finished,
        }

    # -- events ---------------------------------------------------------------------------

    def apply(self, event) -> None:
        """Take one event (see :mod:`srim.scan.events`) into the state. Unknown kinds are
        ignored; events of unknown points are ignored."""
        handler = self._handlers.get(event.kind)
        if handler is None:
            return
        handler(event)
        self.live_version += 1
        if event.kind in _ROW_EVENTS:
            self.rows_version += 1
        if event.kind in _DATA_EVENTS:
            self.data_version += 1

    def _row(self, index):
        return self.points.get(index)

    def _worker(self, ident) -> WorkerRow:
        if ident not in self.workers:
            self.workers[ident] = WorkerRow(ident)
        return self.workers[ident]

    def _free(self, index) -> None:
        for worker in self.workers.values():
            if worker.point == index:
                worker.point = None

    def _on_scan_started(self, event):
        self.n_workers = event.workers
        self.base_seed = event.base_seed
        self.scan_dir = Path(event.scan_dir)
        self.started_at = event.time

    def _on_queued(self, event):
        row = self._row(event.index)
        if row is not None:
            row.status = "queued"

    def _on_started(self, event):
        row = self._row(event.index)
        if row is None:
            return
        row.status = "running"
        row.started = True
        row.worker = event.worker
        self._worker(event.worker).point = event.index

    def _on_cached(self, event):
        row = self._row(event.index)
        if row is None:
            return
        row.cached_units += 1
        if row.status == "queued":
            row.status = "running"

    def _on_progress(self, event):
        row = self._row(event.index)
        if row is not None:
            row.ions = event.ions_done
            row.planned = event.ions_total

    def _on_finished(self, event):
        row = self._row(event.index)
        if row is None:
            return
        row.status = "done" if row.started else "cached"
        row.ions = event.n_ions
        row.planned = event.n_ions
        row.values = dict(event.values)
        row.seconds = event.seconds
        row.error = None
        row.reached = self._reached(row.values)
        self._note_values(event.values)
        self._free(event.index)

    def _on_batch(self, event):
        row = self._row(event.index)
        if row is None:
            return
        for name, (value, err) in event.value_estimates.items():
            row.values[name] = value
            row.values[f"{name}_err"] = err
        row.planned = event.ions
        row.reached = self._reached(row.values)
        self._note_values(event.value_estimates)

    def _on_failed(self, event):
        row = self._row(event.index)
        if row is None:
            return
        row.error = event.error
        self._free(event.index)
        if event.final:
            row.status = "failed"
            self.last_failed = event.index
            self.warnings.append(("error", f"point {event.index}: {event.error}"))
        else:
            self.warnings.append(
                ("warning", f"point {event.index} attempt {event.attempt} failed: {event.error}")
            )

    def _on_retried(self, event):
        row = self._row(event.index)
        if row is not None:
            row.status = "queued"

    def _on_stopped(self, event):
        row = self._row(event.index)
        if row is None:
            return
        row.status = "stopped"
        if event.error is not None:
            row.error = event.error
        self._free(event.index)

    def _on_worker_stats(self, event):
        worker = self._worker(event.worker)
        worker.ions_per_s = event.ions_per_s
        if is_number(event.ions_per_s):
            self.throughput.append(event.ions_per_s)

    def _on_paused(self, event):
        self.paused = event.paused

    def _on_scan_finished(self, event):
        self.finished = True
        self.stopped = event.stopped
        self.finished_at = event.time

    def _note_values(self, values):
        """Remember the value columns (``<name>_err`` columns are errors, not values)."""
        for name in values:
            if name.endswith("_err") and name[:-4] in values:
                continue
            if name not in self.value_names:
                self.value_names.append(name)

    def _reached(self, values) -> bool | None:
        """Whether a precision point's target is met by its estimate (None: no target, or no
        estimate yet)."""
        if self.precision is None:
            return None
        value = values.get(self.precision.target)
        err = values.get(f"{self.precision.target}_err")
        if not is_number(value) or not is_number(err):
            return None
        return bool(is_met(err, goal_of(self.precision, value)))

    # -- summaries ------------------------------------------------------------------------

    def counts(self) -> dict[str, int]:
        """Points per status (every status of :data:`STATUSES` is a key)."""
        counted = Counter(row.status for row in self.points.values())
        return {status: counted.get(status, 0) for status in STATUSES}

    def finished_points(self) -> int:
        """Points that are done with a result (ran or cached)."""
        counts = self.counts()
        return counts["done"] + counts["cached"]

    def total_ions(self) -> int:
        return sum(row.ions for row in self.points.values())

    def elapsed(self, now: float) -> float:
        """Seconds since the scan started (until it finished, when it has)."""
        if self.started_at is None:
            return 0.0
        end = self.finished_at if self.finished_at is not None else now
        return max(end - self.started_at, 0.0)

    def eta_seconds(self) -> float | None:
        """Estimated seconds left, or None while there is nothing to base it on.

        Fixed ion counts: the points still to run times the mean wall time of the points
        that ran, divided by the workers. Precision targets: the ions still expected (a
        point not done is expected at its planned ions, at least the ions of its first
        batches) times the seconds per ion of the points that ran, divided by the workers.
        """
        rows = list(self.points.values())
        pending = [row for row in rows if row.status in ("queued", "running")]
        if not pending:
            return 0.0
        ran = [row for row in rows if row.status == "done"]
        workers = max(self.n_workers, 1)
        if self.precision is None:
            if not ran:
                return None
            mean = sum(row.seconds for row in ran) / len(ran)
            return len(pending) * mean / workers
        seconds = sum(row.seconds for row in ran)
        ions = sum(row.ions for row in ran)
        if seconds <= 0 or ions <= 0:
            return None
        per_ion = seconds / ions
        left = sum(max(self._expected_ions(row) - row.ions, 0) for row in pending)
        return left * per_ion / workers

    def _expected_ions(self, row: PointRow) -> int:
        precision = self.precision
        floor = min(precision.max_ions, initial_batches(precision) * precision.batch_ions)
        return max(row.planned or 0, floor)

    def last_warnings(self, count: int = 5) -> list[tuple[str, str]]:
        """The most recent warnings and errors, newest last."""
        return list(self.warnings)[-count:]

    def failed_point(self) -> PointRow | None:
        """The point to open the log of when none is selected: the last one that failed."""
        if self.last_failed is None:
            return None
        return self.points.get(self.last_failed)

    def log_path(self, index: int) -> Path | None:
        """The ``error.log`` of a point (None before the scan folder is known)."""
        if self.scan_dir is None or index not in self.points:
            return None
        return self.scan_dir / "points" / self.points[index].id[:16] / "error.log"

    # -- the points table -------------------------------------------------------------------

    def table_columns(self) -> list[tuple[str, str]]:
        """``(column id, heading)`` of the points table, in display order."""
        columns = [("index", "#")]
        columns += [(f"param:{name}", name) for name in self.param_names]
        columns += [("status", "status"), ("ions", "ions")]
        columns += [(f"value:{name}", name) for name in self.value_names]
        columns.append(("reached", "target"))
        return columns

    def cell(self, row: PointRow, column: str) -> str:
        """The text of ``row`` in ``column`` (an id of :meth:`table_columns`)."""
        if column == "index":
            return str(row.index)
        if column == "status":
            return row.status
        if column == "ions":
            return str(row.ions)
        if column == "reached":
            return {True: "yes", False: "no"}.get(row.reached, "—")
        kind, _, name = column.partition(":")
        if kind == "param":
            value = row.params.get(name)
            return "—" if value is None else f"{value}"
        if kind == "value":
            return format_value(row.values.get(name), row.values.get(f"{name}_err"))
        raise KeyError(column)

    def sort_key(self, row: PointRow, column: str):
        """The key that orders ``row`` by ``column`` (numbers, then text, then missing)."""
        if column == "index":
            return _orderable(row.index)
        if column == "status":
            return _orderable(STATUSES.index(row.status))
        if column == "ions":
            return _orderable(row.ions)
        if column == "reached":
            return _orderable(None if row.reached is None else int(row.reached))
        kind, _, name = column.partition(":")
        if kind == "param":
            return _orderable(row.params.get(name))
        if kind == "value":
            return _orderable(row.values.get(name))
        raise KeyError(column)

    def sorted_rows(self, column: str = "index", reverse: bool = False) -> list[PointRow]:
        """The points ordered by ``column``, ties in scan order. Points without a value in
        the column come last, in either direction."""
        present, missing = [], []
        for row in self.points.values():
            key = self.sort_key(row, column)
            (missing if key[0] == 2 else present).append((key, row))
        present.sort(key=lambda pair: pair[0], reverse=reverse)
        return [row for _, row in present + missing]
