"""Run a scan on this machine: every point in a worker process, with events and retries.

:func:`run_scan` runs the points of a :class:`~srim.scan.Scan` in a pool of worker processes.
Each worker creates one private run directory (:class:`srim.rundir.RunDir`) at its first
point and reuses it for all its points; the directory is removed when the worker exits.
The parent process hands out the work, runs the extractors on each finished outputs (so
extractors need not be picklable) and reports every step as an event
(:mod:`srim.scan.events`), always from the calling thread and in order.

Layout of a scan folder::

    scan.yaml                  the scan (base run spec and scan section), with the base seed
    points/<id[:16]>/          one point's outputs and run.yaml; error.log if an attempt failed
    points/<id[:16]>/batches/<b>/   the batches of a point with a precision target or batch_ions
    result/                    scan.csv and metadata.yaml (ScanResult.to_csv)
    cache/                     the result cache (unless cache=False or another folder is named)

Seeds: a point runs with ``derive_seed(base_seed, int(id[:15], 16))``, so a point keeps its
seed when points are added or reordered. Batch ``b`` of a point with batches
runs with ``derive_seed(point_seed, b)``. A failed unit (a point, or a batch) is retried
``retries`` times with the same seed, then the point gets status ``failed``; the scan goes on.

Binned beams (``beam.mode: binned``, :mod:`srim.beam.binned`): a binned point runs as one unit
per bin that gets ions. Its ions are planned in beam batches (one batch of all its ions, or
batches of ``batch_ions`` or ``precision.batch_ions``), and each beam batch splits its ions
over the bins by apportionment. A beam batch's values are the extractors on its units merged;
its errors and decisions are on the beam batches. Each unit runs
``bin_spec(point.spec, bin, ions, derive_seed(point_seed, unit))`` in ``batches/<unit>/``.

A batched point (binned, or with ``batch_ions`` or a precision target) records its units when it
finishes: ``run.yaml``, ``units.csv`` and (binned) ``beam_bins.csv``; see
:func:`srim.beam.open_results`, which merges them back.

Batches (:mod:`srim.scan.precision`): a point with a precision target runs as batches of
``batch_ions`` ions, and the unit of work is one batch. A fixed ion count with
``batch_ions`` (``Scan(batch_ions=)``) runs the same way, with all its batches planned at
once; the point is then done when all its batches are, and its values and ``<value>_err``
are the batch-means estimate of the merged batches. A decision is taken only when all
batches of the point planned so far are done, so the result does not depend on the timing
of the workers: the target's value and error are compared with the goal, and more batches
are planned or the point is done (``done``), or it reached ``max_ions`` (``max_ions``, with a
warning). ``batch_finished`` is emitted at each decision.

Pausing: while ``pause_event`` is set, no new unit is started; the running units finish, and
``scan_paused`` is emitted when the flag changes. A stop is still honoured while paused.

Stopping: a ``stop_event`` being set, or SIGINT or SIGTERM in the main thread, stops
scheduling. Running points are killed (SIGTERM to each worker's process group, which also
reaches SRIM; the worker then stops its wine server; SIGKILL after a grace period). Finished
points are kept; the others get status ``stopped`` (a precision point keeps the partial
estimate of its finished batches). POSIX only for the process groups; on Windows
``taskkill /T`` ends each worker and its children.
With ``backend="container"`` a stopped worker also removes the container it runs (see
:mod:`srim.scan.backends`).

Result cache (:mod:`srim.scan.cache`): before a unit runs, its key (the run spec and seed) is
looked up in the cache; a hit is linked into the unit's folder and no worker runs it
(``point_cached``). Otherwise the unit's key is locked (without waiting: a unit whose key
another process is computing waits, and is reused once that run is stored) and the run is
stored when it succeeds. Failed runs are never stored.

Scripts that run a scan must guard their entry point with ``if __name__ == "__main__":``:
the workers use the ``spawn`` start method on every platform, and spawn imports the script.
"""

import contextlib
import logging
import math
import multiprocessing
import os
import queue
import secrets
import shutil
import signal
import subprocess
import sys
import threading
import time
import traceback
import warnings
from collections import deque
from dataclasses import dataclass
from datetime import UTC, datetime
from multiprocessing.process import BaseProcess
from multiprocessing.queues import Queue
from numbers import Integral, Real
from pathlib import Path

import yaml

from .. import __version__
from .._lock import FileLock
from ..output import Results
from ..paths import srim_dir
from ..rundir import RunDir
from ..spec import RunSpec, SpecError
from .batches import (
    MAX_SEED,
    BatchInfo,
    MergedResults,
    derive_seed,
    write_point_record,
)
from .cache import RunCache, cache_key, cache_root, current_srim_sha, materialise
from .events import (
    BatchFinished,
    PointCached,
    PointFailed,
    PointFinished,
    PointProgress,
    PointQueued,
    PointRetried,
    PointStarted,
    PointStopped,
    ScanFinished,
    ScanPaused,
    ScanStarted,
    WorkerStats,
)
from .extract import combine
from .precision import (
    Precision,
    batch_error,
    batches_to_add,
    goal_of,
    initial_batches,
    is_met,
)
from .result import ScanResult
from .spec import Scan, is_binned

log = logging.getLogger(__name__)

#: seconds a worker gets to stop cleanly (after SIGTERM) before it is killed
GRACE_SECONDS = 10.0
#: seconds between two looks at the stop flag and the workers
_POLL_SECONDS = 0.1
_SIGKILL = getattr(signal, "SIGKILL", signal.SIGTERM)
#: columns the scan builds itself; an extractor may not produce them
_RESERVED = frozenset({"index", "id", "status", "error", "path", "n_ions", "seeds"})
#: the marker of a scan folder: ``pysrim2 scan clean --all`` removes only folders that have it
MARKER_NAME = ".pysrim2-scan"
#: the lock of a scan folder, held while a scan runs (and by ``pysrim2 scan clean``)
LOCK_NAME = ".scan.lock"
#: the scan file of a scan folder (the scan with its base seed)
SCAN_FILE = "scan.yaml"


class ScanBusy(RuntimeError):
    """Another scan is running in the scan folder (its lock is held)."""


def _refuse_foreign_folder(folder: Path) -> None:
    """Raise :class:`ValueError` for a folder with other files that is not a scan folder."""
    if not (folder / MARKER_NAME).is_file():
        others = [p.name for p in folder.iterdir() if p.name != LOCK_NAME]
        if others:
            raise ValueError(
                f"{folder} is not empty and not a scan folder (no {MARKER_NAME}); "
                "choose a new or empty folder"
            )


def mark_scan_folder(folder) -> None:
    """Write the marker file of a scan folder (one line, with the pysrim2 version).

    Only a new or empty folder, or one that already is a scan folder, becomes one: a
    folder with other files (a project, say) is refused with :class:`ValueError`, so that
    ``pysrim2 scan clean --all`` can never remove it.
    """
    folder = Path(folder)
    _refuse_foreign_folder(folder)
    text = f"pysrim2 scan folder, pysrim2 {__version__}\n"
    Path(folder, MARKER_NAME).write_text(text, encoding="utf-8")


def scan_lock(folder) -> FileLock:
    """The lock of a scan folder, not acquired yet. ``acquire()`` takes it without waiting
    and raises TimeoutError when another scan (or ``scan clean``) holds it."""
    return FileLock(Path(folder) / LOCK_NAME, timeout=0)


