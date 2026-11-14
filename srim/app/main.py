"""``pysrim2 app``: start the desktop app. This module is the only one that imports Qt."""

import importlib.util

from PySide6.QtCore import QCoreApplication, Qt, QTimer
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


def stop_on_quit(app: QApplication, window: MainWindow) -> None:
    """Stop the window's child processes (the notebook server and the PDF exports) when the
    application quits, also when it quits without this window being closed first. The window's
    own close does the same, and both are idempotent. (A killed process runs no quit handler.)"""
    app.aboutToQuit.connect(window.stop_background)


def prepare_webengine() -> None:
    """Import the WebEngine module and share the OpenGL contexts, when PySide6 has WebEngine.

    WebEngine must be imported and the attribute set before the QApplication is created, so this
    runs first in :func:`main`. Without WebEngine nothing happens (the notebook entries then say
    how to install it).
    """
    if importlib.util.find_spec("PySide6.QtWebEngineWidgets") is None:
        return
    QCoreApplication.setAttribute(Qt.ApplicationAttribute.AA_ShareOpenGLContexts)
    import PySide6.QtWebEngineWidgets  # noqa: F401 (imported for WebEngine's start-up)


def main(argv: list[str] | None = None) -> int:
    """Show the main window and run the event loop until it closes; return the exit code."""
    app = QApplication.instance()
    if app is None:
        prepare_webengine()
        app = QApplication(["pysrim2", *(argv or [])])
    app.setApplicationName("pysrim2")
    settings = startup_settings()
    apply_theme(app, settings.theme)
    window = MainWindow(settings)
    stop_on_quit(app, window)
    open_setup_when_needed(window)
    window.show()
    window.refresh_checks()
    code = app.exec()
    wait_for_threads(EXIT_WAIT_MS)  # a check that is still running gets a short grace period
    return code
