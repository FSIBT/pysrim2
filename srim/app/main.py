"""``pysrim2 app``: start the desktop app. This module is the only one that imports Qt."""

from PySide6.QtCore import QTimer
from PySide6.QtWidgets import QApplication

from srim.app.guidance import setup_guidance
from srim.app.routes import available_routes
from srim.app.settings import AppSettings, load_settings
from srim.app.theme import apply_theme
from srim.app.window import MainWindow
from srim.app.workers import wait_for_threads
from srim.installer import steps

EXIT_WAIT_MS = 5000


def startup_settings() -> AppSettings:
    """Return the saved settings for this platform.

    A saved route this platform does not offer (settings copied from another machine, or wine
    saved on macOS) is dropped in memory only: the guidance then asks for a route. The file is not
    rewritten here; the next save (the wizard's Finish or a theme change) writes the new value.
    """
    settings = load_settings()
    if settings.route is not None and settings.route not in available_routes(
        steps.current_platform()
    ):
        settings.route = None
    return settings


def open_setup_when_needed(window: MainWindow) -> None:
    """Open the setup wizard on the first check whose guidance asks for it; later checks do not.

    The wizard opens on the next turn of the event loop, not inside the ``checked`` slot, so the
    modal loop does not run nested in the signal.
    """
    asked = []

    def on_checked(checks: list[dict]) -> None:
        if asked or not setup_guidance(checks, window.settings.route):
            return
        asked.append(True)
        QTimer.singleShot(0, window.open_setup)

    window.checked.connect(on_checked)


def main(argv: list[str] | None = None) -> int:
    """Show the main window and run the event loop until it closes; return the exit code."""
    app = QApplication.instance() or QApplication(["pysrim2", *(argv or [])])
    app.setApplicationName("pysrim2")
    settings = startup_settings()
    apply_theme(app, settings.theme)
    window = MainWindow(settings)
    open_setup_when_needed(window)
    window.show()
    window.refresh_checks()
    code = app.exec()
    wait_for_threads(EXIT_WAIT_MS)  # a check that is still running gets a short grace period
    return code
