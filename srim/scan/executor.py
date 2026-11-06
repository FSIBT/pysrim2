"""Run a scan on this machine: every point in a worker process, with events and retries.

:func:`run_scan` runs the points of a :class:`~srim.scan.Scan` in a pool of worker processes.
Each worker creates one private run directory (:class:`srim.rundir.RunDir`) at its first
point and reuses it for all its points; the directory is removed when the worker exits.
The parent process hands out the points, runs the extractors on each finished point's
outputs (so extractors need not be picklable) and reports every step as an event
(:mod:`srim.scan.events`), always from the calling thread and in order.

Layout of a scan folder::

    scan.yaml                  the scan (base run spec and scan section), with the base seed
    points/<id[:16]>/          one point's outputs and run.yaml; error.log if an attempt failed
    result/                    scan.csv and metadata.yaml (ScanResult.to_csv)

Seeds: a point runs with ``derive_seed(base_seed, int(id[:15], 16))``, so a point keeps its
seed when points are added or reordered. A failed point is retried ``retries`` times with
the same seed, then gets status ``failed``; the scan goes on.

Stopping: a ``stop_event`` being set, or SIGINT or SIGTERM in the main thread, stops
scheduling. Running points are killed (SIGTERM to each worker's process group, which also
reaches SRIM; the worker then stops its wine server; SIGKILL after a grace period). Finished
points are kept; the others get status ``stopped``. POSIX only for the process groups; on
Windows ``taskkill /T`` ends each worker and its children.

Scripts that use the default ``spawn`` start method of the workers (macOS, Windows) must
guard their entry point with ``if __name__ == "__main__":``.
"""

import contextlib
import logging
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
from collections import deque
from dataclasses import dataclass
from datetime import UTC, datetime
from multiprocessing.process import BaseProcess
from multiprocessing.queues import Queue
from numbers import Integral
from pathlib import Path

from .. import __version__
from ..output import Results
from ..paths import srim_dir
from ..rundir import RunDir
from ..spec import RunSpec
from .batches import MAX_SEED, derive_seed
from .events import (
    PointFailed,
    PointFinished,
    PointProgress,
    PointQueued,
    PointRetried,
    PointStarted,
    ScanFinished,
    ScanStarted,
    WorkerStats,
)
from .extract import combine
from .precision import Precision
from .result import ScanResult
from .spec import Scan

log = logging.getLogger(__name__)

#: seconds a worker gets to stop cleanly (after SIGTERM) before it is killed
GRACE_SECONDS = 10.0
#: seconds between two looks at the stop flag and the workers
_POLL_SECONDS = 0.1
_SIGKILL = getattr(signal, "SIGKILL", signal.SIGTERM)
#: columns the scan builds itself; an extractor may not produce them
_RESERVED = frozenset({"index", "id", "status", "error", "path", "n_ions", "seeds"})


def run_scan(
    scan,
    *,
    workers=None,
    backend="local",
    scan_dir=None,
    on_event=None,
    stop_event=None,
    runner=None,
    cache=None,
    retries=1,
) -> ScanResult:
    """Run every point of ``scan`` and return the :class:`~srim.scan.ScanResult`.

    Parameters
    ----------
    scan : :class:`~srim.scan.Scan`
        the scan to run. Python extractors run in this process (they need not be picklable)
        and are written to ``scan.yaml`` as ``python:<module>:<name>`` references.
    workers : :obj:`int`, optional
        worker processes; default ``max(1, os.cpu_count() - 1)``, never more than points.
    backend : :obj:`str`
        ``"local"`` (the default) runs on this machine. The container back end is planned
        (Task 9); other names raise :class:`NotImplementedError`.
    scan_dir : path-like, optional
        the scan folder (made if missing). Default: a new ``./scan-<UTC time>`` folder.
    on_event : callable, optional
        ``on_event(event)`` for every event (see :mod:`srim.scan.events`). Called in this
        process, from one thread, in order. An exception in it is logged as a warning and
        does not stop the scan.
    stop_event : :class:`threading.Event`, optional
        set it to stop the scan (see the module docstring)
    runner : callable, optional
        ``runner(spec) -> Results`` (a picklable top-level function, as for
        :func:`srim.scan.run_batches`) in place of SRIM. Its results folder is copied to
        the point's folder and the runner owns its run directory. For tests and custom
        back ends. Default: ``TRIM.from_spec(spec).run(...)`` in a private run directory.
    cache : optional
        reserved for the result cache (Task 8); accepted and ignored for now.
    retries : :obj:`int`
        how often a failed point is run again before it is reported as failed (default 1)

    Returns
    -------
    :class:`~srim.scan.ScanResult`
        one row per point, also written to ``<scan_dir>/result/``. Its ``scan`` has the
        base seed filled in when the scan had none.

    Raises
    ------
    NotImplementedError
        for ``backend`` other than ``"local"`` and for precision targets (Task 7)
    FileNotFoundError
        when there is no SRIM install and no ``runner`` is given
    """
    if backend != "local":
        raise NotImplementedError(
            f"backend {backend!r} is not available: only 'local' is (the 'container' "
            "backend is Task 9)"
        )
    if isinstance(scan.ions, Precision):
        raise NotImplementedError("precision targets: Task 7")
    workers = _worker_count(workers)
    retries = _retry_count(retries)
    del cache  # the result cache is Task 8
    if runner is None:
        _require_srim()
    base_seed = scan.seed if scan.seed is not None else secrets.randbelow(MAX_SEED) + 1
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
        )
    )
    folder = _new_folder(scan_dir)
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
    )
    with _stop_on_signals(run.stop):
        return run.execute()