def same_scan_apart_from_seed(scan_file, scan) -> bool:
    """True when the ``scan.yaml`` at ``scan_file`` describes ``scan`` (base spec and scan
    section) apart from the base seed. A file that cannot be read is not the same scan."""
    try:
        document = yaml.safe_load(Path(scan_file).read_text(encoding="utf-8"))
        recorded = RunSpec.from_yaml(scan_file).to_dict()
    except Exception:
        return False
    section = document.get("scan") if isinstance(document, dict) else None
    if not isinstance(section, dict):
        return False
    section = {key: value for key, value in section.items() if key != "seed"}
    wanted = {key: value for key, value in scan.to_dict().items() if key != "seed"}
    return _plain_data({"spec": recorded, "scan": section}) == _plain_data(
        {"spec": scan.base.to_dict(), "scan": wanted}
    )


def _plain_data(data):
    """``data`` as the YAML round trip reads it (so that a file and a scan compare equal)."""
    return yaml.safe_load(yaml.safe_dump(data, sort_keys=True))


def _reusable_seed(scan_file, scan) -> int | None:
    """The base seed recorded in the ``scan.yaml`` of a reused folder, when the scan
    describes ``scan`` apart from its seed (and ``scan`` has no seed of its own)."""
    if scan.seed is not None or not Path(scan_file).is_file():
        return None
    if not same_scan_apart_from_seed(scan_file, scan):
        return None
    seed = yaml.safe_load(Path(scan_file).read_text(encoding="utf-8"))["scan"].get("seed")
    if isinstance(seed, bool) or not isinstance(seed, Integral) or seed < 0:
        return None
    return int(seed)


def run_scan(
    scan,
    *,
    workers=None,
    backend="local",
    scan_dir=None,
    on_event=None,
    stop_event=None,
    pause_event=None,
    runner=None,
    cache=None,
    retries=1,
    image=None,
    runtime=None,
) -> ScanResult:
    """Run every point of ``scan`` and return the :class:`~srim.scan.ScanResult`.

    Parameters
    ----------
    scan : :class:`~srim.scan.Scan`
        the scan to run. Python extractors run in this process (they need not be picklable)
        and are written to ``scan.yaml`` as ``python:<module>:<name>`` references. With a
        :class:`~srim.scan.Precision` ``ions``, each point runs in batches until its target
        is reached (see the module docstring).
    workers : :obj:`int`, optional
        worker processes; default ``max(1, os.cpu_count() - 1)``, never more than the units
        of work the scan can have (points, or batches for precision targets).
    backend : :obj:`str`
        ``"local"`` (the default) runs on this machine. ``"container"`` runs each unit in its
        own container of the pysrim2 image (``pysrim2 run-job``, see
        :mod:`srim.scan.backends`); the events and the result are the same. Slurm is not a
        back end of this function: see :func:`srim.scan.prepare_slurm`.
    scan_dir : path-like, optional
        the scan folder (made if missing). Default: a new ``./scan-<UTC time>`` folder. An
        existing folder is reused: ``scan.yaml`` is overwritten and each point's folder
        ``points/<id[:16]>`` is replaced by the new run.
    on_event : callable, optional
        ``on_event(event)`` for every event (see :mod:`srim.scan.events`). Called in this
        process, from one thread, in order. An exception in it is logged as a warning and
        does not stop the scan.
    stop_event : :class:`threading.Event`, optional
        set it to stop the scan (see the module docstring)
    pause_event : :class:`threading.Event`, optional
        while it is set, no new unit starts (running ones finish); clear it to go on. Each
        change is reported as ``scan_paused``.
    runner : callable, optional
        ``runner(spec) -> Results`` (a picklable top-level function, as for
        :func:`srim.scan.run_batches`) in place of SRIM. Its results folder is copied to
        the point's (or batch's) folder and the runner owns its run directory. For tests and
        custom back ends. Default: ``TRIM.from_spec(spec).run(...)`` in a private run
        directory. Not allowed with ``backend="container"``.
    cache : optional
        the result cache (see :mod:`srim.scan.cache`). ``None`` (the default): the folder
        named by ``$PYSRIM2_CACHE``, else ``<scan_dir>/cache``. ``False``: no cache. A path:
        that folder (it must be empty or a pysrim2 cache). Runs found in the cache are not
        computed again; a rerun of a finished scan therefore computes nothing.
    retries : :obj:`int`
        how often a failed unit (a point, or a batch) is run again before it is reported as
        failed (default 1)
    image : :obj:`str`, optional
        container image for ``backend="container"``. Default: ``$PYSRIM2_IMAGE``, else the
        image of this pysrim2 release (``srim.installer.container.image_ref``).
    runtime : :obj:`str`, optional
        ``"podman"`` or ``"docker"`` for ``backend="container"``. Default: the first one
        found (:func:`srim.installer.container.find_runtime`).

    Returns
    -------
    :class:`~srim.scan.ScanResult`
        one row per point, also written to ``<scan_dir>/result/``. Its ``scan`` has the
        base seed filled in when the scan had none.

    Raises
    ------
    ScanBusy
        when another scan is running in ``scan_dir`` (nothing is started or written then)
    ValueError
        for an unknown ``backend``, or ``runner=`` with the container back end
    ContainerError
        for ``backend="container"`` when no container runtime is found (before any worker
        starts)
    FileNotFoundError
        when there is no SRIM install and no ``runner`` is given (local back end)
    """
    if backend not in ("local", "container"):
        raise ValueError(f"backend must be 'local' or 'container', got {backend!r}")
    workers = _worker_count(workers)
    retries = _retry_count(retries)
    if backend == "container":
        if runner is not None:
            raise ValueError("runner= is for the local back end; the container runs pysrim2")
        runner = _container_runner(image, runtime)
    elif runner is None:
        _require_srim()
        _prune_old_runs()
    folder = _new_folder(scan_dir)
    _refuse_foreign_folder(folder)  # before any lock file is left in a foreign folder
    try:
        lock = scan_lock(folder).acquire()
    except TimeoutError:
        raise ScanBusy(f"a scan is already running in {folder}") from None
    try:
        mark_scan_folder(folder)
        # a rerun in the same folder keeps the base seed of the scan it describes, so the
        # points are the same runs and the cache serves them
        base_seed = scan.seed
        if base_seed is None:
            base_seed = _reusable_seed(folder / SCAN_FILE, scan)
        if base_seed is None:
            base_seed = secrets.randbelow(MAX_SEED) + 1
        recorded = (
            scan
            if scan.seed == base_seed
            else Scan(
                scan.base,
                grid=scan.grid,
                points=scan.rows,
                extract=scan.extract,
                ions=scan.ions,
                seed=base_seed,
                batch_ions=scan.batch_ions,
            )
        )
        store = _open_cache(cache, folder)
        recorded.to_yaml(folder / "scan.yaml", provenance={"pysrim2_version": __version__})
        run = _ScanRun(
            recorded,
            folder,
            workers=workers,
            base_seed=base_seed,
            runner=runner,
            retries=retries,
            on_event=on_event,
            stop=stop_event if stop_event is not None else threading.Event(),
            pause=pause_event,
            cache=store,
        )
        with _stop_on_signals(run.stop):
            return run.execute()
    finally:
        lock.release()


# -- worker processes ----------------------------------------------------------------------

#: state of the worker process itself: its one reusable run directory, and the callback
#: that stops the unit it runs (see :func:`set_unit_abort`)
_WORKER: dict = {"run_dir": None, "abort": None}


