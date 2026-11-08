import pytest

pytest.importorskip("PySide6")
pytest.importorskip("pytestqt")

from PySide6.QtGui import QPalette  # noqa: E402

from srim.app import theme  # noqa: E402
from srim.app.theme import apply_theme  # noqa: E402


@pytest.fixture
def light_after(qapp):
    yield
    apply_theme(qapp, "light")


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
