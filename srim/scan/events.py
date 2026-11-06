"""Events of a scan run by :func:`srim.scan.run_scan`.

Every event is a frozen dataclass with a ``kind`` (snake case, e.g. ``"point_finished"``),
a ``time`` (UTC seconds since the epoch, a float) and the fields below.
:meth:`to_dict` gives plain JSON-safe data (numpy values become Python numbers, NaN and
infinity become ``None``); :meth:`to_json` is the same as one JSON line.

Order of events of one scan::

    scan_started
    point_queued (one per point, in order)
    then, per point: point_started, point_progress, (point_failed, point_retried,
    point_started, ...)*, and one of point_finished, a final point_failed, or (if the
    scan was stopped) point_stopped
    worker_stats (after each finished point)
    point_stopped (for every unfinished point, once the workers are down)
    scan_finished (always last)

A unit of work taken from the result cache (see :mod:`srim.scan.cache`) gets ``point_cached``
instead of ``point_started`` and no worker runs it: a point whose units all come from the cache
has ``point_cached`` events, then ``point_finished`` with status ``cached``.

``point_progress`` is emitted at the start (``ions_done=0``), after each finished batch of a
precision target (``ions_done`` of the finished batches, ``ions_total`` of the batches
planned so far) and just before ``point_finished`` (``ions_done=ions_total``); live progress
inside a SRIM run comes later. ``batch_finished`` is emitted after each decision of a
precision target (all batches planned so far are done): the ions of the point so far and
``value_estimates``, ``{column: [value, err]}`` for its numeric columns.

A point that does not finish when the scan is stopped gets ``point_stopped`` (after all the
workers have stopped, before ``scan_finished``); its row has status ``stopped``.
``scan_finished.stopped`` says that the scan was stopped.
"""

import json
import math
import time as _time
from collections.abc import Mapping
from dataclasses import dataclass, field, fields
from pathlib import Path
from typing import ClassVar