def set_unit_abort(callback):
    """Register ``callback()`` for the unit that this worker runs now (``None``: none).

    A runner that starts something outside the worker (a container) registers a callback
    that stops it. The worker calls it once, in its main thread from the SIGTERM handler (or
    from its parent-watch thread when the parent is gone), then exits. Keep it short and
    bounded: it may run a subprocess, but must not wait for long.
    """
    _WORKER["abort"] = callback


def _abort_unit():
    callback, _WORKER["abort"] = _WORKER["abort"], None
    if callback is not None:
        with contextlib.suppress(Exception):
            callback()


def _outputs_to_stderr():
    """A worker's stdout goes to stderr: stdout belongs to the scan's view (``--ui json``
    writes only events there). Children such as SRIM and wine inherit file descriptor 1,
    so that is redirected too."""
    with contextlib.suppress(OSError):
        os.dup2(2, 1)
    sys.stdout = sys.stderr


def _worker_main(ident, tasks, results, runner, parent_pid):
    """Worker process: run the units it is handed until it gets ``None``.

    A unit is ``(index, batch, attempt, spec, out)``. Reports ``("done", ident, index,
    batch, attempt, seconds)`` or ``("error", ident, index, batch, attempt, summary,
    traceback)`` for each unit. The worker leads its own process group so that the parent
    can stop it and everything it started (SRIM) at once.
    """
    _outputs_to_stderr()
    if hasattr(os, "setpgid"):
        with contextlib.suppress(OSError):
            os.setpgid(0, 0)
        threading.Thread(target=_watch_parent, args=(parent_pid,), daemon=True).start()
    signal.signal(signal.SIGINT, signal.SIG_IGN)  # the parent decides when to stop
    signal.signal(signal.SIGTERM, _terminate_worker)
    try:
        while True:
            try:
                task = tasks.get(timeout=1.0)
            except queue.Empty:
                if os.getppid() != parent_pid:  # the parent is gone: nothing to wait for
                    return
                continue
            if task is None:
                return
            index, batch, attempt, spec, out = task
            began = time.monotonic()
            try:
                _run_point(RunSpec.from_dict(spec), out, runner)
            except BaseException as exc:  # reported; the parent decides on a retry
                results.put(
                    (
                        "error",
                        ident,
                        index,
                        batch,
                        attempt,
                        _summary(exc),
                        traceback.format_exc(),
                    )
                )
            else:
                results.put(("done", ident, index, batch, attempt, time.monotonic() - began))
    finally:
        _release_run_dir()


def _release_run_dir():
    """Stop this worker's wine server and remove its run directory (once)."""
    run_dir, _WORKER["run_dir"] = _WORKER["run_dir"], None
    if run_dir is not None:
        with contextlib.suppress(Exception):
            run_dir.cleanup()


def _terminate_worker(signum, frame):
    """SIGTERM in a worker: stop the unit's outside process (if any), release the run
    directory and exit.

    The parent signals the whole process group, so SRIM stops at the same time.
    """
    _abort_unit()
    _release_run_dir()
    os._exit(1)


#: how often a worker checks that its parent is still alive (POSIX)
_PARENT_CHECK_SECONDS = 1.0


def _watch_parent(parent_pid):
    """Daemon thread of each worker: when the parent is gone, nothing may keep running.

    The worker releases its run directory and kills its process group (its children
    included), so a parent killed with SIGKILL leaves no SRIM or wine behind.
    """
    while True:
        time.sleep(_PARENT_CHECK_SECONDS)
        if os.getppid() != parent_pid:
            _abort_unit()
            _release_run_dir()
            if os.getpgid(0) == os.getpid():  # our own group only, never the parent's
                with contextlib.suppress(OSError):
                    os.killpg(os.getpid(), signal.SIGKILL)
            os._exit(1)


def _run_point(spec, out, runner):
    """Run one point (or batch) and leave its outputs in ``out``."""
    if runner is not None:
        results = runner(spec)
        try:
            shutil.copytree(results.directory, out, dirs_exist_ok=True)
        finally:
            results.close()
        return
    from ..srim import TRIM

    if _WORKER["run_dir"] is None:
        _WORKER["run_dir"] = RunDir.create()
    TRIM.from_spec(spec).run(run_dir=_WORKER["run_dir"], output_dir=out).close()


# -- the parent's bookkeeping --------------------------------------------------------------


@dataclass
class _Worker:
    """One worker process as the parent sees it. ``current`` is ``(index, batch, attempt,
    started)`` of the unit it runs, or ``None`` when it is idle."""

    ident: int
    process: BaseProcess
    tasks: Queue
    #: the worker's process group (POSIX), recorded at start: it outlives the worker
    pgid: int | None = None
    current: tuple | None = None

    @property
    def busy(self) -> bool:
        return self.current is not None


