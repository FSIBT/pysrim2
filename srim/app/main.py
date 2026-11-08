"""``pysrim2 app``: start the desktop app. This module is the only one that imports Qt."""

from PySide6.QtWidgets import QApplication

from srim.app.settings import load_settings
from srim.app.theme import apply_theme
from srim.app.window import MainWindow
from srim.app.workers import wait_for_threads

EXIT_WAIT_MS = 5000


def main(argv: list[str] | None = None) -> int:
    """Show the main window and run the event loop until it closes; return the exit code."""
    app = QApplication.instance() or QApplication(["pysrim2", *(argv or [])])
    app.setApplicationName("pysrim2")
    settings = load_settings()
    apply_theme(app, settings.theme)
    window = MainWindow(settings)
    window.show()
    window.refresh_checks()
    code = app.exec()
    wait_for_threads(EXIT_WAIT_MS)  # a check that is still running gets a short grace period
    return code