def _plain(value):
    """``value`` with numpy values, paths and non-finite floats made JSON-safe."""
    if isinstance(value, Mapping):
        return {str(k): _plain(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_plain(v) for v in value]
    if isinstance(value, Path):
        return str(value)
    if hasattr(value, "tolist") and not isinstance(value, (str, bytes)):
        return _plain(value.tolist())  # numpy scalars and arrays, recursively
    if isinstance(value, float) and not math.isfinite(value):
        return None
    return value


class _Event:
    """Shared ``to_dict`` / ``to_json`` of the event dataclasses."""

    kind: ClassVar[str]

    def to_dict(self) -> dict:
        """The event as plain, JSON-safe data, with ``kind`` first"""
        data = {f.name: getattr(self, f.name) for f in fields(self)}
        return _plain({"kind": self.kind, **data})

    def to_json(self) -> str:
        """The event as one line of JSON"""
        return json.dumps(self.to_dict(), allow_nan=False, sort_keys=False)


def now() -> float:
    """Current time, UTC seconds since the epoch (what the ``time`` fields hold)"""
    return _time.time()


@dataclass(frozen=True)
class ScanStarted(_Event):
    """The scan starts. ``workers`` is the number of worker processes; ``base_seed`` the
    base seed the point seeds are derived from; ``scan_dir`` the scan folder."""

    kind: ClassVar[str] = "scan_started"
    n_points: int
    workers: int
    base_seed: int
    scan_dir: str
    time: float = field(default_factory=now)


@dataclass(frozen=True)
class PointQueued(_Event):
    """A point is waiting to run (``index`` in the scan, ``id`` its spec hash)."""

    kind: ClassVar[str] = "point_queued"
    index: int
    id: str
    time: float = field(default_factory=now)


@dataclass(frozen=True)
class PointStarted(_Event):
    """A point was handed to worker ``worker`` (attempt 1 is the first try)."""

    kind: ClassVar[str] = "point_started"
    index: int
    id: str
    worker: int
    attempt: int
    time: float = field(default_factory=now)


@dataclass(frozen=True)
class PointCached(_Event):
    """A unit of a point was taken from the result cache, so no worker ran it. ``batch`` is
    the batch of a precision target (0 for a point with a fixed ion count)."""

    kind: ClassVar[str] = "point_cached"
    index: int
    id: str
    batch: int
    time: float = field(default_factory=now)


@dataclass(frozen=True)
class PointProgress(_Event):
    """Ions done of a point out of ``ions_total`` (see the module docstring)."""

    kind: ClassVar[str] = "point_progress"
    index: int
    ions_done: int
    ions_total: int
    time: float = field(default_factory=now)


@dataclass(frozen=True)
class PointFinished(_Event):
    """A point is done: ``values`` are its extracted values, ``n_ions`` its ions,
    ``seconds`` the wall time of its SRIM run(s)."""

    kind: ClassVar[str] = "point_finished"
    index: int
    id: str
    values: dict
    n_ions: int
    seconds: float
    time: float = field(default_factory=now)


@dataclass(frozen=True)
class PointStopped(_Event):
    """A point did not finish because the scan was stopped. ``error`` is the one-line
    error of its last failed attempt, or ``None`` if it never failed."""

    kind: ClassVar[str] = "point_stopped"
    index: int
    id: str
    error: str | None = None
    time: float = field(default_factory=now)


@dataclass(frozen=True)
class PointFailed(_Event):
    """An attempt of a point failed with the one-line ``error``. ``final`` is False when
    the point is retried (see :class:`PointRetried`) and True when it gives up."""

    kind: ClassVar[str] = "point_failed"
    index: int
    id: str
    error: str
    attempt: int
    final: bool
    time: float = field(default_factory=now)


@dataclass(frozen=True)
class PointRetried(_Event):
    """A failed point is queued again; ``attempt`` is the attempt that will run next."""

    kind: ClassVar[str] = "point_retried"
    index: int
    id: str
    attempt: int
    time: float = field(default_factory=now)


@dataclass(frozen=True)
class BatchFinished(_Event):
    """A decision of a precision target: ``batch`` is the batch that finished last, ``ions``
    the ions of the point planned so far and ``value_estimates`` maps each numeric column to
    ``[value, err]`` (``err`` is NaN with fewer than two batches)."""

    kind: ClassVar[str] = "batch_finished"
    index: int
    batch: int
    ions: int
    value_estimates: dict
    time: float = field(default_factory=now)


@dataclass(frozen=True)
class WorkerStats(_Event):
    """Throughput of worker ``worker``: ``ions_per_s`` of its last point (``None`` if not
    known); ``cpu_percent`` is not measured yet and is ``None``."""

    kind: ClassVar[str] = "worker_stats"
    worker: int
    ions_per_s: float | None
    cpu_percent: float | None = None
    time: float = field(default_factory=now)


@dataclass(frozen=True)
class ScanFinished(_Event):
    """The scan ended: ``n_done`` points finished, ``n_failed`` failed for good, after
    ``seconds``; ``stopped`` is True if it was stopped (Ctrl-C or ``stop_event``)."""

    kind: ClassVar[str] = "scan_finished"
    n_done: int
    n_failed: int
    seconds: float
    stopped: bool
    time: float = field(default_factory=now)


#: every event class, by ``kind``
EVENT_TYPES = {
    cls.kind: cls
    for cls in (
        ScanStarted,
        PointQueued,
        PointStarted,
        PointCached,
        PointProgress,
        PointFinished,
        PointFailed,
        PointRetried,
        PointStopped,
        BatchFinished,
        WorkerStats,
        ScanFinished,
    )
}

__all__ = [
    "EVENT_TYPES",
    "BatchFinished",
    "PointCached",
    "PointFailed",
    "PointFinished",
    "PointProgress",
    "PointQueued",
    "PointRetried",
    "PointStarted",
    "PointStopped",
    "ScanFinished",
    "ScanStarted",
    "WorkerStats",
]