class _ScanRun:
    """The state of one :func:`run_scan` call. Used from the calling thread only.

    A unit of work is ``(index, batch, attempt)``: a whole point for a fixed ion count
    (batch 0 of its folder ``points/<id>``), or one batch of a point with a precision target
    (folder ``points/<id>/batches/<batch>``). ``plan[index]`` holds the ions of each batch
    planned so far, ``finished[index]`` maps the finished batches to their wall time.
    """

    def __init__(
        self,
        scan,
        folder,
        *,
        workers,
        base_seed,
        runner,
        retries,
        on_event,
        stop,
        pause=None,
        cache=None,
    ):
        self.scan = scan
        self.folder = folder
        self.points = scan.points()
        self.precision = scan.ions if isinstance(scan.ions, Precision) else None
        self.base_seed = base_seed
        self.runner = runner
        self.retries = retries
        self.on_event = on_event
        self.stop = stop
        self.pause = pause
        self.paused = False  # the pause flag as last reported (see _update_pause)
        self.cache = cache
        self.srim_sha = current_srim_sha() if cache is not None else None
        #: (index, batch) -> (key, lock) of the units this run computes (cache in use)
        self.locks: dict[tuple[int, int], tuple[str, object]] = {}
        #: units whose key another process is computing (they wait, without a worker)
        self.waiting: list[tuple] = []
        #: points with at least one unit computed by a worker (the others may be cached)
        self.ran: set[int] = set()
        self.extract = combine(scan.extract)
        self.names = set(scan.param_names) | _RESERVED
        self.seeds = [derive_seed(base_seed, int(p.id[:15], 16)) for p in self.points]
        self.specs = [
            p.spec.with_overrides([f"seed={seed}"])
            for p, seed in zip(self.points, self.seeds, strict=True)
        ]
        self.dirs = [folder / "points" / p.id[:16] for p in self.points]
        self.plan: dict[int, list[int]] = {p.index: [] for p in self.points}
        self.finished: dict[int, dict[int, float]] = {p.index: {} for p in self.points}
        self.batch_values: dict[tuple[int, int], dict] = {}
        self.batch_ions = scan.batch_ions
        #: binned points: point index -> the bins of its beam (srim.beam.binned.BeamBin)
        self.bins: dict[int, list] = {}
        for p in self.points:
            if is_binned(p.spec):
                from ..beam.binned import make_bins  # lazy: srim.beam imports srim.scan

                self.bins[p.index] = make_bins(p.spec)
        #: the units of a binned point: unit -> (beam batch, bin index); beam batch -> units
        self.units: dict[int, list[tuple[int, int]]] = {p.index: [] for p in self.points}
        self.beam_units: dict[int, list[list[int]]] = {p.index: [] for p in self.points}
        self.pending: deque = deque()
        self.rows: dict[int, dict] = {}
        self.workers: dict[int, _Worker] = {}
        self.started: set[int] = set()
        self.stopping = False
        self.n_done = 0
        self.n_failed = 0
        self.last_error: dict[int, str] = {}
        self.deaths = 0
        capacity = 0
        for p in self.points:  # a point's units are planned in point order
            index = p.index
            if not self._batched(index):
                self.pending.append((index, 0, 1))
                capacity += 1
            elif self.precision is None:
                self._plan_fixed(index)
                capacity += len(self.plan[index])
            else:
                self._schedule(index, initial_batches(self.precision))
                per_point = math.ceil(self.precision.max_ions / self.precision.batch_ions)
                capacity += per_point * (len(self.bins[index]) if self._binned(index) else 1)
        self.n_workers = min(workers, capacity)
        self.ctx = multiprocessing.get_context("spawn")
        self.results = self.ctx.Queue()

    # -- the whole run -----------------------------------------------------------------

    def execute(self) -> ScanResult:
        started = time.monotonic()
        self._emit(ScanStarted(len(self.points), self.n_workers, self.base_seed, str(self.folder)))
        for point in self.points:
            self._emit(PointQueued(point.index, point.id))
        for folder in self.dirs:  # a point's folder is replaced by its new run
            shutil.rmtree(folder, ignore_errors=True)
        try:
            if self.stop.is_set():  # stopped before the start: no worker is started
                self._halt()
            else:
                for ident in range(self.n_workers):
                    self.workers[ident] = self._spawn(ident)
                self._loop()
        finally:
            self._shutdown()
            self._release_all()
        self._settle_unfinished()
        rows = [self.rows[point.index] for point in self.points]
        result = ScanResult.from_rows(self.scan, rows, base_dir=self.folder)
        result.to_csv(self.folder / "result")
        self._emit(
            ScanFinished(
                n_done=self.n_done,
                n_failed=self.n_failed,
                seconds=time.monotonic() - started,
                stopped=self.stopping,
            )
        )
        return result

    def _loop(self):
        while True:
            if self.stop.is_set():
                self._halt()
                return
            self._update_pause()
            self._restart_dead()
            self._poll_waiting()
            self._dispatch()
            if not self._busy() and not self.pending and not self.waiting:
                return
            self._handle(self._next(_POLL_SECONDS))
            self._reap()

    # -- workers -----------------------------------------------------------------------

    def _spawn(self, ident):
        tasks = self.ctx.Queue()
        process = self.ctx.Process(
            target=_worker_main,
            args=(ident, tasks, self.results, self.runner, os.getpid()),
            name=f"pysrim2-scan-worker-{ident}",
            daemon=True,
        )
        process.start()
        pgid = None
        if hasattr(os, "setpgid"):
            pgid = process.pid
            # the worker makes itself a group leader too; whichever runs first wins
            with contextlib.suppress(OSError):
                os.setpgid(process.pid, process.pid)
        return _Worker(ident, process, tasks, pgid)

    def _busy(self):
        return [w for w in self.workers.values() if w.busy]

    def _restart_dead(self):
        """Replace idle workers that died while there is still work to hand out."""
        if self.stopping or not self.pending:
            return
        for ident, worker in list(self.workers.items()):
            if not worker.busy and not worker.process.is_alive():
                _close_queue(worker.tasks)
                self.workers[ident] = self._spawn(ident)

    def _dispatch(self):
        """Hand the pending units out: cache hits finish at once, misses go to idle workers."""
        while self.pending and not self.stop.is_set() and not self._pause_requested():
            unit = self.pending.popleft()
            verdict = self._admit(unit)
            if verdict == "hit":
                continue
            if verdict == "wait":
                self.waiting.append(unit)
                continue
            worker = self._idle_worker()
            if worker is None:
                self.pending.appendleft(unit)
                return
            self._send(worker, *unit)

    def _pause_requested(self) -> bool:
        return self.pause is not None and self.pause.is_set()

    def _update_pause(self):
        """Report a change of the pause flag (``scan_paused``); the dispatch checks the flag
        itself, so a pause set by an event handler holds from the next unit on."""
        paused = self._pause_requested()
        if paused != self.paused:
            self.paused = paused
            self._emit(ScanPaused(paused))

    def _idle_worker(self):
        for worker in self.workers.values():
            if not worker.busy and worker.process.is_alive():
                return worker
        return None

    def _send(self, worker, index, batch, attempt):
        point = self.points[index]
        out = self._unit_dir(index, batch)
        self.ran.add(index)
        if attempt == 1:  # the unit's folder starts clean (a point's folder was cleared)
            shutil.rmtree(out, ignore_errors=True)
        out.mkdir(parents=True, exist_ok=True)
        worker.current = (index, batch, attempt, time.monotonic())
        spec = self._unit_spec(index, batch)
        worker.tasks.put((index, batch, attempt, spec.to_dict(inline=True), str(out)))
        if attempt > 1 or index not in self.started:
            self.started.add(index)
            self._emit(PointStarted(index, point.id, worker.ident, attempt))
        self._emit(PointProgress(index, self._done_ions(index), self._planned_ions(index)))

    def _next(self, timeout):
        try:
            return self.results.get(timeout=timeout)
        except queue.Empty:
            return None

    def _drain(self, timeout):
        """Handle the messages that arrive within ``timeout`` (a dead worker's last words)."""
        while (message := self._next(timeout)) is not None:
            self._handle(message)

    def _handle(self, message):
        if message is None:
            return
        kind, ident, index, batch, attempt = message[:5]
        worker = self.workers.get(ident)
        unit = (index, batch, attempt)
        if worker is None or worker.current is None or worker.current[:3] != unit:
            return  # stale: the worker was already stopped or replaced
        worker.current = None
        if kind == "done":
            self._store(index, batch)  # a finished run is kept, even if its point settled
            if index in self.rows:  # the point is settled (failed): its other batches are moot
                return
            self._complete(worker, index, batch, attempt, message[5])
        elif index in self.rows or self.stopping:  # no retry: the unit is over
            self._retire(index, batch)
        else:
            self._failed(index, batch, attempt, message[5], message[6])

    def _complete(self, worker, index, batch, attempt, seconds):
        """A unit's outputs are in its folder (run by ``worker``, or taken from the cache)."""
        if not self._batched(index):
            self._finished(worker, index, attempt, seconds)
        else:
            self._batch_finished(worker, index, batch, attempt, seconds)

    def _reap(self):
        """Attempts whose worker died without a word count as failed.

        Workers that keep dying (more than twice the number of workers) stop the scan: a
        crash that repeats is not for retries to fix.
        """
        for worker in self._busy():
            if worker.process.is_alive():
                continue
            self._drain(0.5)
            if worker.busy:
                index, batch, attempt, _ = worker.current
                worker.current = None
                code = worker.process.exitcode
                self.deaths += 1
                if self.deaths > 2 * self.n_workers:
                    raise RuntimeError(
                        f"worker processes died {self.deaths} times before finishing a point "
                        f"(last exit code {code}); the scan is stopped. See the error.log "
                        "files of the points."
                    )
                self._failed(
                    index,
                    batch,
                    attempt,
                    f"worker process exited unexpectedly (exit code {code})",
                    "",
                )

    def _halt(self):
        """Stop: kill the running units, keep what finished, drop the rest."""
        self.stopping = True
        busy = self._busy()
        for worker in busy:
            _signal_worker(worker, signal.SIGTERM)
        deadline = time.monotonic() + GRACE_SECONDS
        while any(w.busy and w.process.is_alive() for w in busy):
            if time.monotonic() >= deadline:
                break
            self._handle(self._next(_POLL_SECONDS))
        for worker in busy:
            if worker.busy:
                _signal_worker(worker, _SIGKILL)
                worker.current = None
        self.pending.clear()
        self.waiting = []
        self._release_idle()

    def _shutdown(self):
        """Stop every worker process; the ones still running a unit are killed."""
        for worker in self._busy():
            _signal_worker(worker, signal.SIGTERM)
        for worker in self.workers.values():
            if not worker.busy and worker.process.is_alive():
                with contextlib.suppress(Exception):
                    worker.tasks.put(None)
        for worker in self.workers.values():
            worker.process.join(timeout=GRACE_SECONDS)
            if worker.process.is_alive():
                _signal_worker(worker, signal.SIGTERM)
                worker.process.join(timeout=5.0)
            if worker.process.is_alive():
                _signal_worker(worker, _SIGKILL)
                worker.process.join(timeout=5.0)
            _close_queue(worker.tasks)
        _close_queue(self.results)

    # -- units of work ----------------------------------------------------------------

    def _batched(self, index) -> bool:
        """A point runs as units of batches: a binned beam, a precision target, or batch_ions."""
        return index in self.bins or self.precision is not None or self.batch_ions is not None

    def _binned(self, index) -> bool:
        """The units of this point are the bins of its beam (see :meth:`_add_beam`)."""
        return index in self.bins

    def _plan_fixed(self, index):
        """Plan all units of a fixed-count point (their count is known, so none is added
        later): the batches of ``batch_ions``, or for a binned beam one beam batch per batch of
        ions (a single beam batch without ``batch_ions``)."""
        total = self.points[index].spec.number_ions
        if self._binned(index):
            size = total if self.batch_ions is None else self.batch_ions
            for ions in fixed_batch_sizes(total, size):
                self._add_beam(index, ions)
            return
        for batch, size in enumerate(fixed_batch_sizes(total, self.batch_ions)):
            self.plan[index].append(size)
            self.pending.append((index, batch, 1))

    def _add_beam(self, index, size):
        """Plan one beam batch of ``size`` ions of a binned point and queue its units.

        The ions of the batch are split over the bins by Webster apportionment of the ions
        planned so far (:func:`srim.beam.binned.beam_batch_allocation`): a bin with no ion gets
        no unit. Each unit is one bin's run; ``plan`` holds the ions of every unit.
        """
        from ..beam.binned import beam_batch_allocation  # lazy: srim.beam imports srim.scan

        beam = len(self.beam_units[index])
        probabilities = [b.probability for b in self.bins[index]]
        counts = beam_batch_allocation(probabilities, sum(self.plan[index]), size)
        units = []
        for k, ions in enumerate(int(n) for n in counts):
            if ions > 0:
                unit = len(self.plan[index])
                self.plan[index].append(ions)
                self.units[index].append((beam, k))
                self.pending.append((index, unit, 1))
                units.append(unit)
        self.beam_units[index].append(units)

    def _schedule(self, index, count):
        """Plan up to ``count`` more batches of a precision point (never beyond
        ``max_ions``) and queue them. Each batch has the ions the maximum still allows.
        A binned point gets whole beam batches (their units are queued with them)."""
        precision = self.precision
        for _ in range(count):
            planned = sum(self.plan[index])
            if planned >= precision.max_ions:
                return
            size = min(precision.batch_ions, precision.max_ions - planned)
            if self._binned(index):
                self._add_beam(index, size)
                continue
            batch = len(self.plan[index])
            self.plan[index].append(size)
            self.pending.append((index, batch, 1))

    def _unit_dir(self, index, batch):
        if not self._batched(index):
            return self.dirs[index]
        return self.dirs[index] / "batches" / str(batch)

    def _unit_spec(self, index, batch):
        if not self._batched(index):
            return self.specs[index]
        seed = derive_seed(self.seeds[index], batch)
        ions = self.plan[index][batch]
        if self._binned(index):
            from ..beam.binned import bin_spec  # lazy: srim.beam imports srim.scan

            _beam, k = self.units[index][batch]
            return bin_spec(self.points[index].spec, self.bins[index][k], ions, seed)
        return self.points[index].spec.with_overrides([f"number_ions={ions}", f"seed={seed}"])

    def _planned_ions(self, index):
        if not self._batched(index):
            return self.points[index].spec.number_ions
        return sum(self.plan[index])

    def _beam_units(self, index, beam) -> list[int]:
        """The units of a beam batch (a plain batch of a point is its own beam)."""
        if self._binned(index):
            return self.beam_units[index][beam]
        return [beam]

    def _units_of(self, index, beams) -> list[int]:
        return [unit for beam in beams for unit in self._beam_units(index, beam)]

    def _beam_ions(self, index, beam) -> int:
        return sum(self.plan[index][unit] for unit in self._beam_units(index, beam))

    def _beam_done(self, index, beam) -> bool:
        return all(unit in self.finished[index] for unit in self._beam_units(index, beam))

    def _done_beams(self, index) -> list[int]:
        """The beam batches whose units are all finished (for a plain batch, the finished
        batches), in order."""
        if not self._binned(index):
            return sorted(self.finished[index])
        return [b for b in range(len(self.beam_units[index])) if self._beam_done(index, b)]

    def _done_units(self, index) -> list[int]:
        """The units whose outputs are kept: those of the finished beam batches."""
        return self._units_of(index, self._done_beams(index))

    def _columns(self, index, unit):
        """The per-ion columns of a binned unit (see :class:`srim.scan.BatchInfo`)."""
        if not self._binned(index):
            return None
        beam, k = self.units[index][unit]
        bin_ = self.bins[index][k]
        return {
            "beam_batch": beam,
            "bin": k,
            "bin_energy_keV": bin_.energy_keV,
            "bin_angle_deg": bin_.angle_deg,
        }

    def _infos(self, index, units):
        return [
            BatchInfo(
                unit,
                derive_seed(self.seeds[index], unit),
                self.plan[index][unit],
                directory=str(self._unit_dir(index, unit)),
                columns=self._columns(index, unit),
            )
            for unit in units
        ]

    def _done_ions(self, index):
        """Ions of the finished batches of a precision point (0 for a fixed count)."""
        return sum(self.plan[index][b] for b in self.finished[index])

    def _row_seeds(self, index):
        """The seeds column: the point's seed, or the seeds of the finished batches."""
        if not self._batched(index):
            return str(self.seeds[index])
        done = self._done_units(index)
        return ",".join(str(derive_seed(self.seeds[index], b)) for b in done) or None

    # -- the result cache --------------------------------------------------------------

    def _key(self, index, batch):
        spec = self._unit_spec(index, batch)
        return cache_key(spec, spec.seed)

    def _status(self, index, status):
        """``cached`` for a point that finished (``done``) with none of its units run by a
        worker; any other outcome (``max_ions``) keeps its status."""
        return "cached" if status == "done" and index not in self.ran else status

    def _admit(self, unit):
        """Decide how a unit runs: ``"hit"`` (taken from the cache and completed now),
        ``"run"`` (a worker runs it; its key is locked by this run) or ``"wait"`` (another
        process holds the key: the unit is tried again later, without a worker)."""
        index, batch, _attempt = unit
        if self.cache is None or (index, batch) in self.locks:
            return "run"
        key = self._key(index, batch)
        if self._hit(index, batch, key):
            return "hit"
        lock = self.cache.try_lock(key)
        if lock is None:
            return "wait"
        # the other process may have stored the run while we were getting the lock
        if self._hit(index, batch, key):
            lock.release()
            return "hit"
        self.locks[(index, batch)] = (key, lock)
        return "run"

    def _hit(self, index, batch, key) -> bool:
        """Use the cached run of ``key`` for the unit, if there is one (and it can be linked)."""
        entry = self.cache.lookup(key, self.srim_sha)
        if entry is None:
            return False
        try:
            shutil.rmtree(self._unit_dir(index, batch), ignore_errors=True)
            materialise(entry, self._unit_dir(index, batch))
        except OSError as exc:
            log.warning(
                "scan point %d: cached run %s cannot be used (%s); it is computed again",
                index,
                key[:12],
                exc,
            )
            return False
        self._emit(PointCached(index, self.points[index].id, batch))
        self._complete(None, index, batch, 1, 0.0)
        return True

    def _poll_waiting(self):
        """Units that waited for another process's key: taken when it is stored or free."""
        waiting, self.waiting = self.waiting, []
        for unit in waiting:
            if unit[0] in self.rows:
                continue
            verdict = self._admit(unit)
            if verdict == "wait":
                self.waiting.append(unit)
            elif verdict == "run":
                self.pending.append(unit)

    def _store(self, index, batch):
        """A unit's run succeeded: store it under its key and release the key."""
        held = self.locks.pop((index, batch), None)
        if held is None:
            return
        key, lock = held
        try:
            self.cache.store(key, self._unit_dir(index, batch))
        except OSError as exc:
            log.warning("scan point %d: the run could not be cached (%s)", index, exc)
        finally:
            lock.release()

    def _retire(self, index, batch):
        """A unit will not run again: release its key."""
        held = self.locks.pop((index, batch), None)
        if held is not None:
            held[1].release()

    def _release_idle(self, index=None):
        """Release the keys of the units (of ``index``, or all) that no worker is running."""
        running = {w.current[:2] for w in self.workers.values() if w.current is not None}
        for unit in list(self.locks):
            if (index is None or unit[0] == index) and unit not in running:
                self._retire(*unit)

    def _release_all(self):
        """Release every key (the workers are down)."""
        for unit in list(self.locks):
            self._retire(*unit)

    # -- outcomes of one attempt -------------------------------------------------------

    def _finished(self, worker, index, attempt, seconds):
        """A fixed-count point's run is done: extract its values and record the row.

        ``worker`` is None for a run taken from the cache."""
        point = self.points[index]
        out = self.dirs[index]
        n_ions = point.spec.number_ions
        self._emit(PointProgress(index, n_ions, n_ions))
        try:
            with Results(str(out)) as results:
                results.spec = self._unit_spec(index, 0).to_dict()  # ions_in_layer reads it
                values = dict(self.extract(results, point))
            self._check_names(values)
        except Exception as exc:
            summary = _summary(exc)
            self._log_error(index, attempt, summary, traceback.format_exc())
            # the outputs exist, so the ions were run: the requested count is kept
            self._give_up(index, attempt, summary, n_ions=n_ions)
            return
        self.rows[index] = {
            "index": index,
            "status": self._status(index, "done"),
            "n_ions": n_ions,
            "seeds": str(self.seeds[index]),
            "path": str(out),
            "error": None,
            **values,
        }
        self.n_done += 1
        self._emit(PointFinished(index, point.id, values, n_ions, seconds))
        if worker is not None:
            ions_per_s = n_ions / seconds if seconds > 0 else None
            self._emit(WorkerStats(worker.ident, ions_per_s))

    def _batch_finished(self, worker, index, batch, attempt, seconds):
        """One batch of a precision point is done: extract its values, and when all the
        batches planned so far are done, take the decision for the point."""
        if self._binned(index):
            return self._unit_finished(worker, index, batch, attempt, seconds)
        point = self.points[index]
        size = self.plan[index][batch]
        try:
            with Results(str(self._unit_dir(index, batch))) as results:
                results.spec = self._unit_spec(index, batch).to_dict()
                values = dict(self.extract(results, point))
            self._check_names(values)
        except Exception as exc:
            summary = _summary(exc)
            self._log_error(index, attempt, summary, traceback.format_exc())
            self._give_up(index, attempt, summary, n_ions=self._done_ions(index) or None)
            return
        self.batch_values[(index, batch)] = values
        self.finished[index][batch] = seconds
        self._emit(PointProgress(index, self._done_ions(index), self._planned_ions(index)))
        if worker is not None:
            self._emit(WorkerStats(worker.ident, size / seconds if seconds > 0 else None))
        if len(self.finished[index]) == len(self.plan[index]):
            if self.precision is None:
                self._finish_batched(index, attempt)
            else:
                self._decide(index, batch, attempt)

    def _unit_finished(self, worker, index, unit, attempt, seconds):
        """One unit of a binned point is done. When all units of its beam batch are done,
        the beam batch's values are the extractors on those units merged. When all planned
        units are done, the point is finished (fixed count) or decided (precision), on the
        beam batches."""
        beam = self.units[index][unit][0]
        size = self.plan[index][unit]
        self.finished[index][unit] = seconds
        self._emit(PointProgress(index, self._done_ions(index), self._planned_ions(index)))
        if worker is not None:
            self._emit(WorkerStats(worker.ident, size / seconds if seconds > 0 else None))
        if self._beam_done(index, beam):
            try:
                values = self._merged_values(index, self._beam_units(index, beam))
            except Exception as exc:
                summary = _summary(exc)
                self._log_error(index, attempt, summary, traceback.format_exc())
                self._give_up(index, attempt, summary, n_ions=self._done_ions(index) or None)
                return
            self.batch_values[(index, beam)] = values
        if len(self.finished[index]) == len(self.plan[index]):
            if self.precision is None:
                self._finish_batched(index, attempt)
            else:
                self._decide(index, beam, attempt)

    def _finish_batched(self, index, attempt):
        """All batches of a fixed-count point are done: its values are the estimate of the
        merged batches, and the point is ``done`` (no batch is added)."""
        try:
            values, errs = self._estimate(index, self._done_beams(index))
        except Exception as exc:
            summary = _summary(exc)
            self._log_error(index, attempt, summary, traceback.format_exc())
            self._give_up(index, attempt, summary, n_ions=sum(self.plan[index]))
            return
        self._finish_precision(index, "done", values, errs)

    def _decide(self, index, batch, attempt):
        """All batches of a precision point planned so far are done: met, or add batches,
        or stop at the ion maximum."""
        precision = self.precision
        point = self.points[index]
        ions = sum(self.plan[index])
        beams = self._done_beams(index)
        try:
            values, errs = self._estimate(index, beams)
            target = precision.target
            if target not in values:
                raise SpecError(
                    f"precision target {target!r} is not an extracted column; the columns "
                    f"are: {', '.join(values) or 'none'}"
                )
            if not _is_number(values[target]):
                raise SpecError(f"precision target {target!r} is not a number: {values[target]!r}")
        except Exception as exc:
            summary = _summary(exc)
            self._log_error(index, attempt, summary, traceback.format_exc())
            self._give_up(index, attempt, summary, n_ions=ions)
            return
        value, err = values[target], errs.get(target, math.nan)
        goal = goal_of(precision, value)
        self._emit(
            BatchFinished(
                index,
                batch,
                ions,
                {name: [values[name], errs[name]] for name in errs},
            )
        )
        if is_met(err, goal):
            self._finish_precision(index, "done", values, errs)
            return
        if ions >= precision.max_ions:
            warnings.warn(
                f"precision target not reached for scan point {index} ({point.params}): "
                f"{target} = {value:.6g} +/- {err:.3g} after {ions} ions (goal "
                f"{goal:.3g}); stopped at max_ions={precision.max_ions}",
                stacklevel=2,
            )
            self._finish_precision(index, "max_ions", values, errs)
            return
        if self.stopping:  # the point is stopped: its partial estimate is kept
            return
        count = batches_to_add(precision, ions, err, goal, len(beams))
        self._schedule(index, count)

    def _record(self, index, beams):
        """Record the finished beam batches of a batched point in its folder (see
        :func:`srim.scan.batches.write_point_record`): the units, the point's spec with the
        total ions and the point seed, and for a binned beam the bins with their ions."""
        units = self._units_of(index, beams)
        rows = []
        for unit in units:
            beam, k = self.units[index][unit] if self._binned(index) else (unit, None)
            seed = derive_seed(self.seeds[index], unit)
            rows.append((unit, beam, k, self.plan[index][unit], seed))
        ions = sum(self.plan[index][unit] for unit in units)
        spec = self.points[index].spec.with_overrides(
            [f"number_ions={ions}", f"seed={self.seeds[index]}"]
        )
        beam_bins = None
        if self._binned(index):
            from ..beam.binned import bins_frame  # lazy: srim.beam imports srim.scan

            counts = [0] * len(self.bins[index])
            for unit in units:
                counts[self.units[index][unit][1]] += self.plan[index][unit]
            beam_bins = bins_frame(self.bins[index], counts)
        try:
            write_point_record(
                self.dirs[index],
                spec,
                rows,
                beam_bins,
                provenance={"pysrim2_version": __version__},
            )
        except OSError as exc:
            log.warning("scan point %d: its record could not be written (%s)", index, exc)

    def _finish_precision(self, index, status, values, errs):
        """A precision point is done (``done``, or ``max_ions``): its row and event."""
        point = self.points[index]
        ions = sum(self.plan[index])
        row_values = {**values, **{f"{name}_err": err for name, err in errs.items()}}
        self._record(index, self._done_beams(index))
        self.rows[index] = {
            "index": index,
            "status": self._status(index, status),
            "n_ions": ions,
            "seeds": self._row_seeds(index),
            "path": str(self.dirs[index]),
            "error": None,
            **row_values,
        }
        self.n_done += 1
        seconds = sum(self.finished[index].values())
        self._emit(PointFinished(index, point.id, row_values, ions, seconds))

    def _merged_values(self, index, units):
        """The extractors' values on the given units merged (their ions in order)."""
        point = self.points[index]
        ions = sum(self.plan[index][unit] for unit in units)
        spec = point.spec.with_overrides([f"number_ions={ions}"]).to_dict()
        values = merged_values(
            self.extract,
            point,
            [self._unit_dir(index, unit) for unit in units],
            self._infos(index, units),
            spec,
            self.seeds[index],
        )
        self._check_names(values)
        return values

    def _estimate(self, index, beams):
        """Values and errors of a point from its finished beam batches (a plain batch is one).

        The values are the extractors applied to the merged units of the beam batches; each
        error is the batch-means error of the beam batches' values, weighted by their ions
        (:func:`~srim.scan.precision.batch_error`). A binned point with a single beam batch
        has no errors (there is nothing to take a spread of).
        """
        point = self.points[index]
        beams = list(beams)
        units = self._units_of(index, beams)
        ions = sum(self.plan[index][unit] for unit in units)
        spec = point.spec.with_overrides([f"number_ions={ions}"]).to_dict()
        values, errs = estimate_batches(
            self.extract,
            point,
            [self._unit_dir(index, unit) for unit in units],
            self._infos(index, units),
            spec,
            self.seeds[index],
            [self.batch_values[(index, beam)] for beam in beams],
            weights=[self._beam_ions(index, beam) for beam in beams],
        )
        if self._binned(index) and len(beams) < 2:
            errs = {}
        self._check_names(values)
        return values, errs

    def _failed(self, index, batch, attempt, summary, tb):
        self._log_error(index, attempt, summary, tb)
        if attempt > self.retries:
            self._give_up(index, attempt, summary, n_ions=self._done_ions(index) or None)
            return
        point = self.points[index]
        self.last_error[index] = summary
        self._emit(PointFailed(index, point.id, summary, attempt, final=False))
        self.pending.append((index, batch, attempt + 1))
        self._emit(PointRetried(index, point.id, attempt + 1))

    def _give_up(self, index, attempt, summary, n_ions=None):
        point = self.points[index]
        self._emit(PointFailed(index, point.id, summary, attempt, final=True))
        self.pending = deque(unit for unit in self.pending if unit[0] != index)
        self.waiting = [unit for unit in self.waiting if unit[0] != index]
        self._release_idle(index)
        self.rows[index] = {
            "index": index,
            "status": "failed",
            "n_ions": n_ions,
            "seeds": self._row_seeds(index),
            "path": str(self.dirs[index]),
            "error": summary,
        }
        self.n_failed += 1

    def _settle_unfinished(self):
        """A row and a ``point_stopped`` event for every point that did not finish.

        A point that failed earlier keeps its folder (with ``error.log``) and its last error;
        the outputs of an attempt that was cut short are removed. A precision point with
        finished batches keeps them, and its row has their partial estimate.
        """
        for point in self.points:
            index = point.index
            if index in self.rows:
                continue
            error = self.last_error.get(index)
            done = self._done_beams(index)
            if done:
                self.rows[index] = self._partial_row(index, done, error)
            else:
                self._clear_outputs(index, keep_log=error is not None)
                self.rows[index] = {
                    "index": index,
                    "status": "stopped",
                    "n_ions": None,
                    "seeds": None,
                    "path": str(self.dirs[index]) if error is not None else None,
                    "error": error,
                }
            self._emit(PointStopped(index, point.id, error))

    def _partial_row(self, index, done, error):
        """The row of a precision point stopped with some finished batches (``done``: its
        finished beam batches)."""
        try:
            values, errs = self._estimate(index, done)
        except Exception as exc:
            log.warning("scan point %d: no partial estimate (%s)", index, _summary(exc))
            values, errs = {}, {}
        units = self._units_of(index, done)
        self._clear_outputs(index, keep_log=error is not None, keep_batches=units)
        self._record(index, done)
        ions = sum(self._beam_ions(index, beam) for beam in done)
        return {
            "index": index,
            "status": "stopped",
            "n_ions": ions,
            "seeds": self._row_seeds(index),
            "path": str(self.dirs[index]),
            "error": error,
            **values,
            **{f"{name}_err": err for name, err in errs.items()},
        }

    def _clear_outputs(self, index, keep_log, keep_batches=()):
        """Remove a point's outputs; keep ``error.log`` if ``keep_log`` and the batch folders
        listed in ``keep_batches`` (the others go)."""
        out = self.dirs[index]
        if not keep_log and not keep_batches:
            shutil.rmtree(out, ignore_errors=True)
            return
        for entry in out.iterdir() if out.is_dir() else ():
            if entry.name == "error.log" and keep_log:
                continue
            if entry.name == "batches" and keep_batches and entry.is_dir():
                for batch_dir in entry.iterdir():
                    if not (batch_dir.name.isdigit() and int(batch_dir.name) in keep_batches):
                        shutil.rmtree(batch_dir, ignore_errors=True)
                continue
            if entry.is_dir() and not entry.is_symlink():
                shutil.rmtree(entry, ignore_errors=True)
            else:
                entry.unlink(missing_ok=True)

    # -- output ------------------------------------------------------------------------

    def _check_names(self, values):
        clash = sorted(set(values) & self.names)
        if clash:
            raise ValueError(f"extractor column(s) {clash} clash with the scan's own columns")

    def _log_error(self, index, attempt, summary, tb):
        out = self.dirs[index]
        out.mkdir(parents=True, exist_ok=True)
        with open(out / "error.log", "a", encoding="utf-8") as f:
            f.write(f"attempt {attempt}: {summary}\n{tb}\n")

    def _emit(self, event):
        if self.on_event is None:
            return
        try:
            self.on_event(event)
        except Exception:
            log.warning("on_event raised on %s; the scan goes on", event.kind, exc_info=True)


