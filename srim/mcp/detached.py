"""Scan runs that outlive the process that started them (the MCP server, a terminal).

:func:`start_detached` starts ``python -m srim.mcp.detached <folder>`` in a session (POSIX) or
process group (Windows) of its own, with its output in ``runner.log``. The runner runs the scan
of ``<folder>/scan.yaml`` with :func:`srim.scan.run_scan`, writes every event as one line of
``events.jsonl`` and acts on the requests in ``control.jsonl`` (see :mod:`srim.ui.web`). Nothing
here needs the process that started the run: :func:`run_state` reads the folder, and
:func:`send_control` writes a request.

The folder holds ``runner.json`` (``pid``, ``started``, ``workers``, ``backend``, and ``error``
when the run failed). A run whose process is gone without a ``scan_finished`` event is
"crashed". Standard library only.
"""

from __future__ import annotations

import argparse
import json
import os
import signal
import subprocess
import sys
import threading
import time
from pathlib import Path

from srim.scan import Scan
from srim.scan.events import event_from_dict
from srim.scan.executor import MARKER_NAME
from srim.ui.tui.state import ScanState
from srim.ui.web import CONTROL_FILE, read_requests, send_request

from .backend import BackendError

#: the events of the run, one JSON line each (the runner writes them, :func:`run_state` reads)
EVENTS_FILE = "events.jsonl"
#: the runner's pid and settings (written by :func:`start_detached`, updated on failure)
PID_FILE = "runner.json"
#: the runner's stdout and stderr
LOG_FILE = "runner.log"
#: seconds between two reads of the control file
POLL_SECONDS = 0.5
#: the requests the runner acts on; a request of another word is ignored
ACTIONS = ("pause", "resume", "stop")
#: exit codes, as ``pysrim2 scan run``
EXIT_OK = 0
EXIT_FAILED = 1
EXIT_BAD_INPUT = 2
EXIT_INTERRUPTED = 130
#: Windows process creation flags (the values of subprocess.CREATE_NEW_PROCESS_GROUP and
#: subprocess.DETACHED_PROCESS, which exist on Windows only)
_CREATE_NEW_PROCESS_GROUP = 0x00000200
_DETACHED_PROCESS = 0x00000008
#: the exit code that GetExitCodeProcess reports for a process still running
_STILL_ACTIVE = 259


def start_detached(folder, workers: int, backend: str = "local", env: dict | None = None) -> int:
    """Start the run of the scan in ``folder`` and return the pid of its runner.

    The folder must be a scan folder (made with :func:`srim.scan.executor.mark_scan_folder`
    before ``scan.yaml`` is written, as :func:`run_scan` requires) and hold ``scan.yaml``.
    ``env`` is added to the environment of the runner.

    Raises :class:`~srim.mcp.backend.BackendError` when the folder has no scan or a run is
    already going in it (the events and the control file of that run would be lost).
    """
    folder = Path(folder)
    if not (folder / "scan.yaml").is_file():
        raise BackendError(f"{folder} has no scan.yaml: save the scan first")
    if not (folder / MARKER_NAME).is_file():
        raise BackendError(
            f"{folder} is not a scan folder: call mark_scan_folder on it before writing scan.yaml"
        )
    previous = _runner_meta(folder)  # raises when runner.json is unreadable
    if previous is not None and run_state(folder)["status"] in ("running", "paused"):
        raise BackendError(f"a run is already going in {folder}; wait for it or stop it")
    # the old runner.json (and any error it kept) is not this run's
    (folder / PID_FILE).unlink(missing_ok=True)
    # a new run starts with empty events and control files: an old stop or an old
    # scan_finished must not be read as this run's
    (folder / CONTROL_FILE).write_bytes(b"")
    (folder / EVENTS_FILE).write_bytes(b"")
    command = [
        sys.executable,
        "-m",
        "srim.mcp.detached",
        str(folder),
        "--workers",
        str(workers),
        "--backend",
        backend,
    ]
    environment = {**os.environ, **(env or {})}
    options: dict = {}
    if os.name == "nt":
        options["creationflags"] = _CREATE_NEW_PROCESS_GROUP | _DETACHED_PROCESS
    else:
        options["start_new_session"] = True
    with open(os.devnull, "rb") as stdin, open(folder / LOG_FILE, "w", encoding="utf-8") as log:
        process = subprocess.Popen(
            command,
            stdin=stdin,
            stdout=log,
            stderr=subprocess.STDOUT,
            env=environment,
            **options,
        )
    meta = {
        "pid": process.pid,
        "started": time.time(),
        "workers": workers,
        "backend": backend,
    }
    # the runner may have written its own early error into runner.json already: keep it
    _write_meta(folder, {**(_runner_meta(folder) or {}), **meta})
    return process.pid


