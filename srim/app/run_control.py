"""Starting, pausing and stopping a run of the app's setup.

:class:`RunController` runs the setup's scan (:func:`srim.app.runs.plan_run`) with
:func:`srim.scan.run_scan` in a thread (:func:`srim.app.workers.run_in_thread`), so the window
stays responsive. The scan's events go from the scan's thread into a queue, and a timer in the GUI
thread drains the queue into the scan's state (:class:`srim.ui.tui.state.ScanState`): nothing Qt
is touched from the scan's thread.

While a run goes the document is locked (:attr:`DocumentModel.locked`). The run ends in the GUI
thread, after the scan has returned, with ``finished`` (its result) or ``failed`` (a readable
message). Each worker's rate is remembered (:func:`srim.app.runs.remember_rate`) and the settings
are saved once, at the end.

Stop sets the scan's stop event: the scan kills its running points and keeps the finished ones.
Pause sets the scan's pause event: no new point starts until it is cleared.

:meth:`RunController.load` opens a run folder: its scan is read in a thread, then its setup is
opened into the document and its result and state are shown as the end of a run would show them
(``finished``). A folder that did not finish shows the points that have outputs (``partial``).
"""

import queue
import threading
from copy import deepcopy
from datetime import UTC, datetime
from pathlib import Path

from PySide6.QtCore import QObject, QTimer, Signal

from srim.app.recent import RUN, RecentEntry, entry_problem
from srim.app.runs import (
    UNFINISHED_TEXT,
    ParallelChoice,
    loaded_state,
    plan_run,
    read_run,
    remember_rate,
    run_folder,
)
from srim.app.settings import save_settings
from srim.app.workers import run_in_thread
from srim.scan import run_scan
from srim.scan.events import WorkerStats
from srim.scan.executor import ScanBusy
from srim.spec import SpecError
from srim.ui.tui.state import ScanState

#: how often the GUI thread takes the scan's events (milliseconds)
DRAIN_MS = 200


def backend_for(route: str | None, choice: ParallelChoice, runner) -> str:
    """The scan's back end: ``container`` for the container route or a container choice, else
    ``local``. A test runner always runs locally (the container runs pysrim2 itself)."""
    if runner is None and (route == "container" or choice.container):
        return "container"
    return "local"


def _text(error: BaseException) -> str:
    return str(error).strip() or type(error).__name__


def _message(error: BaseException) -> str:
    text = _text(error)
    if isinstance(error, ScanBusy):
        return f"The run did not start: {text}"
    return f"The run did not finish: {text}"


