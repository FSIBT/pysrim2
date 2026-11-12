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
from srim.app.guidance import Guidance  # noqa: E402
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
    assert win.status.shown_text().startswith("Checking the installation…")


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


def test_the_window_title_starts_with_pysrim2(qtbot):
    win = MainWindow(AppSettings(route="wine"), checks_fn=lambda route: [])
    qtbot.addWidget(win)
    assert win.windowTitle().startswith("pysrim2 — Untitled")


# --- the run setup document: edit, undo, open, save, status bar and jumps -------------

from PySide6.QtGui import QKeySequence  # noqa: E402
from PySide6.QtWidgets import QFileDialog  # noqa: E402

from srim.app import document as document_module  # noqa: E402
from srim.app.document import DocumentModel  # noqa: E402
from srim.app.statusbar import READY  # noqa: E402
from srim.spec import RunSpec  # noqa: E402


def window_with(qtbot, checks=None, document=None, route="wine"):
    win = MainWindow(
        AppSettings(route=route), checks_fn=lambda route: checks or [], document=document
    )
    qtbot.addWidget(win)
    return win


def answer(monkeypatch, button, asked=None):
    """Make the discard question answer ``button`` (and record that it was asked)."""

    def question(parent, title, text, *args, **kwargs):
        if asked is not None:
            asked.append(text)
        return button

    monkeypatch.setattr(QMessageBox, "question", question)


def test_the_status_bar_lists_setup_messages_then_the_setup_problems(qtbot):
    checks = [{"name": "trim-exe", "ok": False, "detail": "missing", "fix": "install"}]
    win = window_with(qtbot, checks=checks)
    with qtbot.waitSignal(win.checked, timeout=5000):
        win.refresh_checks()
    tip = win.status._label.toolTip()
    assert win.status.shown_text().startswith("trim-exe")
    assert tip.index("trim-exe") < tip.index("Choose the ion")


def test_blocking_problems_come_before_notes(qtbot, monkeypatch):
    monkeypatch.setattr(
        document_module,
        "problems",
        lambda doc, base_dir=None: [
            Guidance("A note", "beam/mode", blocking=False),
            Guidance("A blocker", "beam/ion"),
        ],
    )
    win = window_with(qtbot)
    win._on_document_changed()
    assert win.status.shown_text() == "A blocker (+1 more)"
    tip = win.status._label.toolTip()
    assert tip.index("A blocker") < tip.index("A note")


def test_ready_only_when_nothing_is_missing(qtbot, boron_data, write_setup, tmp_path):
    document = DocumentModel()
    win = window_with(qtbot, document=document)
    assert win.status.shown_text() != READY
    document.open(write_setup(tmp_path / "run.yaml", boron_data))
    with qtbot.waitSignal(win.checked, timeout=5000):
        win.refresh_checks()
    assert win.status.shown_text() == READY == "The run setup is complete"


@pytest.mark.parametrize(
    ("target", "tab", "rest"),
    [
        ("beam/energy_keV", "beam", "energy_keV"),
        ("geometry/1/density", "geometry", "1/density"),
        ("geometry", "geometry", ""),
        ("settings/seed", "settings", "seed"),
    ],
)
def test_a_click_selects_the_tab_and_focuses_the_field(qtbot, target, tab, rest):
    win = window_with(qtbot)
    calls = []
    win.editors[tab].focus_target = calls.append
    win.status.jump.emit(target)
    assert win.tabs.currentWidget() is win.editors[tab]
    assert calls == [rest]


def test_the_editors_are_placeholders_with_the_plan_one_text(qtbot):
    win = window_with(qtbot)
    assert set(win.editors) == {"geometry", "beam", "settings"}
    assert win.editors["beam"].text() == "Beam: arrives in a later release"
    assert win.tabs.tabText(win.tabs.indexOf(win.editors["settings"])) == "Run settings"


def test_the_edit_menu_has_undo_and_redo_with_the_standard_keys(qtbot):
    win = window_with(qtbot)
    names = {action.text() for action in win.menuBar().actions()}
    assert "&Edit" in names
    assert win.undo_action.shortcut() == QKeySequence.keyBindings(QKeySequence.StandardKey.Undo)[0]
    assert win.redo_action.shortcut() == QKeySequence.keyBindings(QKeySequence.StandardKey.Redo)[0]


def test_undo_is_enabled_only_after_an_edit(qtbot):
    win = window_with(qtbot)
    assert not win.undo_action.isEnabled()
    win.document.edit(lambda d: d.__setitem__("seed", 3))
    assert win.undo_action.isEnabled() and not win.redo_action.isEnabled()
    win.undo_action.trigger()
    assert win.document.doc["seed"] == 0
    assert not win.undo_action.isEnabled() and win.redo_action.isEnabled()


def test_the_title_names_the_setup_and_shows_the_modified_state(
    qtbot, boron_data, write_setup, tmp_path
):
    win = window_with(qtbot)
    assert win.windowTitle() == "pysrim2 — Untitled[*]" and not win.isWindowModified()
    win.document.edit(lambda d: d.__setitem__("seed", 3))
    assert win.isWindowModified()
    path = write_setup(tmp_path / "run.yaml", boron_data)
    win.document.open(path)
    assert win.windowTitle() == "pysrim2 — run.yaml[*]" and not win.isWindowModified()


