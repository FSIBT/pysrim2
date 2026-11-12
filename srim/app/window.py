"""The main window: the run tabs, the menus, the run setup and the guidance status bar."""

from collections.abc import Callable
from pathlib import Path

from PySide6.QtCore import QUrl, Signal
from PySide6.QtGui import QActionGroup, QDesktopServices, QKeySequence
from PySide6.QtWidgets import (
    QApplication,
    QDialog,
    QFileDialog,
    QLabel,
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
from srim.app.run_settings import RunSettingsTab
from srim.app.settings import AppSettings, save_settings
from srim.app.setup import SetupWizard
from srim.app.statusbar import GuidanceBar
from srim.app.theme import THEMES, apply_theme
from srim.app.workers import run_in_thread
from srim.cli import collect_checks
from srim.spec import SpecError

#: the editor tabs, by key (the first part of a guidance target) and tab name
EDITOR_TABS = (("geometry", "Geometry"), ("beam", "Beam"), ("settings", "Run settings"))
#: the tabs that only say they arrive later in 0.12
PLAIN_TABS = ("Progress", "Results")
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
    ):
        super().__init__(parent)
        self.settings = settings
        self._checks_fn = checks_fn
        self._closing = False
        self._setup_open = False
        self._generation = 0
        self._setup_messages: list[Guidance] = []
        self.document = document if document is not None else DocumentModel(self)

        # Editors are keyed "geometry", "beam" and "settings".
        self.editors: dict[str, QWidget] = {}
        self.tabs = QTabWidget(self)
        for key, name in EDITOR_TABS:
            if key == "geometry":
                editor = GeometryTab(self.document, self)
            elif key == "beam":
                editor = BeamTab(self.document, self)
            else:
                editor = RunSettingsTab(self.document, self)
            self.editors[key] = editor
            self.tabs.addTab(editor, name)
        for name in PLAIN_TABS:
            self.tabs.addTab(QLabel(f"{name}: arrives in a later release", self), name)
        self.setCentralWidget(self.tabs)

        self.status = GuidanceBar(self)
        self.setStatusBar(self.status)
        self.status.jump.connect(self._on_jump)

        self.theme_actions: dict[str, object] = {}
        self._build_menus()

        self.document.changed.connect(self._on_document_changed)
        self._on_document_changed()

        hints = QApplication.instance().styleHints()
        hints.colorSchemeChanged.connect(self._on_scheme_changed)

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
        self.save_action = file_menu.addAction("&Save setup")
        self.save_action.setShortcut(QKeySequence("Ctrl+S"))
        self.save_action.triggered.connect(self.save_setup)
        self.save_as_action = file_menu.addAction("Save setup &as…")
        self.save_as_action.setShortcut(QKeySequence("Ctrl+Shift+S"))
        self.save_as_action.triggered.connect(self.save_setup_as)
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
        document = self.document
        self.undo_action.setEnabled(document.can_undo)
        self.redo_action.setEnabled(document.can_redo)
        name = document.path.name if document.path is not None else "Untitled"
        self.setWindowTitle(f"pysrim2 — {name}[*]")
        self.setWindowModified(document.is_modified)
        self._show_status()

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
        if not self._may_discard():
            return
        try:
            self.document.open(path)
        except (SpecError, OSError) as error:
            QMessageBox.warning(self, "Open setup", str(error))

    def save_setup(self) -> None:
        self._commit_pending()
        if self.document.path is None:
            self.save_setup_as()
            return
        self._save_to(self.document.path)

    def save_setup_as(self) -> None:
        self._commit_pending()
        if self.document.base_dir is not None:
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

    def _save_to(self, path: Path) -> None:
        try:
            self.document.save(path)
        except (SpecError, OSError) as error:
            QMessageBox.warning(self, "Save setup", str(error))

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
        # Unsaved edits are asked about first; "No" keeps the window open.
        if not self._may_discard():
            event.ignore()
            return
        # No wait here: a slow check keeps running in the background and its result is ignored.
        # The application waits for stragglers once, at exit (see srim.app.main).
        self._closing = True
        super().closeEvent(event)
