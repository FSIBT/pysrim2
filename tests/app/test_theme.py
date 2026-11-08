import pytest

pytest.importorskip("PySide6")
pytest.importorskip("pytestqt")

from PySide6.QtCore import Qt  # noqa: E402
from PySide6.QtGui import QPalette  # noqa: E402

from srim.app import theme  # noqa: E402
from srim.app.theme import apply_theme  # noqa: E402


@pytest.fixture
def light_after(qapp):
    yield
    apply_theme(qapp, "light")
    _pin(qapp, "unknown")


def _pin(app, which):
    """Pin the platform colour scheme (Qt 6.8 and later) to light, dark or unknown."""
    scheme = {"light": Qt.ColorScheme.Light, "dark": Qt.ColorScheme.Dark}.get(which)
    hints = app.styleHints()
    hints.setColorScheme(scheme if scheme is not None else Qt.ColorScheme.Unknown)


def test_apply_theme_returns_the_effective_scheme(qapp, light_after):
    assert apply_theme(qapp, "dark") == "dark"
    assert apply_theme(qapp, "light") == "light"
    assert qapp.palette().color(QPalette.Window).lightness() > 128


def test_dark_palette_has_a_dark_window(qapp, light_after):
    apply_theme(qapp, "dark")
    assert qapp.palette().color(QPalette.Window).lightness() < 128
    assert qapp.palette().color(QPalette.WindowText).lightness() > 128


def test_system_follows_the_os_scheme(qapp, light_after, monkeypatch):
    monkeypatch.setattr(theme, "_os_scheme", lambda app: "dark")
    assert apply_theme(qapp, "system") == "dark"
    monkeypatch.setattr(theme, "_os_scheme", lambda app: "light")
    assert apply_theme(qapp, "system") == "light"


def test_the_os_scheme_is_light_or_dark(qapp):
    assert theme._os_scheme(qapp) in ("light", "dark")


def test_an_unknown_theme_is_refused(qapp):
    with pytest.raises(ValueError):
        apply_theme(qapp, "pink")


def test_light_is_light_even_when_the_os_scheme_is_dark(qapp, light_after):
    _pin(qapp, "dark")
    assert apply_theme(qapp, "light") == "light"
    assert qapp.palette().color(QPalette.Window).lightness() > 128
    assert qapp.palette().color(QPalette.WindowText).lightness() < 128


def test_light_after_dark_is_light(qapp, light_after):
    apply_theme(qapp, "dark")
    apply_theme(qapp, "light")
    assert qapp.palette().color(QPalette.Window).lightness() > 128


def test_choosing_a_scheme_pins_the_platform_scheme(qapp, light_after, monkeypatch):
    pinned = []
    monkeypatch.setattr(theme, "_pin", lambda app, scheme: pinned.append(scheme))
    apply_theme(qapp, "dark")
    apply_theme(qapp, "light")
    apply_theme(qapp, "system")
    assert pinned == [Qt.ColorScheme.Dark, Qt.ColorScheme.Light, Qt.ColorScheme.Unknown]
