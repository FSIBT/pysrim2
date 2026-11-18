"""The app's side of the agent: the open window's setup, runs and plots, answered to the agent (Qt).

:class:`AgentBackend` is the handler of the app's :class:`~srim.app.agent_server.AgentServer`. It
answers the method names of :class:`srim.mcp.backend.Backend` with the results of
:class:`srim.mcp.headless.HeadlessBackend`, so a tool cannot tell the two apart. It acts through
the window's own paths: an edit is one :meth:`DocumentModel.edit` labelled ``"agent: <summary>"``,
a run is the window's :class:`~srim.app.run_control.RunController`, and a plot is shown in the
Results tab.

A method that may block answers with a :class:`~srim.app.agent_server.Deferred`: the doctor's
checks and the reading of a run folder run in a thread (:func:`srim.app.workers.run_in_thread`).
Every Deferred settles. It is resolved with the answer, rejected with the error of a thread that
failed (or of an open that was replaced), or cancelled by the server when the window closes or the
peer goes away. Nothing here waits on the GUI thread.

The undo rule: the agent undoes and redoes only its own steps (labelled ``"agent: "``). When the
step on top is the user's, the agent is refused, because undoing it would lose the user's edit.

The methods the client computes from the run folder itself (summary, plots, tables, exports) and
the installer and delete methods are not served here: an unknown method is refused.
"""

import inspect
import logging
import os
import time
from collections.abc import Callable
from copy import deepcopy
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any

import srim
from srim.app.agent_server import INTERNAL_ERROR_TEXT, Deferred
from srim.app.document import LOCKED_TEXT
from srim.app.model import clean, problems
from srim.app.runs import RUNS_SUFFIX, ParallelChoice
from srim.app.workers import run_in_thread
from srim.mcp import headless as rules
from srim.mcp.backend import BackendError
from srim.mcp.detached import ACTIONS, run_state, send_control
from srim.spec import SpecError

logger = logging.getLogger(__name__)

#: the prefix of the undo label of every agent edit
AGENT_PREFIX = "agent: "
#: the methods the app answers; anything else is an unknown method
SERVED = frozenset(
    {
        "get_setup",
        "set_values",
        "set_section",
        "new_setup",
        "open_setup",
        "save_setup",
        "would_replace",
        "has_unsaved_changes",
        "undo",
        "redo",
        "status",
        "estimate",
        "start_run",
        "run_status",
        "control_run",
        "list_runs",
        "open_run",
        "show_plot",
        "ask",
        "log",
    }
)
#: the answer of an open that another open or a run took the place of
REPLACED_TEXT = "the open was replaced by another one before its folder was read"
#: the answer of an open that a run took the place of
RUN_STARTED_TEXT = "a run started before the folder was opened: open it again when the run ends"
#: the status of a run folder with no run file (a run that never started)
NOT_STARTED = rules.NOT_STARTED


