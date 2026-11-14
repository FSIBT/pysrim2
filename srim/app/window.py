"""The main window: the run tabs, the menus, the run setup and the guidance status bar."""

import os
from collections.abc import Callable
from pathlib import Path

from PySide6.QtCore import QUrl, Signal
from PySide6.QtGui import QActionGroup, QDesktopServices, QKeySequence
from PySide6.QtWidgets import (
    QApplication,
    QDialog,
    QFileDialog,
    QMainWindow,
    QMessageBox,
    QTabWidget,
    QWidget,
)

import srim
from srim.app import DOCS_URL
from srim.app.beam_tab import BeamTab
from srim.app.document import DocumentModel
from srim.app.geometry import GeometryTab
from srim.app.guidance import Guidance, setup_guidance
from srim.app.notebook_tab import REPLACE_TEXT, NotebookTab, start_problem
from srim.app.parallel_dialog import ParallelDialog
from srim.app.progress_tab import ProgressTab
from srim.app.recent import RUN, SETUP, add_recent, entry_problem, load_recent, new_entry
from srim.app.results_tab import ResultsTab
from srim.app.run_control import RunController
from srim.app.run_settings import RunSettingsTab
from srim.app.runs import (
    MISSING_OUTPUTS_TEXT,
    ParallelChoice,
    outputs_missing,
    parallel_advice,
    plan_run,
    run_samples,
    setup_path_for_run,
)
from srim.app.save_dialog import SaveResultsDialog
from srim.app.scan_tab import ScanTab
from srim.app.settings import AppSettings, save_settings
from srim.app.setup import SetupWizard
from srim.app.statusbar import GuidanceBar
from srim.app.theme import THEMES, apply_theme
from srim.app.workers import run_in_thread
from srim.cli import collect_checks
from srim.installer.container import find_runtime
from srim.spec import SpecError

#: the editor tabs, by key (the first part of a guidance target) and tab name
EDITOR_TABS = (
    ("geometry", "Geometry"),
    ("beam", "Beam"),
    ("settings", "Run settings"),
    ("scan", "Scan"),
)
#: the question Quit asks while a run goes
STOP_AND_QUIT_TEXT = "A run is going. Stop it and quit?"
#: how long Quit waits for a stopped run to end before the window closes anyway
QUIT_WAIT_MS = 30_000
THEME_LABELS = {"system": "&System", "light": "&Light", "dark": "&Dark"}
SETUP_TARGET = "setup"
FILE_FILTER = "Run setup (*.yaml *.yml)"
DISCARD_TEXT = "Discard the changes to this setup?"


