"""Light and dark colours for the app: the Fusion style with a palette of our own.

``system`` follows the operating system's colour scheme; the effective scheme is returned so
the window can say what is shown.
"""

from PySide6.QtCore import Qt
from PySide6.QtGui import QColor, QPalette
from PySide6.QtWidgets import QApplication

THEMES = ("system", "light", "dark")


def _os_scheme(app: QApplication) -> str:
    """``dark`` when the operating system asks for dark colours, otherwise ``light``."""
    scheme = app.styleHints().colorScheme()
    return "dark" if scheme == Qt.ColorScheme.Dark else "light"


def _dark_palette() -> QPalette:
    palette = QPalette()
    colours = {
        QPalette.Window: "#353535",
        QPalette.WindowText: "#ffffff",
        QPalette.Base: "#232323",
        QPalette.AlternateBase: "#353535",
        QPalette.Text: "#ffffff",
        QPalette.Button: "#353535",
        QPalette.ButtonText: "#ffffff",
        QPalette.BrightText: "#ff5555",
        QPalette.ToolTipBase: "#232323",
        QPalette.ToolTipText: "#ffffff",
        QPalette.Highlight: "#2a82da",
        QPalette.HighlightedText: "#000000",
        QPalette.Link: "#4ea3ff",
    }
    for role, colour in colours.items():
        palette.setColor(role, QColor(colour))
    palette.setColor(QPalette.Disabled, QPalette.Text, QColor("#7f7f7f"))
    palette.setColor(QPalette.Disabled, QPalette.WindowText, QColor("#7f7f7f"))
    palette.setColor(QPalette.Disabled, QPalette.ButtonText, QColor("#7f7f7f"))
    return palette


def apply_theme(app: QApplication, choice: str) -> str:
    """Set the Fusion style and the palette for ``choice``; return ``light`` or ``dark``."""
    if choice not in THEMES:
        raise ValueError(f"unknown theme {choice!r}; expected one of {', '.join(THEMES)}")
    effective = _os_scheme(app) if choice == "system" else choice
    app.setStyle("Fusion")
    app.setPalette(_dark_palette() if effective == "dark" else app.style().standardPalette())
    return effective