class AgentBackend:
    """The agent's handler for one window. See the module docstring."""

    mode = "app"
    #: opening a run folder loads its setup into the window, replacing the unsaved changes
    open_run_replaces_setup = True

    def __init__(self, window):
        self.window = window
        self._written: set[str] = set()  # the keys of the files this backend saved
        self._started: Path | None = None  # the run folder this backend started last
        #: the open of a run folder that is waiting for its read: (folder, Deferred)
        self._pending_open: tuple[Path, Deferred] | None = None
        #: True from the start of a run until its end: a failure is then the run's
        self._run_ends_pending = False
        #: (folder, message) of the last run that failed
        self._failure: tuple[Path, str] | None = None
        run = window.run
        run.started.connect(self._on_run_started)
        run.finished.connect(self._on_run_finished)
        run.failed.connect(self._on_run_failed)

    # --- the dispatch -----------------------------------------------------------------

    def handle(self, method: str, params: dict):
        """Answer one request: the result (JSON data), or a :class:`Deferred` for a later one.

        Raises :class:`BackendError` for an unknown method or bad arguments, and whatever the
        method raises (the server answers that as an internal error).
        """
        if method not in SERVED:
            raise BackendError(f"unknown method {method!r}")
        if not isinstance(params, dict):
            raise BackendError(f"{method}: the arguments must be an object")
        call = getattr(self, method)
        try:
            inspect.signature(call).bind(**params)
        except TypeError as error:
            raise BackendError(f"{method}: {error}") from None
        return call(**params)

    # --- the setup --------------------------------------------------------------------

    def get_setup(self) -> dict:
        document = self.window.document
        setup = clean(document.doc)
        setup["scan"] = deepcopy(document.doc.get("_scan"))
        found = problems(document.doc, self._base_dir())
        return {
            "path": str(document.path) if document.path is not None else None,
            "setup": setup,
            "problems": [{"text": p.text, "blocking": p.blocking} for p in found],
            "modified": document.is_modified,
        }

    def has_unsaved_changes(self) -> bool:
        return self.window.document.is_modified

    def set_values(self, assignments: list[str]) -> dict:
        if not isinstance(assignments, list) or not all(isinstance(a, str) for a in assignments):
            raise BackendError("assignments must be a list of dotted.key=value strings")
        return self._edit(
            "set " + ", ".join(assignments),
            lambda doc: rules.with_values(doc, assignments),
        )

    def set_section(self, name: str, value: Any) -> dict:
        return self._edit(f"set {name}", lambda doc: rules.with_section(doc, name, value))

    def new_setup(self) -> dict:
        self._refuse_while_busy()
        self.window.document.new()
        return self.get_setup()

    def open_setup(self, path: str) -> dict:
        self._refuse_while_busy()
        target = self._resolve(path)
        try:
            self.window.open_setup_path(target)
        except SpecError as error:
            raise BackendError(str(error)) from error
        except OSError as error:
            raise BackendError(f"cannot open {target}: {error}") from error
        return self.get_setup()

    def save_setup(self, path: str | None = None) -> dict:
        document = self.window.document
        if path is None:
            if document.path is None:
                raise BackendError("the setup has no file yet: give a path to save it to")
            target = document.path
        else:
            target = self._resolve(path)
        if document.in_run_folder and target == document.path:
            raise BackendError(
                "the setup is a run folder's and is not saved in place: give a path of its own"
            )
        self.window._commit_pending()
        try:
            self.window.save_setup_path(target)
        except (SpecError, OSError) as error:
            raise BackendError(str(error)) from error
        self._written.add(rules._key(target))
        return self.get_setup()

    def would_replace(self, path: str) -> bool:
        target = self._resolve(path)
        # a link is never trusted: see HeadlessBackend.would_replace
        if os.path.islink(target):
            return True
        document = self.window.document
        if document.path is not None and rules._key(target) == rules._key(document.path):
            # the file the user opened: replacing it loses its unsaved edits, so it is asked
            # about while the document has them, until this backend has saved it once
            return (
                target.exists() and document.is_modified and rules._key(target) not in self._written
            )
        return target.exists() and rules._key(target) not in self._written

    # --- undo and redo (the agent's own steps only) -------------------------------------

    def undo(self) -> dict:
        self._refuse_while_busy()
        self.window._commit_pending()
        document = self.window.document
        _own_step(document.can_undo, document.undo_label, "undo")
        document.undo()
        return self.get_setup()

    def redo(self) -> dict:
        self._refuse_while_busy()
        self.window._commit_pending()
        document = self.window.document
        _own_step(document.can_redo, document.redo_label, "redo")
        document.redo()
        return self.get_setup()

    # --- runs -------------------------------------------------------------------------

    def estimate(self, workers: int | None = None, batch_ions: int | None = None) -> dict:
        scan, workers = rules.plan_setup(
            self.window.document.doc, self._base_dir(), workers, batch_ions, _cores()
        )
        return self._estimate(scan, workers)

    def start_run(self, workers: int | None = None, batch_ions: int | None = None) -> dict:
        self._refuse_while_busy()
        self.window._commit_pending()
        document = self.window.document
        if document.path is None or document.in_run_folder:
            raise BackendError("save the setup first: save_setup(path)")
        base = self._base_dir()
        scan, workers = rules.plan_setup(document.doc, base, workers, batch_ions, _cores())
        if document.is_modified:
            self.save_setup(None)  # the scan of the file as saved
            scan, workers = rules.plan_setup(document.doc, base, workers, batch_ions, _cores())
        try:
            self.window.start_run_with(ParallelChoice(workers=workers, batch_ions=batch_ions))
        except (SpecError, RuntimeError) as error:
            raise BackendError(str(error)) from error
        folder = self.window.run.folder
        self._started = folder
        return {"folder": str(folder), "estimate": self._estimate(scan, workers)}

    def run_status(self, folder: str | None = None) -> Deferred:
        """The state of a run folder (the latest run when None). The folder is read in a thread."""
        wanted = self._resolve(folder) if folder is not None else None
        snap = self._snapshot()
        return _in_thread(lambda: _status_at(_target(wanted, snap), snap), lambda status: status)

    def control_run(self, action: str, folder: str | None = None) -> Deferred:
        """Pause, resume or stop a run. The window's own run is controlled here; another run
        folder gets its control request in a thread (see :func:`srim.mcp.detached.send_control`)."""
        if action not in ACTIONS:
            raise BackendError(f"unknown action {action!r} (have: {', '.join(ACTIONS)})")
        wanted = self._resolve(folder) if folder is not None else None
        snap = self._snapshot()

        def read() -> tuple[Path, bool]:
            target = _target(wanted, snap)
            if _is_open(target, snap):
                return target, True
            send_control(target, action)  # raises BackendError when the run is not going
            return target, False

        def then(result: tuple[Path, bool]) -> dict:
            target, is_open = result
            if is_open:
                run = self.window.run
                if not run.running:
                    status = self._app_status(target)["status"]
                    raise BackendError(f"the run is {status}; there is nothing to {action}")
                if action == "stop":
                    run.stop()
                else:
                    # the toggled signal of the action pauses or resumes the run
                    self.window.pause_action.setChecked(action == "pause")
            return {"folder": str(target), "requested": action}

        return _in_thread(read, then)

    def list_runs(self, setup_path: str | None = None) -> Deferred:
        """The run folders of a setup, newest first, read in a thread."""
        snap = replace(self._snapshot(), runs_dir=self._runs_dir(setup_path))
        return _in_thread(
            lambda: [{"folder": str(p), "status": status} for p, status in _listed(snap)],
            lambda rows: rows,
        )

    def open_run(self, folder: str) -> Deferred:
        run = self.window.run
        if run.running:
            raise BackendError(LOCKED_TEXT)
        target = self._resolve(folder)
        if not (target / "scan.yaml").is_file():
            raise BackendError(f"{target} has no scan.yaml: not a run folder")
        deferred = Deferred()
        self._settle_pending_open(BackendError(REPLACED_TEXT))
        self._pending_open = (target, deferred)
        deferred.on_cancel(lambda: self._forget_open(deferred))
        try:
            run.load(target)  # a failure that is known at once settles the Deferred here
        except RuntimeError as error:
            self._forget_open(deferred)
            raise BackendError(str(error)) from error
        return deferred

    # --- the Results tab ----------------------------------------------------------------

    def show_plot(self, folder: str | None, point: int | None, key: str) -> Deferred:
        """Show a plot of a run the window holds in the Results tab. The folder is found in a
        thread; the tab is changed here."""
        wanted = self._resolve(folder) if folder is not None else None
        snap = self._snapshot()

        def then(target: Path) -> dict:
            if not self._is_open_run(target):
                raise BackendError(
                    f"the run {target} is not open in the app: open it with open_run first"
                )
            rel = self._point_path(point)
            try:
                self.window.results.show_plot(rel, key)
            except ValueError as error:
                raise BackendError(str(error)) from error
            return {"shown": True}

        return _in_thread(lambda: _target(wanted, snap), then)

    # --- the status ---------------------------------------------------------------------

    def status(self) -> Deferred:
        """The doctor's checks and the latest run, both read in a thread."""
        route = self.window.settings.route
        checks_fn = self.window._checks_fn
        snap = self._snapshot()

        def read():
            checks = checks_fn(route)
            try:
                latest = _status_at(_target(None, snap), snap)
            except BackendError:  # no run yet: the status says so with None
                latest = None
            return checks, latest

        return _in_thread(
            read,
            lambda result: self._status_dict(*result),
            failed_text="could not check the installation",
        )

    # --- stubs until the confirm dialog (Task 5) and the activity panel (Task 6) --------------

    def ask(self, text: str, client: str = "") -> Deferred:
        """STUB (Task 5): the confirm dialog answers this. Until then nothing can be asked."""
        deferred = Deferred()
        deferred.reject(BackendError("asking the user in the app is not available yet"))
        return deferred

    def log(self, entry: dict) -> None:
        """STUB (Task 6): the Agent activity panel records the entry here. Until then, nothing."""
        return None

    # --- internals ----------------------------------------------------------------------

    def _edit(self, summary: str, change: Callable[[dict], dict]) -> dict:
        """Apply ``change`` to a copy of the setup, check it as the library does, and make it the
        setup in one undo step labelled with ``summary``. Nothing changes when it is refused."""
        self._refuse_while_busy()
        self.window._commit_pending()
        candidate = change(deepcopy(self.window.document.doc))
        rules._check(candidate, self._base_dir())
        replacement = deepcopy(candidate)

        def apply(doc: dict) -> None:
            doc.clear()
            doc.update(replacement)

        self.window.document.edit(apply, label=AGENT_PREFIX + summary)
        return self.get_setup()

    def _refuse_while_busy(self) -> None:
        run = self.window.run
        if run.running:
            raise BackendError(LOCKED_TEXT)
        if run.loading:
            raise BackendError("a run folder is being read: try again when it has opened")

    def _is_open_run(self, folder: Path) -> bool:
        """True when ``folder`` is the run the window holds (running, or loaded or finished)."""
        run = self.window.run
        return run.folder is not None and run.state is not None and _same(run.folder, folder)

    def _app_status(self, folder: Path) -> dict:
        """The state of the window's run, in the shape of :func:`srim.mcp.detached.run_state`."""
        run = self.window.run
        state = run.state
        errors = [text for level, text in state.warnings if level == "error"]
        failure = self._failure
        if run.running:
            status = "paused" if state.paused else "running"
        elif failure is not None and _same(failure[0], folder):
            status = "failed"
            errors.append(failure[1])
        elif state.finished:
            status = "stopped" if state.stopped else "finished"
        else:
            status = "crashed"  # a run that stopped without its end (a folder that did not finish)
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

    def _estimate(self, scan, workers: int) -> dict:
        return rules.estimate_of(scan, workers, self.window.settings.ions_per_s, _cores())

    def _snapshot(self) -> "_Snapshot":
        """The window's state that the folder reads need, taken in the GUI thread."""
        run = self.window.run
        open_folder = Path(run.folder) if run.folder is not None and run.state is not None else None
        document = self.window.document
        return _Snapshot(
            open_folder=open_folder,
            open_status=self._app_status(open_folder) if open_folder is not None else None,
            started=self._started,
            runs_dir=self._runs_dir(None) if document.path is not None else None,
        )

    def _status_dict(self, checks: list[dict], latest: dict | None) -> dict:
        """The answer of :meth:`status`: the checks, and the latest run's state (or None)."""
        problem = rules.srim_problem(checks)
        document = self.window.document
        return {
            "mode": "app",
            "version": srim.__version__,
            "checks": checks,
            "srim_ready": problem is None,
            "srim_problem": problem,
            "setup": str(document.path) if document.path is not None else None,
            "running": latest,
        }

    def _point_path(self, point: int | None) -> str | None:
        """The point's folder relative to the run folder (None for a single run's point)."""
        if point is None:
            return None
        run = self.window.run
        if run.result is not None:
            for row in rules.run_points(run.result):
                if row["index"] == point:
                    if row["path"] is None:
                        raise BackendError(f"point {point} has no results")
                    return row["path"]
        elif run.state is not None and point in run.state.points:
            return f"points/{run.state.points[point].id[:16]}"
        raise BackendError(f"the run has no point {point}")

    def _runs_dir(self, setup_path: str | None) -> Path:
        """The ``<stem>-runs`` folder of the setup at ``setup_path`` (the current one when None)."""
        setup = self._resolve(setup_path) if setup_path is not None else self.window.document.path
        if setup is None:
            raise BackendError("the setup has no file yet: save it, or give its setup_path")
        return setup.parent / f"{setup.stem}{RUNS_SUFFIX}"

    def _base_dir(self) -> Path:
        """The folder relative histogram files are read from: the setup's, else the working dir."""
        return self.window.document.base_dir or Path.cwd()

    def _resolve(self, path: str) -> Path:
        """``path`` as an absolute path, relative to the app's working directory."""
        return (Path.cwd() / Path(path).expanduser()).absolute()

    # --- the open of a run folder ---------------------------------------------------------

    def _settle_pending_open(self, error: BackendError) -> None:
        pending, self._pending_open = self._pending_open, None
        if pending is not None:
            pending[1].reject(error)

    def _forget_open(self, deferred: Deferred) -> None:
        if self._pending_open is not None and self._pending_open[1] is deferred:
            self._pending_open = None

    def _on_run_started(self) -> None:
        self._run_ends_pending = True
        self._failure = None
        self._settle_pending_open(BackendError(RUN_STARTED_TEXT))

    def _on_run_finished(self, result) -> None:
        self._run_ends_pending = False
        pending, self._pending_open = self._pending_open, None
        if pending is None:
            return
        target, deferred = pending
        run = self.window.run
        if not _same(run.folder, target):
            deferred.reject(BackendError(REPLACED_TEXT))  # another folder was read meanwhile
            return
        _settle(
            deferred,
            lambda: {
                "folder": str(target),
                "finished": not run.partial,
                "points": rules.run_points(result),
            },
        )

    def _on_run_failed(self, message: str) -> None:
        if self._run_ends_pending and self.window.run.folder is not None:
            self._failure = (self.window.run.folder, message)
        self._run_ends_pending = False
        self._settle_pending_open(BackendError(message))


