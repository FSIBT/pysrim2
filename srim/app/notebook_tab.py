"""The Notebook tab: a marimo or Jupyter notebook of the run folder, shown inside the app.

:class:`NotebookTab` creates the notebook (:func:`srim.app.notebooks.create_notebook`), starts its
server in a thread (:func:`srim.app.workers.run_in_thread`) and loads the server's address into a
web view on the GUI thread. A banner says when the run folder has results that the notebook has
not loaded yet (the counts are read in a thread every ``settings.refresh_seconds``, only while the
tab is visible). Reload touches the reload file for marimo; Jupyter loads new results when its
loading cell runs again, so Reload says so.

Export PDF… (the button here and the Notebook menu) is connected by the window, which runs
:func:`srim.app.notebooks.export_pdf` in a thread and reports the result in the status bar.

This module is the Qt side of the notebook support. The server and the files are in
:mod:`srim.app.notebooks` (Qt-free). :func:`webengine_available` lives here because it asks about
a Qt module.
"""

import importlib.util
from pathlib import Path

from PySide6.QtCore import QTimer, QUrl, Signal
from PySide6.QtGui import QDesktopServices
from PySide6.QtWidgets import QHBoxLayout, QLabel, QPushButton, QVBoxLayout, QWidget

from srim.app.notebooks import (
    REINSTALL_TEXT,
    NotebookServer,
    create_notebook,
    tool_problem,
)
from srim.app.workers import run_in_thread
from srim.notebook import NOTEBOOK_DIR, RELOAD_FILE, loaded_count, results_count

NEW_RESULTS_TEXT = "New results since the notebook loaded them."
JUPYTER_RELOAD_TEXT = "Run the first cell again to load them."
NO_FOLDER_TEXT = "Run the setup or open a run folder first"
REPLACE_TEXT = "A notebook is open. Stop it and start this one?"
STARTING_TEXT = "Starting the notebook…"
RELOAD_FAILED_TEXT = "Could not reload the notebook: "
START_FAILED_TEXT = "The notebook did not start: "
EXPORT_NO_NOTEBOOK_TEXT = "Start a notebook first"
EXPORTING_TEXT = "Exporting the notebook to PDF…"
EXPORTED_TEXT = "Exported the notebook to "
EXPORT_FAILED_TEXT = "The PDF export failed: "


def webengine_available() -> bool:
    """True when PySide6's WebEngine module is installed (the view of the tab needs it)."""
    return importlib.util.find_spec("PySide6.QtWebEngineWidgets") is not None


def _new_webengine_view():
    """The view the notebook is shown in. The import is here, so the module loads without it."""
    from PySide6.QtWebEngineWidgets import QWebEngineView

    return QWebEngineView()


def start_problem(kind: str, folder) -> str | None:
    """Why a notebook of ``kind`` cannot start in ``folder`` (the reason text), or None.

    Without a run folder the reason says so; a missing tool or a missing WebEngine gives
    :data:`~srim.app.notebooks.REINSTALL_TEXT`.
    """
    if folder is None:
        return NO_FOLDER_TEXT
    problem = tool_problem(kind)
    if problem is not None:
        return problem
    if not webengine_available():
        return REINSTALL_TEXT
    return None


def _text(error: BaseException) -> str:
    return str(error).strip() or type(error).__name__