# -- helpers -------------------------------------------------------------------------------


def _container_runner(image, runtime):
    """The runner of the container back end: one container per unit, checked before the
    scan starts (no runtime is an error now, not in each worker)."""
    from ..installer import container
    from .backends import ContainerRunner

    if runtime is None:
        runtime = container.find_runtime()
        if runtime is None:
            raise container.ContainerError(
                "no container runtime (podman or docker) is available: install one with "
                "'pysrim2 install runtime', or use backend='local'"
            )
    reference = image or os.environ.get("PYSRIM2_IMAGE")
    reference = reference or container.image_ref(container.image_tag())
    return ContainerRunner(runtime, reference)


def _open_cache(cache, folder):
    """The :class:`~srim.scan.cache.RunCache` that ``cache=`` names, prepared, or None."""
    root = cache_root(cache, folder)
    if root is None:
        return None
    store = RunCache(root)
    store.prepare()
    return store


def _summary(exc) -> str:
    """One line: ``ValueError: message`` (the message's line breaks become spaces). An
    exception may name its own summary in ``scan_summary`` (container units do)."""
    own = getattr(exc, "scan_summary", None)
    if own:
        return str(own)
    text = " ".join(str(exc).split())
    name = type(exc).__name__
    return f"{name}: {text}" if text else name