# --- helpers ----------------------------------------------------------------------------------


@dataclass(frozen=True)
class _Snapshot:
    """What the folder reads of the window need, as the GUI thread saw it when they were asked:
    the window's own run (its folder and its state, when it has one), the run this backend
    started last, and the runs folder of the setup (None without a setup file)."""

    open_folder: Path | None
    open_status: dict | None
    started: Path | None
    runs_dir: Path | None


def _is_open(folder: Path, snap: _Snapshot) -> bool:
    return snap.open_folder is not None and _same(folder, snap.open_folder)


def _listed(snap: _Snapshot) -> list[tuple[Path, str]]:
    """The run folders of ``snap.runs_dir``, newest first, with their states (read in a thread)."""
    if snap.runs_dir is None or not snap.runs_dir.is_dir():
        return []
    folders = [p for p in snap.runs_dir.iterdir() if (p / "scan.yaml").is_file()]
    folders.sort(key=rules._run_order, reverse=True)
    rows = []
    for folder in folders:
        if _is_open(folder, snap):
            rows.append((folder, snap.open_status["status"]))
        else:
            rows.append((folder, rules._status_of(folder)))
    return rows


def _target(wanted: Path | None, snap: _Snapshot) -> Path:
    """``wanted``, or the latest run: the one this backend started, else the newest run of the
    setup that has a run file (as :class:`HeadlessBackend` picks it)."""
    if wanted is not None:
        return wanted
    if snap.runs_dir is None:
        raise BackendError("no run: start one with start_run()")
    if snap.started is not None and snap.started.parent == snap.runs_dir:
        return snap.started
    for folder, status in _listed(snap):
        if status != NOT_STARTED:
            return folder
    raise BackendError("no run: start one with start_run()")