# -- worker processes ----------------------------------------------------------------------

#: state of the worker process itself: its one reusable run directory
_WORKER: dict = {"run_dir": None}


def _worker_main(ident, tasks, results, runner, parent_pid):
    """Worker process: run the points it is handed until it gets ``None``.

    Reports ``("done", ident, index, attempt, seconds)`` or ``("error", ident, index,
    attempt, summary, traceback)`` for each point. The worker leads its own process group
    so that the parent can stop it and everything it started (SRIM) at once.
    """
    if hasattr(os, "setpgid"):
        with contextlib.suppress(OSError):
            os.setpgid(0, 0)
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
            index, attempt, spec, out = task
            began = time.monotonic()
            try:
                _run_point(RunSpec.from_dict(spec), out, runner)
            except BaseException as exc:  # reported; the parent decides on a retry
                results.put(("error", ident, index, attempt, _summary(exc), traceback.format_exc()))
            else:
                results.put(("done", ident, index, attempt, time.monotonic() - began))
    finally:
        run_dir, _WORKER["run_dir"] = _WORKER["run_dir"], None
        if run_dir is not None:
            with contextlib.suppress(Exception):
                run_dir.cleanup()


def _terminate_worker(signum, frame):
    """SIGTERM in a worker: stop this worker's wine server and remove its run directory.

    The parent signals the whole process group, so SRIM stops at the same time.
    """
    run_dir = _WORKER["run_dir"]
    if run_dir is not None:
        with contextlib.suppress(Exception):
            run_dir.cleanup()
    os._exit(1)


def _run_point(spec, out, runner):
    """Run one point and leave its outputs in ``out``."""
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
    """One worker process as the parent sees it. ``current`` is ``(index, attempt,
    started)`` of the point it runs, or ``None`` when it is idle."""

    ident: int
    process: BaseProcess
    tasks: Queue
    current: tuple | None = None

    @property
    def busy(self) -> bool:
        return self.current is not None