def run_state(folder) -> dict:
    """The state of the run in ``folder``, read from its events and its runner.

    ``status`` is "running", "paused", "finished", "stopped" (a stop was done), "failed" (the
    runner ended with an error) or "crashed" (the runner is gone without a ``scan_finished``
    event). The counts are of the points and ions the events show so far; ``ions_total`` is the
    planned ions of the points that have a fixed number (precision targets are not counted).
    Raises :class:`~srim.mcp.backend.BackendError` when the folder has no scan or no runner.
    """
    folder = Path(folder)
    if not (folder / "scan.yaml").is_file():
        raise BackendError(f"{folder} has no scan.yaml")
    meta = _runner_meta(folder)
    if meta is None:
        raise BackendError(f"no run was started in {folder}")
    # liveness first: a runner that ends writes its last events before it is gone, so the
    # events read after this check are complete
    alive = _pid_alive(meta.get("pid"))
    meta = _runner_meta(folder) or meta  # the error of a run that ended is written before it goes
    state = _replay(folder)
    if state.finished:
        status = "stopped" if state.stopped else "finished"
    elif meta.get("error"):
        status = "failed"
    elif not alive:
        status = "crashed"
    elif state.paused:
        status = "paused"
    else:
        status = "running"
    errors = [text for level, text in state.warnings if level == "error"]
    if meta.get("error") and not state.finished:
        errors.append(meta["error"])
    return {
        "folder": str(folder),
        "status": status,
        "points_done": state.finished_points(),
        "points_total": state.n_points,
        "ions_done": state.total_ions(),
        "ions_total": sum(row.planned or 0 for row in state.points.values()),
        "errors": errors,
        "elapsed_s": round(state.elapsed(time.time()), 3),
    }


def send_control(folder, action: str) -> None:
    """Ask the run in ``folder`` to ``pause``, ``resume`` or ``stop``. Raises
    :class:`~srim.mcp.backend.BackendError` for another action, or when the run is not going
    (finished, stopped, failed or crashed)."""
    if action not in ACTIONS:
        raise BackendError(f"unknown action {action!r} (have: {', '.join(ACTIONS)})")
    status = run_state(folder)["status"]
    if status not in ("running", "paused"):
        raise BackendError(f"the run is {status}; there is nothing to {action}")
    send_request(folder, action)


def main(argv: list[str] | None = None) -> int:
    """The runner: run the scan of a folder (see the module docstring). Exit code as
    ``pysrim2 scan run``: 0 written, 130 stopped, 2 bad input, 1 failed."""
    parser = argparse.ArgumentParser(prog="python -m srim.mcp.detached")
    parser.add_argument("folder")
    parser.add_argument("--workers", type=int, default=None)
    parser.add_argument("--backend", default="local")
    args = parser.parse_args(argv)
    folder = Path(args.folder)

    from srim.cli import _runner_from_env  # the test runner of PYSRIM2_SCAN_RUNNER, if set
    from srim.scan import run_scan
    from srim.scan.executor import ScanBusy

    try:
        scan = Scan.from_yaml(folder / "scan.yaml")
        runner = _runner_from_env()
    except Exception as exc:  # a scan that does not load is bad input, whatever the cause
        _record_error(folder, str(exc))
        print(f"{folder}: {exc}", file=sys.stderr)
        return EXIT_BAD_INPUT

    stop, pause = threading.Event(), threading.Event()
    done = threading.Event()
    outcome = {"stopped": False}
    # append: start_detached starts the file empty, and a runner that loses the scan lock
    # (ScanBusy) must not wipe the events of the run that holds it
    with open(folder / EVENTS_FILE, "a", encoding="utf-8", newline="\n") as events_file:

        def on_event(event):
            events_file.write(event.to_json() + "\n")
            events_file.flush()
            if event.kind == "scan_finished":
                outcome["stopped"] = event.stopped

        poller = threading.Thread(
            target=_poll_control,
            args=(folder, stop, pause, done),
            name="pysrim2-control",
            daemon=True,
        )
        previous = _stop_on_sigterm(stop)
        poller.start()
        try:
            result = run_scan(
                scan,
                workers=args.workers,
                backend=args.backend,
                scan_dir=folder,
                on_event=on_event,
                stop_event=stop,
                pause_event=pause,
                runner=runner,
            )
        except ScanBusy as exc:
            _record_error(folder, str(exc))
            print(exc, file=sys.stderr)
            return EXIT_FAILED
        except Exception as exc:  # the run is over; the error is kept for run_state
            _record_error(folder, f"{type(exc).__name__}: {exc}")
            print(f"run failed: {exc}", file=sys.stderr)
            return EXIT_FAILED
        finally:
            done.set()
            previous()
    print(f"result: {folder / 'result' / 'scan.csv'}", file=sys.stderr)
    if outcome["stopped"]:
        print("stopped: the partial result above is kept", file=sys.stderr)
        return EXIT_INTERRUPTED
    counts = result.table["status"].value_counts()
    if counts.get("failed", 0) or counts.get("missing", 0) or counts.get("stopped", 0):
        return EXIT_FAILED
    return EXIT_OK


