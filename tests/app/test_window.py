import threading
import time

import pytest

pytest.importorskip("PySide6")
pytest.importorskip("pytestqt")

from PySide6.QtCore import QUrl  # noqa: E402
from PySide6.QtGui import QPalette  # noqa: E402
from PySide6.QtWidgets import QMessageBox  # noqa: E402

import srim  # noqa: E402
from srim.app import (
    DOCS_URL,  # noqa: E402
    workers,  # noqa: E402
)
from srim.app import window as window_module  # noqa: E402
from srim.app.settings import AppSettings, load_settings  # noqa: E402
from srim.app.theme import apply_theme  # noqa: E402
from srim.app.window import MainWindow  # noqa: E402


def no_wizard(monkeypatch):
    """The setup wizard is modal; these tests only check that it is asked for."""
    monkeypatch.setattr(window_module.SetupWizard, "exec", lambda self: 0)


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


def test_a_failing_check_shows_in_the_status_bar_and_a_click_asks_for_setup(qtbot, monkeypatch):
    no_wizard(monkeypatch)
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


def test_closing_does_not_wait_for_a_slow_check(qtbot):
    started = threading.Event()
    release = threading.Event()

    def blocked(route):
        started.set()
        release.wait(5)
        return []

    win = MainWindow(AppSettings(route="wine"), checks_fn=blocked)
    qtbot.addWidget(win)
    try:
        win.refresh_checks()
        assert started.wait(5)
        begin = time.monotonic()
        win.close()
        assert time.monotonic() - begin < 1.0
    finally:
        release.set()


def test_a_check_that_outlives_the_window_is_ignored_and_forgotten(qtbot):
    started = threading.Event()
    release = threading.Event()
    checked = []

    def blocked(route):
        started.set()
        release.wait(5)
        return [{"name": "late", "ok": False, "detail": "x", "fix": "y"}]

    win = MainWindow(AppSettings(route="wine"), checks_fn=blocked)
    qtbot.addWidget(win)
    win.checked.connect(checked.append)
    try:
        win.refresh_checks()
        assert started.wait(5)
        win.close()
    finally:
        release.set()
    qtbot.waitUntil(lambda: not workers._live, timeout=5000)
    assert checked == []
    assert win.status.shown_text() == "Checking the installation…"


def test_an_older_refresh_that_finishes_last_is_ignored(qtbot):
    first_started = threading.Event()
    release_first = threading.Event()
    calls = []

    def checks(route):
        calls.append(route)
        if len(calls) == 1:
            first_started.set()
            release_first.wait(5)
            return [{"name": "old", "ok": False, "detail": "stale", "fix": "x"}]
        return [{"name": "new", "ok": False, "detail": "fresh", "fix": "y"}]

    win = MainWindow(AppSettings(route="wine"), checks_fn=checks)
    qtbot.addWidget(win)
    try:
        win.refresh_checks()
        assert first_started.wait(5)
        with qtbot.waitSignal(win.checked, timeout=5000):
            win.refresh_checks()
        assert "new" in win.status.shown_text()
    finally:
        release_first.set()
    qtbot.waitUntil(lambda: not workers._live, timeout=5000)
    assert "old" not in win.status.shown_text()
    assert "new" in win.status.shown_text()


def test_the_tooltip_shows_guidance_text_literally(qtbot):
    from PySide6.QtGui import QTextDocument

    from srim.app.guidance import Guidance

    win = MainWindow(AppSettings(route="wine"), checks_fn=lambda route: [])
    qtbot.addWidget(win)
    win.status.set_messages([Guidance("path <b>bold</b> & more", "setup")])
    tip = win.status._label.toolTip()
    document = QTextDocument()
    document.setHtml(tip)
    assert document.toPlainText() == "path <b>bold</b> & more"


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


def test_run_setup_asks_for_setup(qtbot, monkeypatch):
    no_wizard(monkeypatch)
    win = MainWindow(AppSettings(route="wine"), checks_fn=lambda route: [])
    qtbot.addWidget(win)
    with qtbot.waitSignal(win.setup_requested, timeout=1000):
        win.setup_action.trigger()


def test_the_window_title_is_pysrim2(qtbot):
    win = MainWindow(AppSettings(route="wine"), checks_fn=lambda route: [])
    qtbot.addWidget(win)
    assert win.windowTitle() == "pysrim2"
