"""The main window: the run tabs, the menus and the guidance status bar."""

from collections.abc import Callable

from PySide6.QtCore import QUrl, Signal
from PySide6.QtGui import QActionGroup, QDesktopServices, QKeySequence
from PySide6.QtWidgets import QApplication, QLabel, QMainWindow, QMessageBox, QTabWidget

import srim
from srim.app import DOCS_URL
from srim.app.guidance import Guidance, setup_guidance
from srim.app.settings import AppSettings, save_settings
from srim.app.statusbar import GuidanceBar
from srim.app.theme import THEMES, apply_theme
from srim.app.workers import run_in_thread, wait_for_threads
from srim.cli import collect_checks

TAB_NAMES = ("Geometry", "Beam", "Run settings", "Progress", "Results")
THEME_LABELS = {"system": "&System", "light": "&Light", "dark": "&Dark"}
SETUP_TARGET = "setup"
CLOSE_WAIT_MS = 5000


class MainWindow(QMainWindow):
    checked = Signal(list)
    setup_requested = Signal()

    def __init__(
        self,
        settings: AppSettings,
        checks_fn: Callable[[str | None], list[dict]] = collect_checks,
        parent=None,
    ):
        super().__init__(parent)
        self.settings = settings
        self._checks_fn = checks_fn
        self._closing = False
        self.setWindowTitle("pysrim2")

        self.tabs = QTabWidget(self)
        for name in TAB_NAMES:
            self.tabs.addTab(QLabel(f"{name}: arrives in a later release", self), name)
        self.setCentralWidget(self.tabs)

        self.status = GuidanceBar(self)
        self.setStatusBar(self.status)
        self.status.jump.connect(self._on_jump)

        self.theme_actions: dict[str, object] = {}
        self._build_menus()

        hints = QApplication.instance().styleHints()
        hints.colorSchemeChanged.connect(self._on_scheme_changed)

    # --- menus ----------------------------------------------------------------

    def _build_menus(self) -> None:
        menus = self.menuBar()

        file_menu = menus.addMenu("&File")
        quit_action = file_menu.addAction("&Quit")
        quit_action.setShortcut(QKeySequence("Ctrl+Q"))
        quit_action.triggered.connect(self.close)

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
            self.status.set_messages([Guidance(f"Could not save the theme: {error}")])

    def _on_scheme_changed(self, *_args) -> None:
        if self.settings.theme == "system" and not self._closing:
            apply_theme(QApplication.instance(), "system")

    def _about(self) -> None:
        QMessageBox.about(self, "About pysrim2", f"pysrim2 {srim.__version__}")

    # --- setup and checks ----------------------------------------------------------

    def open_setup(self) -> None:
        """Open the setup view. Task 5 of the plan fills it in; until then it only says so."""
        self.setup_requested.emit()

    def _on_jump(self, target: str) -> None:
        if target == SETUP_TARGET:
            self.open_setup()

    def refresh_checks(self) -> None:
        """Run the installation checks in a thread and show what is not ready."""
        self.status.set_messages([Guidance("Checking the installation…")])
        route = self.settings.route
        run_in_thread(
            self,
            lambda: self._checks_fn(route),
            self._checks_done,
            self._checks_failed,
        )

    def _checks_done(self, checks: list[dict]) -> None:
        if self._closing:
            return
        self.status.set_messages(setup_guidance(checks, self.settings.route))
        self.checked.emit(checks)

    def _checks_failed(self, error: BaseException) -> None:
        if self._closing:
            return
        self.status.set_messages(
            [Guidance(f"Could not check the installation: {error}", SETUP_TARGET)]
        )

    def closeEvent(self, event):  # noqa: N802 (Qt's name)
        self._closing = True
        wait_for_threads(CLOSE_WAIT_MS)
        super().closeEvent(event)