def _status_at(folder: Path, snap: _Snapshot) -> dict:
    """The state of ``folder``: the window's run when it is that one, else its run file's."""
    if _is_open(folder, snap):
        return snap.open_status
    return run_state(folder)


def _in_thread(
    read: Callable[[], Any],
    then: Callable[[Any], Any],
    failed_text: str | None = None,
) -> Deferred:
    """Run ``read`` in a thread and answer with ``then(value)`` in the GUI thread.

    The Deferred always settles: with ``then``'s value, with the BackendError ``read`` or ``then``
    raised, or with ``failed_text`` and the error text for another error of ``read`` (an
    unexpected one is logged, and the agent gets the internal-error text).
    """
    deferred = Deferred()

    def done(value):
        _settle(deferred, lambda: then(value))

    def failed(error: BaseException):
        if isinstance(error, BackendError):
            deferred.reject(error)
        elif failed_text is not None:
            deferred.reject(BackendError(f"{failed_text}: {_text(error)}"))
        else:
            logger.error("an agent read failed", exc_info=error)
            deferred.reject(BackendError(INTERNAL_ERROR_TEXT))

    try:
        run_in_thread(read, done, failed)
    except Exception as error:  # the thread could not start: the Deferred still settles
        failed(error)
    return deferred


def _own_step(available: bool, label: str | None, verb: str) -> None:
    """Refuse the undo or redo unless its step (``label``, when ``available``) is the agent's.

    A user's step has no label, so it is refused like any other step that is not the agent's.
    Raises :class:`BackendError`.
    """
    if not available:
        raise BackendError(f"there is nothing to {verb}")
    if label is None or not label.startswith(AGENT_PREFIX):
        raise BackendError(
            f"the step that {verb} would act on is yours, not the agent's: the agent does not "
            f"{verb} it, as that would lose your edit"
        )


def _settle(deferred: Deferred, make: Callable[[], Any]) -> None:
    """Resolve ``deferred`` with ``make()``; reject it when that raises, so it always settles."""
    try:
        value = make()
    except BackendError as error:
        deferred.reject(error)
    except Exception:
        logger.exception("an agent answer failed")
        deferred.reject(BackendError(INTERNAL_ERROR_TEXT))
    else:
        deferred.resolve(value)


def _same(a: Path | None, b: Path | None) -> bool:
    return a is not None and b is not None and os.path.realpath(a) == os.path.realpath(b)


def _cores() -> int:
    return os.cpu_count() or 1


def _text(error: BaseException) -> str:
    return str(error).strip() or type(error).__name__