def fixed_batch_sizes(total: int, batch_ions: int) -> list[int]:
    """The ions of each batch of a fixed count: ``batch_ions`` each, the last one the rest."""
    return [min(batch_ions, total - i * batch_ions) for i in range(math.ceil(total / batch_ions))]


def merged_values(extract, point, directories, infos, spec, base_seed):
    """The values of ``extract`` on the batches merged (:class:`~srim.scan.MergedResults`,
    built from ``directories`` and ``infos``, the :class:`BatchInfo` of each batch, in order).
    """
    parts, merged = [], None
    try:
        for directory in directories:
            parts.append(Results(str(directory)))
        merged = MergedResults(parts, infos, spec=spec, base_seed=base_seed)
        values = dict(extract(merged, point))
    finally:
        if merged is not None:  # closes the parts too
            merged.close()
        else:
            for part in parts:
                part.close()
    return values


def estimate_batches(
    extract, point, directories, infos, spec, base_seed, batch_values, weights=None
):
    """Values and errors of a point from its batches (the batch-means estimator of
    :mod:`srim.scan.precision`).

    ``extract`` is applied to the batches merged (see :func:`merged_values`); each error is
    the batch-means error of ``batch_values``, the values of each batch alone, weighted by
    ``weights`` (default: the ions of each of ``infos``).
    """
    values = merged_values(extract, point, directories, infos, spec, base_seed)
    if weights is None:
        weights = [info.n_ions for info in infos]
    errs = {}
    for name, value in values.items():
        if _is_number(value):
            samples = [_as_float(batch.get(name)) for batch in batch_values]
            errs[name] = batch_error(samples, weights)
    return values, errs