class MainWindow(QMainWindow):
    checked = Signal(list)
    setup_requested = Signal()

    def __init__(
        self,
        settings: AppSettings,
        checks_fn: Callable[[str | None], list[dict]] = collect_checks,
        parent=None,
        document: DocumentModel | None = None,
        run_runner=None,
    ):
        super().__init__(parent)
        self.settings = settings
        self._checks_fn = checks_fn
        self._closing = False
        self._setup_open = False
        self._generation = 0
        #: bumped by each change of the setup and each run's end: a late sampled read is ignored
        self._samples_token = 0
        self._setup_messages: list[Guidance] = []
        #: the notebook tab while a notebook runs or is starting (None otherwise)
        self.notebook_tab: NotebookTab | None = None
        self.document = document if document is not None else DocumentModel(self)
        #: the run of the setup (``run_runner`` replaces SRIM in tests; see srim.app.run_control)
        self.run = RunController(self, runner=run_runner)

        # Editors are keyed "geometry", "beam", "settings" and "scan".
        self.editors: dict[str, QWidget] = {}
        self.tabs = QTabWidget(self)
        for key, name in EDITOR_TABS:
            if key == "geometry":
                editor = GeometryTab(self.document, self)
            elif key == "beam":
                editor = BeamTab(self.document, self)
            elif key == "settings":
                editor = RunSettingsTab(self.document, self)
            else:
                editor = ScanTab(self.document, self)
            self.editors[key] = editor
            self.tabs.addTab(editor, name)
        self.setCentralWidget(self.tabs)

        self.status = GuidanceBar(self)
        self.setStatusBar(self.status)
        self.status.jump.connect(self._on_jump)

        self.theme_actions: dict[str, object] = {}
        self._build_menus()
        self._refresh_recent_menu()
        # The Progress tab shows the run and uses the Run actions as its buttons.
        self.progress = ProgressTab(self.run, self.pause_action, self.stop_action, self)
        self.results = ResultsTab(self.run, self.document, self.settings, self)
        self.tabs.addTab(self.progress, "Progress")
        self.tabs.addTab(self.results, "Results")

        self.run.started.connect(self._on_run_started)
        self.run.finished.connect(self._on_run_ended)
        self.run.failed.connect(self._on_run_failed)
        self.run.notice.connect(lambda text: self._set_setup_messages([Guidance(text)]))

        self.document.changed.connect(self._on_document_changed)
        self._on_document_changed()

        hints = QApplication.instance().styleHints()
        hints.colorSchemeChanged.connect(self._on_scheme_changed)

    @property
    def closing(self) -> bool:
        """True once the window's close has been accepted (its late background results are
        ignored from then on)."""
        return self._closing

    # --- menus ----------------------------------------------------------------

    def _build_menus(self) -> None:
        menus = self.menuBar()

        file_menu = menus.addMenu("&File")
        self.new_action = file_menu.addAction("&New setup")
        self.new_action.setShortcut(QKeySequence("Ctrl+N"))
        self.new_action.triggered.connect(self.new_setup)
        self.open_action = file_menu.addAction("&Open setup…")
        self.open_action.setShortcut(QKeySequence("Ctrl+O"))
        self.open_action.triggered.connect(self.open_setup_file)
        self.recent_menu = file_menu.addMenu("Open &recent")
        self.recent_menu.aboutToShow.connect(self._refresh_recent_menu)
        self.open_run_action = file_menu.addAction("Open &run folder…")
        self.open_run_action.triggered.connect(self.open_run_folder)
        self.save_action = file_menu.addAction("&Save setup")
        self.save_action.setShortcut(QKeySequence("Ctrl+S"))
        self.save_action.triggered.connect(self.save_setup)
        self.save_as_action = file_menu.addAction("Save setup &as…")
        self.save_as_action.setShortcut(QKeySequence("Ctrl+Shift+S"))
        self.save_as_action.triggered.connect(self.save_setup_as)
        self.save_results_action = file_menu.addAction("Save &results…")
        self.save_results_action.triggered.connect(self.save_results)
        file_menu.addSeparator()
        self.quit_action = file_menu.addAction("&Quit")
        self.quit_action.setShortcut(QKeySequence("Ctrl+Q"))
        self.quit_action.triggered.connect(self.close)

        edit_menu = menus.addMenu("&Edit")
        self.undo_action = edit_menu.addAction("&Undo")
        self.undo_action.setShortcut(QKeySequence(QKeySequence.StandardKey.Undo))
        self.undo_action.triggered.connect(self._undo)
        self.redo_action = edit_menu.addAction("&Redo")
        self.redo_action.setShortcut(QKeySequence(QKeySequence.StandardKey.Redo))
        self.redo_action.triggered.connect(self._redo)

        setup_menu = menus.addMenu("&Setup")
        self.setup_action = setup_menu.addAction("Run setup…")
        self.setup_action.triggered.connect(self.open_setup)

        run_menu = menus.addMenu("&Run")
        self.start_action = run_menu.addAction("&Start")
        self.start_action.setShortcut(QKeySequence("Ctrl+R"))
        self.start_action.triggered.connect(self.start_run)
        self.pause_action = run_menu.addAction("&Pause")
        self.pause_action.setCheckable(True)
        self.pause_action.toggled.connect(self.run.pause)
        self.stop_action = run_menu.addAction("&Stop")
        self.stop_action.triggered.connect(self.run.stop)
        self.run_toolbar = self.addToolBar("Run")
        for action in (self.start_action, self.pause_action, self.stop_action):
            self.run_toolbar.addAction(action)

        notebook_menu = menus.addMenu("&Notebook")
        self.notebook_menu = notebook_menu
        self.start_marimo_action = notebook_menu.addAction("Start &marimo notebook")
        self.start_marimo_action.triggered.connect(
            lambda _checked=False: self.start_notebook("marimo")
        )
        self.start_jupyter_action = notebook_menu.addAction("Start &Jupyter notebook")
        self.start_jupyter_action.triggered.connect(
            lambda _checked=False: self.start_notebook("jupyter")
        )
        notebook_menu.addSeparator()
        # Connected when the export is built; until then it is greyed out.
        self.export_pdf_action = notebook_menu.addAction("Export &PDF…")
        self.export_pdf_action.setEnabled(False)
        notebook_menu.addSeparator()
        self.stop_notebook_action = notebook_menu.addAction("S&top notebook")
        self.stop_notebook_action.triggered.connect(self._stop_notebook)

        view_menu = menus.addMenu("&View")
        theme_menu = view_menu.addMenu("&Theme")
        group = QActionGroup(self)
        group.setExclusive(True)
        for choice in THEMES:
            action = theme_menu.addAction(THEME_LABELS[choice])
            action.setCheckable(True)
            action.setChecked(choice == self.settings.theme)
            action.setActionGroup(group)
            action.triggered.connect(lambda _checked=False, c=choice: self._choose_theme(c))
            self.theme_actions[choice] = action

        help_menu = menus.addMenu("&Help")
        self.documentation_action = help_menu.addAction("Documentation")
        self.documentation_action.triggered.connect(
            lambda: QDesktopServices.openUrl(QUrl(DOCS_URL))
        )
        self.about_action = help_menu.addAction("About pysrim2")
        self.about_action.triggered.connect(self._about)

    def _choose_theme(self, choice: str) -> None:
        self.settings.theme = choice
        apply_theme(QApplication.instance(), choice)
        try:
            save_settings(self.settings)
        except OSError as error:
            self._set_setup_messages([Guidance(f"Could not save the theme: {error}")])

    def _on_scheme_changed(self, *_args) -> None:
        if self.settings.theme == "system" and not self._closing:
            apply_theme(QApplication.instance(), "system")

    def _about(self) -> None:
        QMessageBox.about(self, "About pysrim2", f"pysrim2 {srim.__version__}")

    # --- the run setup -------------------------------------------------------------

    def _on_document_changed(self) -> None:
        self._samples_token += 1
        document = self.document
        name = document.path.name if document.path is not None else "Untitled"
        self.setWindowTitle(f"pysrim2 — {name}[*]")
        self.setWindowModified(document.is_modified)
        self._update_run_actions()
        self._show_status()

    def _busy(self) -> bool:
        """True while a run goes or a run folder is being read: the setup is not changed then."""
        return self.run.running or self.run.loading

    def _update_run_actions(self) -> None:
        """The Run actions, and the File and Edit actions and editors a run locks."""
        running = self.run.running
        busy = self._busy()
        document = self.document
        blocking = any(problem.blocking for problem in document.problems)
        self.start_action.setEnabled(not busy and not blocking)
        self.pause_action.setEnabled(running)
        self.stop_action.setEnabled(running)
        if not running and self.pause_action.isChecked():
            self.pause_action.setChecked(False)
        self.new_action.setEnabled(not busy)
        self.open_action.setEnabled(not busy)
        self.open_run_action.setEnabled(not busy)
        self.recent_menu.setEnabled(not busy)
        self.save_action.setEnabled(not busy)
        self.save_as_action.setEnabled(not busy)
        # A run folder that did not finish has no result/scan.csv: the export has nothing to read.
        # A finished run whose point folders are gone has nothing to export either, and says so.
        missing = outputs_missing(self.run.result, self.run.folder)
        self.save_results_action.setEnabled(
            not busy
            and self.run.result is not None
            and self.run.folder is not None
            and not self.run.partial
            and not missing
        )
        tip = MISSING_OUTPUTS_TEXT if missing else ""
        self.save_results_action.setToolTip(tip)
        self.save_results_action.setStatusTip(tip)
        self.undo_action.setEnabled(document.can_undo and not busy)
        self.redo_action.setEnabled(document.can_redo and not busy)
        for editor in self.editors.values():
            editor.setEnabled(not busy)
        self._update_notebook_actions()

    # --- notebooks -------------------------------------------------------------------

    def _update_notebook_actions(self) -> None:
        """The start entries say why they are greyed out (no run folder, a missing tool)."""
        for action, kind in (
            (self.start_marimo_action, "marimo"),
            (self.start_jupyter_action, "jupyter"),
        ):
            problem = start_problem(kind, self.run.folder)
            action.setEnabled(problem is None)
            action.setToolTip(problem or "")
            action.setStatusTip(problem or "")
        self.stop_notebook_action.setEnabled(self.notebook_tab is not None)

    def start_notebook(self, kind: str) -> None:
        """Start a notebook of ``kind`` in the loaded or running run folder, in a Notebook tab.

        A notebook that is already open is asked about first: Yes stops it, No keeps it.
        """
        if self._closing:
            return
        folder = self.run.folder
        problem = start_problem(kind, folder)
        if problem is not None:
            self._set_setup_messages([Guidance(problem)])
            return
        if self.notebook_tab is not None:
            buttons = QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No
            answer = QMessageBox.question(
                self, "Notebook", REPLACE_TEXT, buttons, QMessageBox.StandardButton.No
            )
            if answer != QMessageBox.StandardButton.Yes:
                return
            self._stop_notebook()
        tab = NotebookTab(self)
        tab.stopped.connect(lambda tab=tab: self._notebook_stopped(tab))
        tab.failed.connect(self._notebook_failed)
        self.notebook_tab = tab
        self.tabs.addTab(tab, "Notebook")
        self.tabs.setCurrentWidget(tab)
        self._update_notebook_actions()
        tab.start(kind, folder)

    def _stop_notebook(self) -> None:
        tab = self.notebook_tab
        if tab is None:
            return
        tab.stop()
        self._remove_notebook_tab()

    def _notebook_stopped(self, tab: NotebookTab) -> None:
        if tab is self.notebook_tab:
            self._remove_notebook_tab()

    def _notebook_failed(self, message: str) -> None:
        self._set_setup_messages([Guidance(message)])

    def _remove_notebook_tab(self) -> None:
        tab = self.notebook_tab
        if tab is None:
            return
        self.notebook_tab = None
        index = self.tabs.indexOf(tab)
        if index >= 0:
            self.tabs.removeTab(index)
        tab.deleteLater()
        self._update_notebook_actions()

    # --- recent setups and runs ----------------------------------------------------------

    def _refresh_recent_menu(self) -> None:
        """File > Open recent: the saved entries, newest first. An entry that cannot be opened
        now is greyed out, and its tooltip says why."""
        menu = self.recent_menu
        menu.clear()
        entries = load_recent()
        if not entries:
            menu.addAction("No recent setups or runs").setEnabled(False)
            return
        for entry in entries:
            problem = entry_problem(entry)
            path = Path(entry.path)
            action = menu.addAction(f"{path.parent.name}/{path.name}")
            action.setEnabled(problem is None and not self._busy())
            action.setToolTip(problem or entry.path)
            action.triggered.connect(lambda _checked=False, e=entry: self._open_recent(e))

    def _remember(self, path, kind: str) -> None:
        """Put a setup or a run folder at the front of the recent list (a file that cannot be
        written is said in the status bar, and the list is left as it was)."""
        try:
            add_recent(new_entry(path, kind))
        except OSError as error:
            self._set_setup_messages([Guidance(f"Could not save the recent items: {error}")])
        self._refresh_recent_menu()

    def _open_recent(self, entry) -> None:
        if entry.kind == RUN:
            self._open_run(entry.path)
        else:
            self._open_setup(entry.path)

    def open_run_folder(self) -> None:
        """File > Open run folder…: a finished or an unfinished run folder (RunController.load)."""
        self._commit_pending()
        start = str(self.document.base_dir) if self.document.base_dir else ""
        folder = QFileDialog.getExistingDirectory(self, "Open run folder", start)
        if folder:
            self._open_run(folder)

    def _open_run(self, folder) -> None:
        """Open a run folder: the unsaved edits are asked about first, then the folder is read."""
        if self._closing or self._busy():
            return
        self._commit_pending()
        if not self._may_discard():
            return
        self.run.load(folder)
        self._update_run_actions()

    # --- runs --------------------------------------------------------------------------

    def start_run(self) -> None:
        """Start a run of the setup. An unsaved or changed setup is saved first (Save as when it
        has no file yet); a long run is offered the parallel choice first."""
        if self.run.running or self._closing:
            return
        self._commit_pending()
        if self._needs_new_file() or self.document.is_modified:
            self.save_setup()
            if self._needs_new_file() or self.document.is_modified:
                return  # cancelled, or the save failed (and said so)
        choice = self._choose_parallel()
        if choice is None:
            return
        try:
            self.run.start(choice)
        except SpecError as error:
            QMessageBox.warning(self, "Run", str(error))

    def _choose_parallel(self) -> ParallelChoice | None:
        """The choice for the run: the dialog when the run is long enough to offer it, else one
        worker. None when the user cancels or the setup cannot be planned (and says why)."""
        document = self.document
        cores = os.cpu_count() or 1
        try:
            scan = plan_run(document.doc, document.base_dir, ParallelChoice())
            advice = parallel_advice(
                scan, cores, self.settings.ions_per_s, find_runtime() is not None
            )
        except SpecError as error:
            QMessageBox.warning(self, "Run", str(error))
            return None
        if advice is None:
            return ParallelChoice()
        ions = scan.ions if isinstance(scan.ions, int) else 0
        dialog = ParallelDialog(advice, cores=cores, ions=ions, parent=self)
        try:
            if dialog.exec() != QDialog.DialogCode.Accepted:
                return None
            return dialog.choice()
        finally:
            dialog.deleteLater()

    def _on_run_started(self) -> None:
        self._update_run_actions()
        self.tabs.setCurrentWidget(self.progress)

    def _on_run_ended(self, result=None) -> None:
        self._update_run_actions()
        # A finished exact run's sampled ions are read in a thread (they can be many) and drawn
        # over the Beam tab's densities; a late result is ignored if the setup changed meanwhile.
        self._samples_token += 1
        token = self._samples_token
        folder = self.run.folder
        if folder is not None:
            self._remember(folder, RUN)
        self.editors["beam"].show_run_samples(None)
        run_in_thread(
            lambda: run_samples(result, folder),
            lambda ions: self._samples_read(token, ions),
            lambda _error: self._samples_read(token, None),
        )

    def _samples_read(self, token: int, ions) -> None:
        if self._closing or token != self._samples_token:
            return
        self.editors["beam"].show_run_samples(ions)

    def _on_run_failed(self, message: str) -> None:
        self._update_run_actions()
        self._set_setup_messages([Guidance(message)])

    def _commit_pending(self) -> None:
        """Commit the value typed in the focused field before a command uses the document.

        A spin box or line edit commits when Enter is pressed or when it loses focus. A shortcut
        or a menu does not take the focus away, so a typed value would be lost. Clearing the focus
        makes the field commit: a spin box or line edit emits its signals on focus-out, and a
        table's cell editor is committed and closed by its delegate. Only a field of this window
        counts. The field does not get the focus back.
        """
        widget = QApplication.focusWidget()
        if widget is None or widget.window() is not self:
            return
        widget.clearFocus()

    def _undo(self) -> None:
        """Undo the last edit. A value typed and not yet committed is committed first, so it
        becomes an edit of its own and undo reverts it (not the edit before it)."""
        self._commit_pending()
        self.document.undo()

    def _redo(self) -> None:
        self._commit_pending()
        self.document.redo()

    def _may_discard(self) -> bool:
        """True when nothing unsaved would be lost, or the user agrees to discard the edits."""
        self._commit_pending()
        if not self.document.is_modified:
            return True
        buttons = QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No
        answer = QMessageBox.question(
            self, "pysrim2", DISCARD_TEXT, buttons, QMessageBox.StandardButton.No
        )
        return answer == QMessageBox.StandardButton.Yes

    def new_setup(self) -> None:
        if self._may_discard():
            self.document.new()

    def open_setup_file(self) -> None:
        self._commit_pending()
        folder = str(self.document.base_dir) if self.document.base_dir else ""
        path, _ = QFileDialog.getOpenFileName(self, "Open run setup", folder, FILE_FILTER)
        if not path:
            return  # cancelled: nothing is asked and nothing changes
        self._open_setup(path)

    def _open_setup(self, path) -> None:
        """Open the setup file at ``path`` (unsaved edits are asked about first)."""
        self._commit_pending()
        if not self._may_discard():
            return
        try:
            self.document.open(path)
        except (SpecError, OSError) as error:
            QMessageBox.warning(self, "Open setup", str(error))
            return
        self._remember_setup(path)

    def _needs_new_file(self) -> bool:
        """True when the setup has no file of its own: no path yet, or it is the ``scan.yaml`` of
        a run folder. Save and Start then ask for a file (Save as), so a run is never changed."""
        return self.document.path is None or self.document.in_run_folder

    def _remember_setup(self, path) -> None:
        """Record a setup in the recent list, unless it is a run folder's ``scan.yaml`` (the run
        is recorded as a run, when it ends)."""
        if not self.document.in_run_folder:
            self._remember(path, SETUP)

    def save_setup(self) -> None:
        self._commit_pending()
        if self._needs_new_file():
            self.save_setup_as()
            return
        self._save_to(self.document.path)

    def save_setup_as(self) -> None:
        self._commit_pending()
        if self.document.in_run_folder:
            start = str(setup_path_for_run(self.document.path))
        elif self.document.base_dir is not None:
            start = str(self.document.base_dir / "run.yaml")
        else:
            start = "run.yaml"
        path, _ = QFileDialog.getSaveFileName(self, "Save run setup", start, FILE_FILTER)
        if not path:
            return
        target = Path(path)
        if not target.suffix:
            target = target.with_suffix(".yaml")
        self._save_to(target)

    def save_results(self) -> None:
        """File > Save results…: the last finished run's results in the library's formats."""
        folder = self.run.folder
        if self.run.running or self._closing or folder is None or self.run.result is None:
            return
        try:
            dialog = SaveResultsDialog(Path(folder), parent=self)
        except (OSError, SpecError, ValueError) as error:
            QMessageBox.warning(self, "Save results", f"The results cannot be saved: {error}")
            return
        try:
            if dialog.exec() == QDialog.DialogCode.Accepted and dialog.message:
                self._set_setup_messages([Guidance(dialog.message)])
        finally:
            dialog.deleteLater()

    def _save_to(self, path: Path) -> None:
        try:
            self.document.save(path)
        except (SpecError, OSError) as error:
            QMessageBox.warning(self, "Save setup", str(error))
            return
        self._remember_setup(path)

    # --- setup and checks ----------------------------------------------------------

    def open_setup(self) -> None:
        """Run the setup wizard (modal), then check again; Accept also shows the Geometry tab.

        Cancel checks again too, because an install may have run inside the wizard before it was
        cancelled. Nothing happens while the window is closing or while a wizard is already open.
        """
        if self._closing or self._setup_open:
            return
        self.setup_requested.emit()
        self._setup_open = True
        try:
            wizard = SetupWizard(self.settings, checks_fn=self._checks_fn, parent=self)
            result = wizard.exec()
            wizard.deleteLater()
        finally:
            self._setup_open = False
        if self._closing:
            return
        if result == QDialog.DialogCode.Accepted:
            self.tabs.setCurrentIndex(0)
        self.refresh_checks()

    def _on_jump(self, target: str) -> None:
        """A click on a guidance line: the setup, or an editor tab and the field in it."""
        if target == SETUP_TARGET:
            self.open_setup()
            return
        head, _, rest = target.partition("/")
        editor = self.editors.get(head)
        if editor is None:
            return
        self.tabs.setCurrentWidget(editor)
        editor.focus_target(rest)

    def _set_setup_messages(self, messages: list[Guidance]) -> None:
        self._setup_messages = messages
        self._show_status()

    def _show_status(self) -> None:
        """The status bar: the setup's messages, then the blocking problems, then the notes."""
        problems = self.document.problems
        blocking = [problem for problem in problems if problem.blocking]
        notes = [problem for problem in problems if not problem.blocking]
        self.status.set_messages([*self._setup_messages, *blocking, *notes])

    def refresh_checks(self) -> None:
        """Run the installation checks in a thread and show what is not ready."""
        self._set_setup_messages([Guidance("Checking the installation…")])
        route = self.settings.route
        self._generation += 1
        generation = self._generation
        run_in_thread(
            lambda: self._checks_fn(route),
            lambda checks: self._checks_done(generation, checks),
            lambda error: self._checks_failed(generation, error),
        )

    def _is_current(self, generation: int) -> bool:
        # A result is ignored once the window closes, or when a newer refresh has started.
        return not self._closing and generation == self._generation

    def _checks_done(self, generation: int, checks: list[dict]) -> None:
        if not self._is_current(generation):
            return
        self._set_setup_messages(setup_guidance(checks, self.settings.route))
        self.checked.emit(checks)

    def _checks_failed(self, generation: int, error: BaseException) -> None:
        if not self._is_current(generation):
            return
        self._set_setup_messages(
            [Guidance(f"Could not check the installation: {error}", SETUP_TARGET)]
        )

    def closeEvent(self, event):  # noqa: N802 (Qt's name)
        # A run goes on only if the user says so: Yes stops it and waits for its thread to end
        # (the scan kills its running points), up to QUIT_WAIT_MS; then the window closes anyway.
        if self.run.running:
            buttons = QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No
            answer = QMessageBox.question(
                self, "pysrim2", STOP_AND_QUIT_TEXT, buttons, QMessageBox.StandardButton.No
            )
            if answer != QMessageBox.StandardButton.Yes:
                event.ignore()
                return
            self.run.stop()
            self.run.wait(QUIT_WAIT_MS)
        # Unsaved edits are asked about first; "No" keeps the window open.
        if not self._may_discard():
            event.ignore()
            return
        # No wait here: a slow check keeps running in the background and its result is ignored.
        # The application waits for stragglers once, at exit (see srim.app.main).
        # The pop-outs of the Results tab are windows of their own: they close with this window.
        for window in list(self.results.pop_outs):
            window.close()
        # The notebook's server stops only once the window is going to close (not when Quit is
        # cancelled above); the server is a child process, so it must not outlive the window.
        self._stop_notebook()
        self._closing = True
        super().closeEvent(event)