class _ScanRun:
    """The state of one :func:`run_scan` call. Used from the calling thread only."""

    def __init__(self, scan, folder, *, workers, base_seed, runner, retries, on_event, stop):
        self.scan = scan
        self.folder = folder
        self.points = scan.points()
        self.n_workers = min(workers, len(self.points))
        self.base_seed = base_seed
        self.runner = runner
        self.retries = retries
        self.on_event = on_event
        self.stop = stop
        self.extract = combine(scan.extract)
        self.names = set(scan.param_names) | _RESERVED
        self.seeds = [derive_seed(base_seed, int(p.id[:15], 16)) for p in self.points]
        self.specs = [
            p.spec.with_overrides([f"seed={seed}"])
            for p, seed in zip(self.points, self.seeds, strict=True)
        ]
        self.dirs = [folder / "points" / p.id[:16] for p in self.points]
        self.pending = deque((p.index, 1) for p in self.points)
        self.rows: dict[int, dict] = {}
        self.workers: dict[int, _Worker] = {}
        self.stopping = False
        self.n_done = 0
        self.n_failed = 0
        self.ctx = multiprocessing.get_context("spawn")
        self.results = self.ctx.Queue()

    # -- the whole run -----------------------------------------------------------------

    def execute(self) -> ScanResult:
        started = time.monotonic()
        self._emit(ScanStarted(len(self.points), self.n_workers, self.base_seed, str(self.folder)))
        for point in self.points:
            self._emit(PointQueued(point.index, point.id))
        try:
            for ident in range(self.n_workers):
                self.workers[ident] = self._spawn(ident)
            self._loop()
        finally:
            self._shutdown()
        rows = self._rows_in_order()
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
            self._restart_dead()
            self._dispatch()
            if not self._busy() and not self.pending:
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
        return _Worker(ident, process, tasks)

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
        for worker in self.workers.values():
            if not self.pending or self.stop.is_set():
                return
            if worker.busy or not worker.process.is_alive():
                continue
            index, attempt = self.pending.popleft()
            self._send(worker, index, attempt)

    def _send(self, worker, index, attempt):
        point = self.points[index]
        out = self.dirs[index]
        if attempt == 1:  # a new scan may reuse the folder of an old one: start clean
            shutil.rmtree(out, ignore_errors=True)
        out.mkdir(parents=True, exist_ok=True)
        worker.current = (index, attempt, time.monotonic())
        worker.tasks.put((index, attempt, self.specs[index].to_dict(), str(out)))
        self._emit(PointStarted(index, point.id, worker.ident, attempt))
        self._emit(PointProgress(index, 0, point.spec.number_ions))

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
        kind, ident, index, attempt = message[:4]
        worker = self.workers.get(ident)
        if worker is None or worker.current is None or worker.current[:2] != (index, attempt):
            return  # stale: the worker was already stopped or replaced
        worker.current = None
        if kind == "done":
            self._finished(worker, index, attempt, message[4])
        elif not self.stopping:  # an error while stopping is the stop itself
            self._failed(index, attempt, message[4], message[5])

    def _reap(self):
        """Attempts whose worker died without a word count as failed."""
        for worker in self._busy():
            if worker.process.is_alive():
                continue
            self._drain(0.5)
            if worker.busy:
                index, attempt, _ = worker.current
                worker.current = None
                self._failed(
                    index,
                    attempt,
                    f"worker process exited unexpectedly (exit code {worker.process.exitcode})",
                    "",
                )

    def _halt(self):
        """Stop: kill the running points, keep what finished, drop the rest."""
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

    def _shutdown(self):
        """Stop every worker process; the ones still running a point are killed."""
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

    # -- outcomes of one attempt -------------------------------------------------------

    def _finished(self, worker, index, attempt, seconds):
        point = self.points[index]
        out = self.dirs[index]
        n_ions = point.spec.number_ions
        self._emit(PointProgress(index, n_ions, n_ions))
        try:
            with Results(str(out)) as results:
                values = dict(self.extract(results, point))
            clash = sorted(set(values) & self.names)
            if clash:
                raise ValueError(f"extractor column(s) {clash} clash with the scan's own columns")
        except Exception as exc:
            summary = _summary(exc)
            self._log_error(index, attempt, summary, traceback.format_exc())
            self._give_up(index, attempt, summary)
            return
        self.rows[index] = {
            "index": index,
            "status": "done",
            "n_ions": n_ions,
            "seeds": str(self.seeds[index]),
            "path": str(out),
            "error": None,
            **values,
        }
        self.n_done += 1
        self._emit(PointFinished(index, point.id, values, n_ions, seconds))
        ions_per_s = n_ions / seconds if seconds > 0 else None
        self._emit(WorkerStats(worker.ident, ions_per_s))

    def _failed(self, index, attempt, summary, tb):
        self._log_error(index, attempt, summary, tb)
        if attempt > self.retries:
            self._give_up(index, attempt, summary)
            return
        point = self.points[index]
        self._emit(PointFailed(index, point.id, summary, attempt, final=False))
        self.pending.append((index, attempt + 1))
        self._emit(PointRetried(index, point.id, attempt + 1))

    def _give_up(self, index, attempt, summary):
        point = self.points[index]
        self._emit(PointFailed(index, point.id, summary, attempt, final=True))
        self.rows[index] = {
            "index": index,
            "status": "failed",
            "n_ions": None,
            "seeds": str(self.seeds[index]),
            "path": str(self.dirs[index]),
            "error": summary,
        }
        self.n_failed += 1

    def _rows_in_order(self) -> list[dict]:
        rows = []
        for point in self.points:
            row = self.rows.get(point.index)
            if row is None:  # stopped before it finished: no outputs are kept
                shutil.rmtree(self.dirs[point.index], ignore_errors=True)
                row = {
                    "index": point.index,
                    "status": "stopped",
                    "n_ions": None,
                    "seeds": None,
                    "path": None,
                    "error": None,
                }
            rows.append(row)
        return rows

    # -- output ------------------------------------------------------------------------

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


def _summary(exc) -> str:
    """One line: ``ValueError: message`` (the message's line breaks become spaces)"""
    text = " ".join(str(exc).split())
    name = type(exc).__name__
    return f"{name}: {text}" if text else name


def _signal_worker(worker, sig):
    """Send ``sig`` to the worker's process group (the worker, SRIM and their children).

    Only a group the worker leads is signalled as a group; never the parent's own group.
    On Windows ``taskkill /T`` ends the process tree.
    """
    pid = worker.process.pid
    if pid is None:
        return
    if sys.platform == "win32":
        with contextlib.suppress(OSError):
            subprocess.run(
                ["taskkill", "/T", "/F", "/PID", str(pid)],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                check=False,
            )
        return
    with contextlib.suppress(ProcessLookupError, PermissionError):
        if os.getpgid(pid) == pid and pid != os.getpgrp():
            os.killpg(pid, sig)
        else:
            os.kill(pid, sig)


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