def _is_number(value) -> bool:
    return isinstance(value, Real) and not isinstance(value, bool)


def _as_float(value) -> float:
    return float(value) if _is_number(value) else math.nan


def _signal_worker(worker, sig):
    """Send ``sig`` to the worker's process group (the worker, SRIM and their children).

    Only a group the worker leads is signalled as a group; never the parent's own group.
    The group id was recorded when the worker started, so this works after the worker
    has exited and been reaped. On Windows ``taskkill /T /F`` ends the process tree; it
    must be sent while the worker still exists (the PID is not reused yet), and it ends
    everything at once, whatever ``sig`` is.
    """
    if sys.platform == "win32":
        if worker.process.pid is not None:
            with contextlib.suppress(OSError):
                subprocess.run(
                    ["taskkill", "/T", "/F", "/PID", str(worker.process.pid)],
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                    check=False,
                )
        return
    if worker.pgid is None:
        return
    with contextlib.suppress(ProcessLookupError, PermissionError):
        os.killpg(worker.pgid, sig)


def _close_queue(q):
    with contextlib.suppress(Exception):
        q.cancel_join_thread()
        q.close()


def _worker_count(workers) -> int:
    if workers is None:
        return max(1, (os.cpu_count() or 1) - 1)
    if isinstance(workers, bool) or not isinstance(workers, Integral) or workers < 1:
        raise ValueError(f"workers must be a whole number >= 1, got {workers!r}")
    return int(workers)


