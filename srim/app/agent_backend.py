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
#: the run state of a run folder that is not one this window ran (see :meth:`_listed_status`)
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
        own = self.window.document.path
        if own is not None and rules._key(target) == rules._key(own):
            return False
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

    def run_status(self, folder: str | None = None) -> dict:
        return self._status_of(self._run_folder(folder))

    def control_run(self, action: str, folder: str | None = None) -> dict:
        if action not in ACTIONS:
            raise BackendError(f"unknown action {action!r} (have: {', '.join(ACTIONS)})")
        target = self._run_folder(folder)
        run = self.window.run
        if self._is_open_run(target):
            if not run.running:
                status = self._app_status(target)["status"]
                raise BackendError(f"the run is {status}; there is nothing to {action}")
            if action == "stop":
                run.stop()
            else:
                # the toggled signal of the action pauses or resumes the run
                self.window.pause_action.setChecked(action == "pause")
        else:
            send_control(target, action)
        return {"folder": str(target), "requested": action}

    def list_runs(self, setup_path: str | None = None) -> list[dict]:
        runs = self._runs_dir(setup_path)
        if not runs.is_dir():
            return []
        folders = [p for p in runs.iterdir() if (p / "scan.yaml").is_file()]
        folders.sort(key=rules._run_order, reverse=True)
        return [{"folder": str(p), "status": self._listed_status(p)} for p in folders]

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

    def show_plot(self, folder: str | None, point: int | None, key: str) -> dict:
        target = self._run_folder(folder)
        if not self._is_open_run(target):
            raise BackendError(f"the run {target} is not open in the app: open it with open_run")
        rel = self._point_path(point)
        try:
            self.window.results.show_plot(rel, key)
        except ValueError as error:
            raise BackendError(str(error)) from error
        return {"shown": True}

    # --- the status ---------------------------------------------------------------------

    def status(self) -> Deferred:
        route = self.window.settings.route
        checks_fn = self.window._checks_fn
        deferred = Deferred()

        def done(checks):
            _settle(deferred, lambda: self._status_of_checks(checks))

        def failed(error):
            deferred.reject(BackendError(f"could not check the installation: {_text(error)}"))

        try:
            run_in_thread(lambda: checks_fn(route), done, failed)
        except Exception as error:  # the thread could not start: the Deferred still settles
            failed(error)
        return deferred

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

    def _status_of(self, folder: Path) -> dict:
        """The state of a run folder: the window's run when it is that one, else its runner's."""
        if self._is_open_run(folder):
            return self._app_status(folder)
        return run_state(folder)

    def _listed_status(self, folder: Path) -> str:
        if self._is_open_run(folder):
            return self._app_status(folder)["status"]
        return rules._status_of(folder)

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

    def _status_of_checks(self, checks: list[dict]) -> dict:
        problem = rules.srim_problem(checks)
        document = self.window.document
        try:
            running = self.run_status(None)
        except BackendError:  # no run yet: the status says so with None
            running = None
        return {
            "mode": "app",
            "version": srim.__version__,
            "checks": checks,
            "srim_ready": problem is None,
            "srim_problem": problem,
            "setup": str(document.path) if document.path is not None else None,
            "running": running,
        }

    def _run_folder(self, folder: str | None) -> Path:
        """``folder`` as a path; None is the latest run this backend started, else the newest run
        of the setup (as :class:`HeadlessBackend` picks it)."""
        if folder is not None:
            return self._resolve(folder)
        if self.window.document.path is not None:
            started = self._started
            # a run started for another setup (opened or saved since) is not this setup's run
            if started is not None and started.parent == self._runs_dir(None):
                return started
            for run in self.list_runs():
                if run["status"] != NOT_STARTED:
                    return Path(run["folder"])
        raise BackendError("no run: start one with start_run()")

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