def test_new_with_unsaved_edits_asks_and_no_keeps_them(qtbot, monkeypatch):
    win = window_with(qtbot)
    win.document.edit(lambda d: d.__setitem__("seed", 5))
    asked = []
    answer(monkeypatch, QMessageBox.StandardButton.No, asked)
    win.new_action.trigger()
    assert asked == ["Discard the changes to this setup?"]
    assert win.document.doc["seed"] == 5


def test_new_with_yes_discards_the_edits(qtbot, monkeypatch):
    win = window_with(qtbot)
    win.document.edit(lambda d: d.__setitem__("seed", 5))
    answer(monkeypatch, QMessageBox.StandardButton.Yes)
    win.new_action.trigger()
    assert win.document.doc["seed"] == 0 and not win.isWindowModified()


def test_open_asks_before_the_file_dialog_and_no_keeps_the_setup(qtbot, monkeypatch):
    win = window_with(qtbot)
    win.document.edit(lambda d: d.__setitem__("seed", 5))
    answer(monkeypatch, QMessageBox.StandardButton.No)

    def no_dialog(*args, **kwargs):
        raise AssertionError("the file dialog must not open after No")

    monkeypatch.setattr(QFileDialog, "getOpenFileName", no_dialog)
    win.open_action.trigger()
    assert win.document.doc["seed"] == 5


def test_open_replaces_the_setup_and_undo_does_not_go_back(
    qtbot, monkeypatch, boron_data, write_setup, tmp_path
):
    path = write_setup(tmp_path / "run.yaml", boron_data)
    win = window_with(qtbot)
    win.document.edit(lambda d: d.__setitem__("seed", 5))
    answer(monkeypatch, QMessageBox.StandardButton.Yes)
    monkeypatch.setattr(QFileDialog, "getOpenFileName", lambda *a, **k: (str(path), ""))
    win.open_action.trigger()
    assert win.document.doc["seed"] == 12345
    assert not win.undo_action.isEnabled()


def test_a_broken_setup_shows_the_message_and_keeps_the_document(qtbot, monkeypatch, tmp_path):
    broken = tmp_path / "broken.yaml"
    broken.write_text("spec: [not, a, mapping\n", encoding="utf-8")
    warnings = []
    monkeypatch.setattr(
        QMessageBox, "warning", lambda parent, title, text, *a, **k: warnings.append(text)
    )
    monkeypatch.setattr(QFileDialog, "getOpenFileName", lambda *a, **k: (str(broken), ""))
    win = window_with(qtbot)
    win.open_action.trigger()
    assert warnings and "not valid YAML" in warnings[0]
    assert win.document.path is None


def test_a_binary_file_shows_the_message_and_keeps_the_document(qtbot, monkeypatch, tmp_path):
    binary = tmp_path / "run.yaml"
    binary.write_bytes(b"\xff\xfe\x00\x81\x80not text")
    warnings = []
    monkeypatch.setattr(
        QMessageBox, "warning", lambda parent, title, text, *a, **k: warnings.append(text)
    )
    monkeypatch.setattr(QFileDialog, "getOpenFileName", lambda *a, **k: (str(binary), ""))
    win = window_with(qtbot)
    win.open_action.trigger()
    assert warnings and "not a text file" in warnings[0]
    assert win.document.path is None


def test_save_writes_the_open_setup_in_place(qtbot, boron_data, write_setup, tmp_path):
    path = write_setup(tmp_path / "run.yaml", boron_data)
    win = window_with(qtbot)
    win.document.open(path)
    win.document.edit(lambda d: d.__setitem__("seed", 9))
    win.save_action.trigger()
    assert RunSpec.from_yaml(path).to_dict()["seed"] == 9
    assert not win.isWindowModified()


def test_save_as_of_an_incomplete_setup_shows_the_library_message(qtbot, monkeypatch, tmp_path):
    target = tmp_path / "run.yaml"
    warnings = []
    monkeypatch.setattr(
        QMessageBox, "warning", lambda parent, title, text, *a, **k: warnings.append(text)
    )
    monkeypatch.setattr(QFileDialog, "getSaveFileName", lambda *a, **k: (str(target), ""))
    win = window_with(qtbot)
    win.save_action.trigger()
    assert warnings == ["Choose the ion: pick an element on the Beam tab"]
    assert not target.exists()


def test_closing_with_unsaved_edits_asks_and_no_keeps_the_window_open(qtbot, monkeypatch):
    win = window_with(qtbot)
    win.show()
    win.document.edit(lambda d: d.__setitem__("seed", 5))
    asked = []
    answer(monkeypatch, QMessageBox.StandardButton.No, asked)
    assert win.close() is False
    assert asked and win.isVisible()


def test_closing_with_yes_closes_the_window(qtbot, monkeypatch):
    win = window_with(qtbot)
    win.show()
    win.document.edit(lambda d: d.__setitem__("seed", 5))
    answer(monkeypatch, QMessageBox.StandardButton.Yes)
    assert win.close() is True
    assert not win.isVisible()