class RunController(QObject):
    """One run at a time of the window's setup. See the module docstring."""

    #: the run has started (the document is locked from now on)
    started = Signal()
    #: the state changed (after each batch of events, and at the end)
    updated = Signal()
    #: the run ended; the argument is the scan's :class:`~srim.scan.ScanResult`
    finished = Signal(object)
    #: the run ended without a result; the argument is a readable message
    failed = Signal(str)
    #: a note for the status bar (for example, the rates could not be saved)
    notice = Signal(str)

    def __init__(self, window, runner=None):
        super().__init__(window)
        self._window = window
        self._runner = runner
        self._running = False
        self.state: ScanState | None = None
        self.folder = None
        self.snapshot: dict | None = None
        self.result = None
        self._calculation = 1
        self._events: queue.Queue = queue.Queue()
        self._stop_event = threading.Event()
        self._pause_event = threading.Event()
        self._ended = threading.Event()
        self._ended.set()  # no run has started yet
        #: True while a run folder is read (see :meth:`load`)
        self.loading = False
        #: True when the loaded run did not finish (its result is the points with outputs)
        self.partial = False
        self._load_generation = 0
        self._timer = QTimer(self)
        self._timer.setInterval(DRAIN_MS)
        self._timer.timeout.connect(self._drain)

    @property
    def running(self) -> bool:
        """True from the start until the run's end has been handled in the GUI thread."""
        return self._running

    # -- commands (GUI thread) ----------------------------------------------------------

    def start(self, choice: ParallelChoice) -> None:
        """Run the window's setup as ``choice`` says. The setup must have a path (the window
        saves it first). Raises :class:`~srim.spec.SpecError` for a setup the library refuses;
        nothing starts then."""
        if self._running:
            raise RuntimeError("a run is already going")
        document = self._window.document
        if document.path is None:
            raise SpecError("save the setup before running it")
        snapshot = deepcopy(document.doc)
        scan = plan_run(snapshot, document.base_dir, choice)
        folder = run_folder(document.path, datetime.now(UTC))
        backend = backend_for(self._window.settings.route, choice, self._runner)

        self._load_generation += 1  # a folder still being read is no longer wanted
        self.loading = False
        self.state = ScanState(scan)
        self.folder = folder
        self.snapshot = snapshot
        self.result = None
        self.partial = False
        self._calculation = scan.base.calculation
        self._events = queue.Queue()
        self._stop_event = threading.Event()
        self._pause_event = threading.Event()
        self._ended = threading.Event()
        self._running = True
        document.locked = True
        self._timer.start()
        self.started.emit()

        events, stop, pause, ended = self._events, self._stop_event, self._pause_event, self._ended
        runner, workers = self._runner, choice.workers

        def work():
            try:
                return run_scan(
                    scan,
                    workers=workers,
                    backend=backend,
                    scan_dir=folder,
                    on_event=events.put,
                    stop_event=stop,
                    pause_event=pause,
                    runner=runner,
                )
            finally:
                ended.set()

        run_in_thread(work, self._done, self._failed)

    def load(self, folder) -> None:
        """Open the run folder ``folder``: its setup goes into the document (``document.open`` of
        its ``scan.yaml``), and its result and state are shown as after a run.

        The folder is read in a thread, so the GUI thread only takes the result. ``finished``
        is emitted with the result when the folder was read and its setup opened; ``failed``
        with the reason when not (a folder that is not a run, or a setup that cannot be read,
        leaves the window as it was). A folder without ``result/scan.csv`` (a run that stopped
        or crashed) is shown with its points that have outputs, and ``notice`` says so.
        Raises :class:`RuntimeError` while a run goes.
        """
        if self._running:
            raise RuntimeError("a run is already going")
        self._load_generation += 1
        generation = self._load_generation
        self.loading = False
        folder = Path(folder)
        problem = entry_problem(RecentEntry(str(folder), RUN, ""))
        if problem is not None:
            self.failed.emit(f"The run did not open: {problem}")
            return
        self.loading = True

        def read():
            result, finished = read_run(folder)
            return result, finished, loaded_state(result, folder)

        run_in_thread(
            read,
            lambda outcome: self._loaded(generation, folder, *outcome),
            lambda error: self._load_failed(generation, error),
        )

    def pause(self, paused: bool) -> None:
        """Hold back (or let go) the points that have not started. Nothing happens when no run
        goes."""
        if not self._running:
            return
        if paused:
            self._pause_event.set()
        else:
            self._pause_event.clear()

    def stop(self) -> None:
        """Stop the run: its running points are killed, the finished ones are kept."""
        if self._running:
            self._stop_event.set()

    def wait(self, msecs: int) -> bool:
        """Wait up to ``msecs`` for the run's thread to end; True when it has ended (or none ran).

        Used when the window closes during a run, after :meth:`stop`. The run's end is still
        handled through ``finished`` or ``failed`` when the event loop runs again.
        """
        return self._ended.wait(msecs / 1000)

    # -- the run's end and its events (GUI thread) ------------------------------------

    def _is_current_load(self, generation: int) -> bool:
        # A late read is ignored when a newer load or a run has started, or the window closed.
        return (
            generation == self._load_generation and not self._running and not self._window._closing
        )

    def _loaded(self, generation: int, folder: Path, result, finished: bool, state) -> None:
        if not self._is_current_load(generation):
            return
        self.loading = False
        document = self._window.document
        try:
            document.open(folder / "scan.yaml")
        except (SpecError, OSError) as error:
            self.failed.emit(f"The run did not open: {error}")
            return
        self.folder = folder
        self.result = result
        self.state = state
        self.snapshot = deepcopy(document.doc)
        self.partial = not finished
        if not finished:
            self.notice.emit(UNFINISHED_TEXT)
        self.updated.emit()
        self.finished.emit(result)

    def _load_failed(self, generation: int, error: BaseException) -> None:
        if not self._is_current_load(generation):
            return
        self.loading = False
        self.failed.emit(f"The run did not open: {_text(error)}")

    def _done(self, result) -> None:
        self._end(result=result)

    def _failed(self, error: BaseException) -> None:
        self._end(error=_message(error))

    def _end(self, result=None, error: str | None = None) -> None:
        self._drain()  # every event the run put before it ended
        self._timer.stop()
        self._running = False
        self._window.document.locked = False
        self.result = result
        try:
            save_settings(self._window.settings)
        except OSError as problem:
            self.notice.emit(f"Could not save the run rates: {problem}")
        self.updated.emit()
        if error is None:
            self.finished.emit(result)
        else:
            self.failed.emit(error)

    def _drain(self) -> None:
        while True:
            try:
                event = self._events.get_nowait()
            except queue.Empty:
                break
            self.state.apply(event)
            if isinstance(event, WorkerStats):
                remember_rate(self._window.settings, self._calculation, event.ions_per_s)
        self.updated.emit()