class NotebookTab(QWidget):
    """One notebook of a run folder, with its server. See the module docstring."""

    #: the notebook is shown (its server answers)
    started = Signal()
    #: the notebook was stopped (by the user, by the window, or after a failed start)
    stopped = Signal()
    #: the notebook did not start; the argument is a readable message with the server's log
    failed = Signal(str)

    def __init__(self, window, view_factory=None):
        super().__init__(window)
        self._window = window
        self.kind: str | None = None
        self.folder: Path | None = None
        self.server: NotebookServer | None = None
        self._generation = 0
        self._active = False
        self._ready = False
        self._counting = False
        self._pending: bool | None = None

        self._timer = QTimer(self)
        self._timer.setInterval(window.settings.refresh_seconds * 1000)
        self._timer.timeout.connect(self._poll)

        self.reload_button = QPushButton("Reload")
        self.reload_button.clicked.connect(self.reload)
        self.browser_button = QPushButton("Open in browser")
        self.browser_button.clicked.connect(self.open_in_browser)
        self.pdf_button = QPushButton("Export PDF…")
        self.pdf_button.setEnabled(False)
        self.stop_button = QPushButton("Stop notebook")
        self.stop_button.clicked.connect(self.stop)

        toolbar = QHBoxLayout()
        for button in (self.reload_button, self.browser_button, self.pdf_button):
            toolbar.addWidget(button)
        toolbar.addStretch(1)
        toolbar.addWidget(self.stop_button)

        self.banner = QLabel()
        self.banner.setWordWrap(True)
        self.banner.hide()

        make_view = view_factory or _new_webengine_view
        self.view = make_view()

        layout = QVBoxLayout(self)
        layout.addLayout(toolbar)
        layout.addWidget(self.banner)
        layout.addWidget(self.view, 1)

    # --- commands (GUI thread) ----------------------------------------------------------

    def start(self, kind: str, folder) -> None:
        """Create the notebook of ``kind`` in ``folder`` and start its server in a thread.

        The view shows the server's address once it answers. A failure (the file cannot be
        written, the server exits or does not answer) emits ``failed`` with the log's end, and
        ``stopped`` before it; the tab is then stopped.
        """
        if self._active:
            self.stop()
        self.kind = kind
        self.folder = Path(folder)
        self._generation += 1
        generation = self._generation
        self._active = True
        self._ready = False
        self._pending = None
        self.banner.setText(STARTING_TEXT)
        self.banner.show()
        try:
            path = create_notebook(self.folder, kind)
        except OSError as error:
            self._start_failed(generation, error)
            return
        server = NotebookServer(kind, path)
        self.server = server

        def boot() -> None:
            server.start()
            server.wait_ready()

        run_in_thread(
            boot,
            lambda _value: self._ready_now(generation),
            lambda error: self._start_failed(generation, error),
        )

    def stop(self) -> None:
        """Stop the server and forget the tab's running state. Safe to call more than once;
        late results of a start or a count are ignored from now on."""
        self._generation += 1
        self._timer.stop()
        self._ready = False
        if self.server is not None:
            self.server.stop()
        if self._active:
            self._active = False
            self.stopped.emit()

    def reload(self) -> None:
        """Marimo: touch the reload file, so the notebook loads the new results. Jupyter: the
        first cell has to run again, so the banner says so."""
        if not self._ready or self.folder is None:
            return
        if self.kind == "marimo":
            try:
                (self.folder / NOTEBOOK_DIR / RELOAD_FILE).touch(exist_ok=True)
            except OSError as error:
                self.banner.setText(f"{RELOAD_FAILED_TEXT}{error}")
                self.banner.show()
                return
            # The notebook loads the results now: the banner goes, and the next count shows it
            # again only if the results are still not loaded.
            self.banner.hide()
            self._pending = None
            return
        self.banner.setText(JUPYTER_RELOAD_TEXT)
        self.banner.show()

    def open_in_browser(self) -> None:
        """Open the notebook's address (token included) in the default browser."""
        if self.server is not None and self.server.url:
            QDesktopServices.openUrl(QUrl(self.server.url))

    # --- results (GUI thread) -------------------------------------------------------------

    def showEvent(self, event):  # noqa: N802 (Qt's name)
        super().showEvent(event)
        self._update_timer()

    def hideEvent(self, event):  # noqa: N802 (Qt's name)
        super().hideEvent(event)
        self._update_timer()

    def _update_timer(self) -> None:
        """The banner is counted only while the tab is visible and its notebook runs."""
        if self._ready and self._active and self.isVisible():
            if not self._timer.isActive():
                self._timer.start()
                self._poll()
        else:
            self._timer.stop()

    def _ready_now(self, generation: int) -> None:
        if generation != self._generation or not self._active:
            return
        self._ready = True
        self.view.setUrl(QUrl(self.server.url))
        self.banner.hide()
        self._update_timer()
        self.started.emit()

    def _start_failed(self, generation: int, error: BaseException) -> None:
        if generation != self._generation:
            return
        self.stop()
        self.failed.emit(f"{START_FAILED_TEXT}{_text(error)}")

    def _poll(self) -> None:
        """Count the run folder's results in a thread (one count at a time)."""
        if self._counting or not self._active or self.folder is None:
            return
        self._counting = True
        generation = self._generation
        folder = self.folder
        run_in_thread(
            lambda: (results_count(folder), loaded_count(folder)),
            lambda counts: self._counted(generation, counts),
            lambda _error: self._counted(generation, None),
        )

    def _counted(self, generation: int, counts) -> None:
        # Only plain attributes are touched before the generation check: a late result may come
        # after this tab was stopped and deleted.
        self._counting = False
        if generation != self._generation or counts is None or not self._ready:
            return
        results, loaded = counts
        # None: the notebook has not loaded this folder yet, so nothing is new to it
        pending = loaded is not None and results > loaded
        if pending == self._pending:
            return  # a reload message stays until the count changes
        self._pending = pending
        if not pending:
            self.banner.hide()
            return
        text = NEW_RESULTS_TEXT
        if self.kind == "jupyter":
            text = f"{text} {JUPYTER_RELOAD_TEXT}"
        self.banner.setText(text)
        self.banner.show()