def _poll_control(folder: Path, stop, pause, done) -> None:
    """Act on the requests of the control file every :data:`POLL_SECONDS` until ``done``."""
    offset = 0
    while not done.wait(POLL_SECONDS):
        lines, offset = read_requests(folder, offset)
        for word in lines:
            if word == "pause":
                pause.set()
            elif word == "resume":
                pause.clear()
            elif word == "stop":
                stop.set()


def _stop_on_sigterm(stop):
    """SIGTERM sets ``stop`` while the run goes on (main thread only). Returns the function that
    restores the previous handler."""
    if threading.current_thread() is not threading.main_thread():
        return lambda: None
    previous = signal.getsignal(signal.SIGTERM)
    signal.signal(signal.SIGTERM, lambda signum, frame: stop.set())
    return lambda: signal.signal(
        signal.SIGTERM, previous if previous is not None else signal.SIG_DFL
    )


def _replay(folder: Path) -> ScanState:
    """The scan state after the events of ``events.jsonl`` (none, when it is not there yet)."""
    state = ScanState(Scan.from_yaml(folder / "scan.yaml"))
    try:
        text = (folder / EVENTS_FILE).read_text(encoding="utf-8", errors="replace")
    except FileNotFoundError:
        return state
    for line in text.splitlines():
        try:
            data = json.loads(line)
        except ValueError:
            continue  # a line still being written
        if not isinstance(data, dict) or data.get("kind") == "run":
            continue
        try:
            state.apply(event_from_dict(data))
        except (TypeError, ValueError):
            continue  # not an event this version knows
    return state


def _runner_meta(folder: Path) -> dict | None:
    """The contents of ``runner.json``: None when there is none. Raises
    :class:`~srim.mcp.backend.BackendError` when it is there but cannot be read."""
    try:
        text = (folder / PID_FILE).read_text(encoding="utf-8")
    except FileNotFoundError:
        return None
    except OSError:
        raise BackendError(
            f"cannot read {folder / PID_FILE}; remove it or choose another folder"
        ) from None
    try:
        meta = json.loads(text)
    except ValueError:
        meta = None
    if not isinstance(meta, dict):
        raise BackendError(
            f"{folder / PID_FILE} is not a run file; remove it or choose another folder"
        )
    return meta


def _write_meta(folder: Path, meta: dict) -> None:
    """Write ``runner.json`` whole or not at all: a temporary file, then ``os.replace``."""
    target = folder / PID_FILE
    temporary = folder / f"{PID_FILE}.{os.getpid()}.tmp"
    temporary.write_text(json.dumps(meta, indent=2) + "\n", encoding="utf-8")
    os.replace(temporary, target)


def _record_error(folder: Path, message: str) -> None:
    """Keep the error of a failed run in ``runner.json`` (see :func:`run_state`)."""
    try:
        meta = _runner_meta(folder) or {}
    except BackendError:
        meta = {}  # unreadable: the error is what matters now
    meta["error"] = message
    _write_meta(folder, meta)


def _pid_alive(pid) -> bool:
    """True while the process ``pid`` runs. A child of this process that has ended is reaped
    here, so it is not seen as running as a zombie."""
    if not isinstance(pid, int):
        return False
    if os.name == "nt":
        kernel32 = _windows_kernel32()
        handle = kernel32.OpenProcess(0x1000, False, pid)  # PROCESS_QUERY_LIMITED_INFORMATION
        if not handle:
            return False
        try:
            import ctypes
            from ctypes import wintypes

            code = wintypes.DWORD()
            if not kernel32.GetExitCodeProcess(handle, ctypes.byref(code)):
                return False
            return code.value == _STILL_ACTIVE
        finally:
            kernel32.CloseHandle(handle)

    try:
        reaped, _ = os.waitpid(pid, os.WNOHANG)
        return reaped == 0
    except ChildProcessError:
        pass  # not a child of this process
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


def _windows_kernel32():
    """kernel32 with the argument and return types of the calls used here (Windows only).

    Without them ctypes passes and returns C ints, which truncates 64-bit handles."""
    import ctypes
    from ctypes import wintypes

    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel32.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
    kernel32.OpenProcess.restype = wintypes.HANDLE
    kernel32.GetExitCodeProcess.argtypes = [wintypes.HANDLE, ctypes.POINTER(wintypes.DWORD)]
    kernel32.GetExitCodeProcess.restype = wintypes.BOOL
    kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
    kernel32.CloseHandle.restype = wintypes.BOOL
    return kernel32


if __name__ == "__main__":
    sys.exit(main())
