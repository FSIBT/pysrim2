"""Light and dark colours for the app: the Fusion style with a palette of our own.

The chosen scheme is pinned on the style hints (Qt 6.8 and later), so Fusion and the widgets
follow it, and ``system`` returns to the operating system's scheme. The effective scheme is
returned so the window can say what is shown.
"""

from PySide6.QtCore import Qt
from PySide6.QtGui import QColor, QPalette
from PySide6.QtWidgets import QApplication

THEMES = ("system", "light", "dark")


def _os_scheme(app: QApplication) -> str:
    """``dark`` when the operating system asks for dark colours, otherwise ``light``."""
    scheme = app.styleHints().colorScheme()
    return "dark" if scheme == Qt.ColorScheme.Dark else "light"


def _palette(colours: dict, disabled: dict) -> QPalette:
    palette = QPalette()
    for role, colour in colours.items():
        palette.setColor(role, QColor(colour))
    for role, colour in disabled.items():
        palette.setColor(QPalette.Disabled, role, QColor(colour))
    return palette


_DISABLED_TEXT = {
    QPalette.Text: "#808080",
    QPalette.WindowText: "#808080",
    QPalette.ButtonText: "#808080",
}

_LIGHT = {
    QPalette.Window: "#efefef",
    QPalette.WindowText: "#000000",
    QPalette.Base: "#ffffff",
    QPalette.AlternateBase: "#f7f7f7",
    QPalette.Text: "#000000",
    QPalette.Button: "#efefef",
    QPalette.ButtonText: "#000000",
    QPalette.BrightText: "#ff0000",
    QPalette.ToolTipBase: "#ffffdc",
    QPalette.ToolTipText: "#000000",
    QPalette.Highlight: "#308cc6",
    QPalette.HighlightedText: "#ffffff",
    QPalette.Link: "#0000ff",
}

_DARK = {
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


def _pin(app: QApplication, scheme) -> None:
    """Pin the platform colour scheme where the Qt build can do it (6.8 and later)."""
    hints = app.styleHints()
    if hasattr(hints, "setColorScheme"):
        hints.setColorScheme(scheme)


def apply_theme(app: QApplication, choice: str) -> str:
    """Set the Fusion style and an explicit palette for ``choice``; return ``light`` or ``dark``."""
    if choice not in THEMES:
        raise ValueError(f"unknown theme {choice!r}; expected one of {', '.join(THEMES)}")
    if choice == "system":
        _pin(app, Qt.ColorScheme.Unknown)
        effective = _os_scheme(app)
    else:
        _pin(app, Qt.ColorScheme.Dark if choice == "dark" else Qt.ColorScheme.Light)
        effective = choice
    colours = _DARK if effective == "dark" else _LIGHT
    app.setStyle("Fusion")
    app.setPalette(_palette(colours, _DISABLED_TEXT))
    return effective
