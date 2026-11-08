import threading

import pytest

pytest.importorskip("PySide6")
pytest.importorskip("pytestqt")

from PySide6.QtCore import QUrl  # noqa: E402
from PySide6.QtGui import QPalette  # noqa: E402
from PySide6.QtWidgets import QMessageBox  # noqa: E402

import srim  # noqa: E402
from srim.app import DOCS_URL  # noqa: E402
from srim.app import window as window_module  # noqa: E402
from srim.app.settings import AppSettings, load_settings  # noqa: E402
from srim.app.theme import apply_theme  # noqa: E402
from srim.app.window import MainWindow  # noqa: E402


@pytest.fixture
def reset_theme(qapp):
    yield
    apply_theme(qapp, "light")


def test_the_window_has_the_tabs_and_the_menus(qtbot):
    win = MainWindow(AppSettings(route="wine"), checks_fn=lambda route: [])
    qtbot.addWidget(win)
    assert [win.tabs.tabText(i) for i in range(win.tabs.count())] == [
        "Geometry",
        "Beam",
        "Run settings",
        "Progress",
        "Results",
    ]
    assert {a.text() for a in win.menuBar().actions()} >= {"&File", "&Setup", "&View", "&Help"}


def test_a_failing_check_shows_in_the_status_bar_and_a_click_asks_for_setup(qtbot):
    checks = [
        {
            "name": "trim-exe",
            "ok": False,
            "detail": "/x/TRIM.exe",
            "fix": "run: pysrim2 install srim",
        }
    ]
    win = MainWindow(AppSettings(route="wine"), checks_fn=lambda route: checks)
    qtbot.addWidget(win)
    with qtbot.waitSignal(win.checked, timeout=5000):
        win.refresh_checks()
    assert "trim-exe" in win.status.shown_text()
    with qtbot.waitSignal(win.setup_requested, timeout=1000):
        win.status.jump.emit("setup")


def test_no_route_yet_asks_to_choose_one(qtbot):
    win = MainWindow(AppSettings(), checks_fn=lambda route: [])
    qtbot.addWidget(win)
    with qtbot.waitSignal(win.checked, timeout=5000):
        win.refresh_checks()
    assert "Choose how SRIM runs" in win.status.shown_text()


def test_a_check_that_raises_is_shown_not_raised(qtbot):
    def broken(route):
        raise RuntimeError("podman hangs")

    win = MainWindow(AppSettings(route="container"), checks_fn=broken)
    qtbot.addWidget(win)
    win.refresh_checks()
    qtbot.waitUntil(lambda: "podman hangs" in win.status.shown_text(), timeout=5000)


def test_the_checks_run_off_the_gui_thread(qtbot):
    seen = []
    main_thread = threading.get_ident()

    def record(route):
        seen.append(threading.get_ident())
        return []

    win = MainWindow(AppSettings(route="wine"), checks_fn=record)
    qtbot.addWidget(win)
    with qtbot.waitSignal(win.checked, timeout=5000):
        win.refresh_checks()
    assert seen and seen[0] != main_thread


def test_closing_while_a_check_runs_waits_for_it(qtbot):
    started = threading.Event()
    finished = threading.Event()

    def slow(route):
        started.set()
        threading.Event().wait(0.3)
        finished.set()
        return []

    win = MainWindow(AppSettings(route="wine"), checks_fn=slow)
    qtbot.addWidget(win)
    win.refresh_checks()
    assert started.wait(5)
    win.close()
    assert finished.is_set()


def test_choosing_dark_applies_and_saves_it(qtbot, reset_theme, app_home):
    win = MainWindow(AppSettings(route="wine"), checks_fn=lambda route: [])
    qtbot.addWidget(win)
    win.show()
    win.theme_actions["dark"].trigger()
    assert win.theme_actions["dark"].isChecked()
    # Qt hands the new palette to existing widgets through the event loop, so wait for it.
    qtbot.waitUntil(lambda: win.palette().color(QPalette.Window).lightness() < 128, timeout=2000)
    assert load_settings().theme == "dark"


def test_the_theme_menu_starts_with_the_saved_choice(qtbot, reset_theme):
    win = MainWindow(AppSettings(route="wine", theme="light"), checks_fn=lambda route: [])
    qtbot.addWidget(win)
    assert win.theme_actions["light"].isChecked()
    assert not win.theme_actions["system"].isChecked()


def test_documentation_opens_the_docs_url(qtbot, monkeypatch):
    opened = []
    monkeypatch.setattr(window_module.QDesktopServices, "openUrl", lambda url: opened.append(url))
    win = MainWindow(AppSettings(route="wine"), checks_fn=lambda route: [])
    qtbot.addWidget(win)
    win.documentation_action.trigger()
    assert opened == [QUrl(DOCS_URL)]


def test_about_shows_the_version(qtbot, monkeypatch):
    shown = []
    monkeypatch.setattr(
        QMessageBox, "about", lambda parent, title, text: shown.append((title, text))
    )
    win = MainWindow(AppSettings(route="wine"), checks_fn=lambda route: [])
    qtbot.addWidget(win)
    win.about_action.trigger()
    assert shown and srim.__version__ in shown[0][1]


def test_run_setup_asks_for_setup(qtbot):
    win = MainWindow(AppSettings(route="wine"), checks_fn=lambda route: [])
    qtbot.addWidget(win)
    with qtbot.waitSignal(win.setup_requested, timeout=1000):
        win.setup_action.trigger()


def test_the_window_title_is_pysrim2(qtbot):
    win = MainWindow(AppSettings(route="wine"), checks_fn=lambda route: [])
    qtbot.addWidget(win)
    assert win.windowTitle() == "pysrim2"