def _retry_count(retries) -> int:
    if isinstance(retries, bool) or not isinstance(retries, Integral) or retries < 0:
        raise ValueError(f"retries must be a whole number >= 0, got {retries!r}")
    return int(retries)


def _require_srim():
    directory = srim_dir()
    if not os.path.isfile(os.path.join(directory, "TRIM.exe")):
        raise FileNotFoundError(
            f"SRIM is not installed in {directory} (no TRIM.exe); install it with "
            "'pysrim2 install', or pass runner="
        )


def _prune_old_runs():
    """Backstop for scans that were killed hard: remove old unused run directories.

    Uses the retention of :func:`srim.rundir.prune_runs` (directories in use are skipped).
    Never fails the scan.
    """
    from ..rundir import prune_runs

    with contextlib.suppress(Exception):
        prune_runs()


def _new_folder(scan_dir) -> Path:
    """The scan folder: ``scan_dir`` (made if missing) or a new ``scan-<UTC time>``."""
    if scan_dir is not None:
        folder = Path(os.path.abspath(os.fspath(scan_dir)))
        folder.mkdir(parents=True, exist_ok=True)
        return folder
    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    folder = Path(os.path.abspath(f"scan-{stamp}"))
    number = 1
    while True:
        try:
            folder.mkdir(parents=True)
            return folder
        except FileExistsError:  # two scans in the same second
            number += 1
            folder = Path(os.path.abspath(f"scan-{stamp}-{number}"))


@contextlib.contextmanager
def _stop_on_signals(stop):
    """SIGINT and SIGTERM set ``stop``; a second SIGINT raises KeyboardInterrupt.

    Installed only in the main thread (signals cannot be handled elsewhere) and restored
    on exit.
    """
    if threading.current_thread() is not threading.main_thread():
        yield
        return
    presses = 0

    def on_signal(signum, frame):
        nonlocal presses
        presses += 1
        if presses > 1:
            raise KeyboardInterrupt
        stop.set()

    previous = []
    try:
        for name in ("SIGINT", "SIGTERM"):
            sig = getattr(signal, name, None)
            if sig is not None:
                previous.append((sig, signal.signal(sig, on_signal)))
        yield
    finally:
        for sig, handler in previous:
            signal.signal(sig, handler if handler is not None else signal.SIG_DFL)
