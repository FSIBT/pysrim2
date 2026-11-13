import threading
import time

import pytest

pytest.importorskip("PySide6")
pytest.importorskip("pytestqt")

from PySide6.QtCore import QUrl  # noqa: E402
from PySide6.QtGui import QPalette  # noqa: E402
from PySide6.QtWidgets import QApplication, QMessageBox  # noqa: E402
from runners import slow_runner  # noqa: E402

import srim  # noqa: E402
from srim.app import (
    DOCS_URL,  # noqa: E402
    workers,  # noqa: E402
)
from srim.app import window as window_module  # noqa: E402
from srim.app.guidance import Guidance  # noqa: E402
from srim.app.runs import ParallelChoice  # noqa: E402
from srim.app.scan_model import new_scan  # noqa: E402
from srim.app.settings import AppSettings, load_settings  # noqa: E402
from srim.app.theme import apply_theme  # noqa: E402
from srim.app.window import STOP_AND_QUIT_TEXT, MainWindow  # noqa: E402


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
        "Scan",
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
from srim.app.beam_tab import BeamTab  # noqa: E402
from srim.app.document import DocumentModel  # noqa: E402
from srim.app.run_settings import RunSettingsTab  # noqa: E402
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


def test_the_run_settings_tab_is_the_settings_editor(qtbot):
    win = window_with(qtbot)
    assert set(win.editors) == {"geometry", "beam", "settings", "scan"}
    assert isinstance(win.editors["beam"], BeamTab)
    assert isinstance(win.editors["settings"], RunSettingsTab)
    assert win.tabs.tabText(win.tabs.indexOf(win.editors["settings"])) == "Run settings"


def test_a_click_on_a_scan_line_selects_the_scan_tab_and_focuses_the_table(qtbot):
    win = window_with(qtbot)
    win.show()
    qtbot.waitExposed(win)
    win.document.edit(lambda doc: doc.__setitem__("_scan", new_scan()))
    win.status.jump.emit("scan/vary")
    assert win.tabs.currentWidget() is win.editors["scan"]
    table = win.editors["scan"].table
    qtbot.waitUntil(lambda: QApplication.focusWidget() is table)


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


def test_open_asks_only_after_a_file_is_chosen_and_no_keeps_the_setup(
    qtbot, monkeypatch, boron_data, write_setup, tmp_path
):
    path = write_setup(tmp_path / "run.yaml", boron_data)
    win = window_with(qtbot)
    win.document.edit(lambda d: d.__setitem__("seed", 5))
    asked = []
    answer(monkeypatch, QMessageBox.StandardButton.No, asked)
    monkeypatch.setattr(QFileDialog, "getOpenFileName", lambda *a, **k: (str(path), ""))
    win.open_action.trigger()
    assert asked == ["Discard the changes to this setup?"]
    assert win.document.doc["seed"] == 5


def test_cancelling_the_open_dialog_never_asks(qtbot, monkeypatch):
    win = window_with(qtbot)
    win.document.edit(lambda d: d.__setitem__("seed", 5))
    asked = []
    answer(monkeypatch, QMessageBox.StandardButton.Yes, asked)
    monkeypatch.setattr(QFileDialog, "getOpenFileName", lambda *a, **k: ("", ""))
    win.open_action.trigger()
    assert asked == []
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


# -- the Run menu and the toolbar --------------------------------------------------------------


def test_the_run_menu_and_toolbar_have_start_pause_and_stop(qtbot):
    win = MainWindow(AppSettings(route="wine"), checks_fn=lambda route: [], run_runner=None)
    qtbot.addWidget(win)
    run_menu = next(a.menu() for a in win.menuBar().actions() if a.text() == "&Run")
    assert [a.text() for a in run_menu.actions()] == ["&Start", "&Pause", "&Stop"]
    assert win.start_action.shortcut().toString() == "Ctrl+R"
    assert win.pause_action.isCheckable()
    assert win.run_toolbar.actions() == [
        win.start_action,
        win.pause_action,
        win.stop_action,
    ]


def test_quit_during_a_run_asks_before_closing(
    qtbot, monkeypatch, boron_data, write_setup, tmp_path
):
    path = write_setup(tmp_path / "w" / "run.yaml", boron_data)
    win = MainWindow(
        AppSettings(route="wine"),
        checks_fn=lambda route: [],
        run_runner=slow_runner,
    )
    qtbot.addWidget(win)
    win.document.open(path)
    win.run.start(ParallelChoice())
    asked = []

    def question(parent, title, text, *args, **kwargs):
        asked.append(text)
        return QMessageBox.StandardButton.No

    monkeypatch.setattr(QMessageBox, "question", question)
    assert win.close() is False
    assert asked == [STOP_AND_QUIT_TEXT]
    assert win.run.running
    monkeypatch.setattr(
        QMessageBox, "question", lambda *args, **kwargs: QMessageBox.StandardButton.Yes
    )
    with qtbot.waitSignal(win.run.finished, timeout=15_000):
        win.close()


def finished_window(qtbot, tmp_path, write_setup, boron_data, energy=None):
    """A window whose run has finished (test_files/2 outputs, no TRIM.DAT), its results drawn
    when the Results tab is shown."""
    from copy import deepcopy

    from run_folders import DONE, finished_scan

    from srim.app.runs import ParallelChoice, plan_run

    spec = dict(boron_data)
    if energy is not None:
        spec["ion"] = {**boron_data["ion"], "energy_keV": energy}
    path = write_setup(tmp_path / "setup" / "run.yaml", spec)
    win = MainWindow(AppSettings(route="wine"), checks_fn=lambda route: [])
    qtbot.addWidget(win)
    win.document.open(path)
    scan = plan_run(win.document.doc, win.document.base_dir, ParallelChoice())
    folder = tmp_path / "setup-runs" / "20261112T000000Z"
    result = finished_scan(folder, scan, [DONE])
    win.run.result = result
    win.run.folder = folder
    win.run.snapshot = deepcopy(win.document.doc)
    return win, result


def test_closing_the_window_closes_its_pop_outs(qtbot, tmp_path, write_setup, boron_data):
    from srim.app.plots import CATALOGUE

    win, result = finished_window(qtbot, tmp_path, write_setup, boron_data)
    win.show()
    win.tabs.setCurrentWidget(win.results)
    with qtbot.waitSignal(win.results.canvas.drawn, timeout=10000):
        win.run.finished.emit(result)
        win.results.plot_list.setCurrentItem(win.results.item_for("ionization"))
    win.results.pop_out.click()
    pop = win.results.pop_outs[0]
    assert pop.windowTitle() == next(p.title for p in CATALOGUE if p.key == "ionization")
    assert pop.isVisible()
    win.close()
    assert not pop.isVisible()
    assert win.results.pop_outs == []


@pytest.fixture
def held_threads(monkeypatch):
    """The window's background reads are held: the test calls them itself, as it chooses."""
    calls = []

    def fake(fn, done, failed):
        calls.append((fn, done))

    monkeypatch.setattr(window_module, "run_in_thread", fake)
    return calls


def test_the_samples_of_a_finished_run_are_drawn_when_the_setup_is_unchanged(
    qtbot, tmp_path, write_setup, boron_data, held_threads
):
    import pandas as pd

    win, result = finished_window(
        qtbot, tmp_path, write_setup, boron_data, energy={"gaussian": {"mean": 20.0, "sigma": 1.0}}
    )
    win.run.finished.emit(result)
    _fn, done = held_threads[-1]
    done(pd.DataFrame({"energy_keV": [20.0, 20.5, 21.0]}))
    assert len(win.editors["beam"].plots["energy_keV"].axes.containers) == 1


def test_a_late_sample_read_is_ignored_when_the_setup_changed_meanwhile(
    qtbot, tmp_path, write_setup, boron_data, held_threads
):
    import pandas as pd

    win, result = finished_window(
        qtbot, tmp_path, write_setup, boron_data, energy={"gaussian": {"mean": 20.0, "sigma": 1.0}}
    )
    win.run.finished.emit(result)
    _fn, done = held_threads[-1]
    win.document.edit(lambda doc: doc.update(number_ions=300))
    done(pd.DataFrame({"energy_keV": [20.0, 20.5, 21.0]}))
    assert len(win.editors["beam"].plots["energy_keV"].axes.containers) == 0


def test_a_late_sample_read_is_ignored_after_the_window_closed(
    qtbot, tmp_path, write_setup, boron_data, held_threads
):
    import pandas as pd

    win, result = finished_window(
        qtbot, tmp_path, write_setup, boron_data, energy={"gaussian": {"mean": 20.0, "sigma": 1.0}}
    )
    win.run.finished.emit(result)
    _fn, done = held_threads[-1]
    win._closing = True
    done(pd.DataFrame({"energy_keV": [20.0, 20.5, 21.0]}))
    assert len(win.editors["beam"].plots["energy_keV"].axes.containers) == 0


# -- File > Open recent, File > Open run folder…, recent setups and runs -------------------

RUN_TIMEOUT = 60_000


def recent_items(win):
    """The entries of File > Open recent, in menu order (separators left out)."""
    return [action for action in win.recent_menu.actions() if not action.isSeparator()]


def a_run_of(qtbot, tmp_path, write_setup, boron_data):
    """A window whose setup (in ``tmp_path/boron``) has been run once with the fake runner;
    returns the window and the run folder."""
    from runners import fixture_runner as runner

    path = write_setup(tmp_path / "boron" / "run.yaml", boron_data)
    win = MainWindow(AppSettings(route="wine"), checks_fn=lambda route: [], run_runner=runner)
    qtbot.addWidget(win)
    win.document.open(path)
    with qtbot.waitSignal(win.run.finished, timeout=RUN_TIMEOUT):
        win.run.start(ParallelChoice())
    # A real SRIM run writes run.yaml next to its outputs; the fake runner does not.
    for point in (win.run.folder / "points").iterdir():
        (point / "run.yaml").write_text("x", encoding="utf-8")
    return win, win.run.folder


def fresh_window(qtbot):
    from runners import fixture_runner as runner

    win = MainWindow(AppSettings(route="wine"), checks_fn=lambda route: [], run_runner=runner)
    qtbot.addWidget(win)
    return win


def pick_folder(monkeypatch, folder):
    monkeypatch.setattr(
        window_module.QFileDialog,
        "getExistingDirectory",
        staticmethod(lambda *args, **kwargs: str(folder)),
    )


def test_the_file_menu_has_open_recent_and_open_run_folder(qtbot):
    win = fresh_window(qtbot)
    file_menu = next(a.menu() for a in win.menuBar().actions() if a.text() == "&File")
    texts = [a.text() for a in file_menu.actions()]
    assert "Open &recent" in texts
    assert "Open &run folder…" in texts
    assert [item.text() for item in recent_items(win)] == ["No recent setups or runs"]


def test_a_finished_run_is_the_first_recent_item(qtbot, tmp_path, write_setup, boron_data):
    win, folder = a_run_of(qtbot, tmp_path, write_setup, boron_data)
    first = recent_items(win)[0]
    assert first.isEnabled()
    assert first.toolTip() == str(folder)
    assert folder.name in first.text()


def test_opening_a_recent_run_in_a_fresh_window_shows_the_setup_and_results(
    qtbot, tmp_path, write_setup, boron_data
):
    _win, folder = a_run_of(qtbot, tmp_path, write_setup, boron_data)
    fresh = fresh_window(qtbot)
    with qtbot.waitSignal(fresh.run.finished, timeout=RUN_TIMEOUT):
        recent_items(fresh)[0].trigger()
    assert fresh.document.path == folder / "scan.yaml"
    assert not fresh.document.is_modified
    assert fresh.results.banner.isHidden()
    assert not fresh.results.item_for("ionization").isDisabled()
    assert fresh.save_results_action.isEnabled()


def test_a_run_folder_without_its_result_opens_with_the_message(
    qtbot, tmp_path, write_setup, boron_data, monkeypatch
):
    import shutil

    from srim.app.runs import UNFINISHED_TEXT

    _win, folder = a_run_of(qtbot, tmp_path, write_setup, boron_data)
    shutil.rmtree(folder / "result")
    (next((folder / "points").iterdir()) / "run.yaml").write_text("x", encoding="utf-8")
    fresh = fresh_window(qtbot)
    pick_folder(monkeypatch, folder)
    with qtbot.waitSignal(fresh.run.finished, timeout=RUN_TIMEOUT):
        fresh.open_run_action.trigger()
    assert UNFINISHED_TEXT in fresh.status.shown_text()
    assert fresh.run.partial
    assert not fresh.save_results_action.isEnabled()


def test_a_moved_setup_is_greyed_out_with_its_problem(qtbot, tmp_path, boron_data, write_setup):
    from srim.app.recent import SETUP, add_recent, new_entry

    path = write_setup(tmp_path / "moved" / "run.yaml", boron_data)
    add_recent(new_entry(path, SETUP))
    path.unlink()
    win = fresh_window(qtbot)
    item = recent_items(win)[0]
    assert not item.isEnabled()
    assert item.toolTip() == "the file was moved or deleted"


def test_opening_a_setup_adds_it_to_the_recent_list(
    qtbot, tmp_path, boron_data, write_setup, monkeypatch
):
    from srim.app.recent import load_recent

    path = write_setup(tmp_path / "boron" / "run.yaml", boron_data)
    win = fresh_window(qtbot)
    monkeypatch.setattr(
        window_module.QFileDialog,
        "getOpenFileName",
        staticmethod(lambda *args, **kwargs: (str(path), "")),
    )
    win.open_setup_file()
    assert [entry.path for entry in load_recent()] == [str(path)]
    assert recent_items(win)[0].toolTip() == str(path)  # the menu follows the list


def test_open_run_folder_asks_before_discarding_unsaved_edits(
    qtbot, tmp_path, write_setup, boron_data, monkeypatch
):
    _win, folder = a_run_of(qtbot, tmp_path, write_setup, boron_data)
    fresh = fresh_window(qtbot)
    fresh.document.edit(lambda doc: doc.__setitem__("number_ions", 7))
    asked = []

    def question(*args, **kwargs):
        asked.append(args[2])
        return QMessageBox.StandardButton.No

    monkeypatch.setattr(QMessageBox, "question", staticmethod(question))
    pick_folder(monkeypatch, folder)
    fresh.open_run_action.trigger()
    assert asked == [window_module.DISCARD_TEXT]
    assert fresh.run.folder is None
    assert fresh.document.is_modified


# -- a loaded run folder, Save and Start, the stale-read guard, the closing flag ----------------


def _snapshot_tree(folder):
    return sorted(str(path.relative_to(folder)) for path in folder.rglob("*"))


def _open_run(qtbot, monkeypatch, folder):
    """A fresh window with the run folder ``folder`` opened (File > Open run folder…)."""
    fresh = fresh_window(qtbot)
    pick_folder(monkeypatch, folder)
    with qtbot.waitSignal(fresh.run.finished, timeout=RUN_TIMEOUT):
        fresh.open_run_action.trigger()
    return fresh


def _answer_save_dialog(monkeypatch, target, asked):
    """Make the Save dialog answer ``target`` (empty for cancel); ``asked`` gets its start."""

    def get_save(*args, **kwargs):
        asked.append(args[2])
        return (str(target) if target is not None else "", "")

    monkeypatch.setattr(QFileDialog, "getSaveFileName", get_save)


def test_saving_a_loaded_run_never_changes_its_scan_yaml_and_asks_for_a_file(
    qtbot, tmp_path, write_setup, boron_data, monkeypatch
):
    import yaml

    _win, folder = a_run_of(qtbot, tmp_path, write_setup, boron_data)
    fresh = _open_run(qtbot, monkeypatch, folder)
    scan_bytes = (folder / "scan.yaml").read_bytes()
    tree = _snapshot_tree(folder)
    fresh.document.edit(lambda doc: doc.__setitem__("number_ions", 7))
    target = tmp_path / "saved.yaml"
    asked = []
    _answer_save_dialog(monkeypatch, target, asked)
    fresh.save_action.trigger()
    assert asked == [str(tmp_path / "boron" / "run.yaml")]  # the setup beside its run folders
    assert (folder / "scan.yaml").read_bytes() == scan_bytes
    assert _snapshot_tree(folder) == tree
    assert fresh.document.path == target
    assert yaml.safe_load(target.read_text(encoding="utf-8"))["spec"]["number_ions"] == 7


def test_a_cancelled_save_of_a_loaded_run_changes_nothing(
    qtbot, tmp_path, write_setup, boron_data, monkeypatch
):
    _win, folder = a_run_of(qtbot, tmp_path, write_setup, boron_data)
    fresh = _open_run(qtbot, monkeypatch, folder)
    scan_bytes = (folder / "scan.yaml").read_bytes()
    fresh.document.edit(lambda doc: doc.__setitem__("number_ions", 7))
    asked = []
    _answer_save_dialog(monkeypatch, None, asked)
    fresh.save_action.trigger()
    assert len(asked) == 1
    assert (folder / "scan.yaml").read_bytes() == scan_bytes
    assert fresh.document.path == folder / "scan.yaml"


def test_start_on_a_loaded_run_asks_for_a_file_and_creates_nothing_in_the_run_folder(
    qtbot, tmp_path, write_setup, boron_data, monkeypatch
):
    _win, folder = a_run_of(qtbot, tmp_path, write_setup, boron_data)
    fresh = _open_run(qtbot, monkeypatch, folder)
    tree = _snapshot_tree(folder)
    asked = []
    _answer_save_dialog(monkeypatch, None, asked)
    fresh.start_action.trigger()
    assert len(asked) == 1
    assert not fresh.run.running
    assert _snapshot_tree(folder) == tree


def test_start_on_a_loaded_run_runs_the_saved_copy_in_its_own_run_folder(
    qtbot, tmp_path, write_setup, boron_data, monkeypatch
):
    _win, folder = a_run_of(qtbot, tmp_path, write_setup, boron_data)
    fresh = _open_run(qtbot, monkeypatch, folder)
    tree = _snapshot_tree(folder)
    target = tmp_path / "copy.yaml"
    _answer_save_dialog(monkeypatch, target, [])
    with qtbot.waitSignal(fresh.run.finished, timeout=RUN_TIMEOUT):
        fresh.start_action.trigger()
    assert fresh.run.folder.parent == tmp_path / "copy-runs"
    assert _snapshot_tree(folder) == tree


def test_save_and_save_as_are_disabled_while_a_run_goes(
    qtbot, tmp_path, boron_data, write_setup, monkeypatch
):
    path = write_setup(tmp_path / "w" / "run.yaml", boron_data)
    win = MainWindow(AppSettings(route="wine"), checks_fn=lambda route: [], run_runner=slow_runner)
    qtbot.addWidget(win)
    win.document.open(path)
    win.document.edit(lambda doc: doc.__setitem__("seed", 9))
    win.run.start(ParallelChoice())
    assert not win.save_action.isEnabled()
    assert not win.save_as_action.isEnabled()
    monkeypatch.setattr(
        QMessageBox, "question", lambda *args, **kwargs: QMessageBox.StandardButton.Yes
    )
    with qtbot.waitSignal(win.run.finished, timeout=15_000):
        win.run.stop()
    assert win.save_action.isEnabled()
    assert win.save_as_action.isEnabled()


def test_save_and_save_as_are_disabled_while_a_run_folder_is_read(
    qtbot, tmp_path, write_setup, boron_data, monkeypatch, held_loads
):
    _win, folder = a_run_of(qtbot, tmp_path, write_setup, boron_data)
    fresh = fresh_window(qtbot)
    pick_folder(monkeypatch, folder)
    fresh.open_run_action.trigger()
    assert not fresh.save_action.isEnabled()
    assert not fresh.save_as_action.isEnabled()
    read, done, _failed = held_loads[-1]
    done(read())
    assert fresh.save_action.isEnabled()
    assert fresh.save_as_action.isEnabled()


@pytest.fixture
def held_loads(monkeypatch):
    """The run folder reads are held: the test calls them, as it chooses. A run's own thread
    goes on as usual (only the ``read`` of RunController.load is kept)."""
    from srim.app import run_control

    real = run_control.run_in_thread
    calls = []

    def fake(fn, done, failed):
        if fn.__name__ == "read":
            calls.append((fn, done, failed))
        else:
            real(fn, done, failed)

    monkeypatch.setattr(run_control, "run_in_thread", fake)
    return calls


def test_a_run_folder_read_that_ends_after_the_window_closed_is_ignored(
    qtbot, tmp_path, write_setup, boron_data, monkeypatch, held_loads
):
    _win, folder = a_run_of(qtbot, tmp_path, write_setup, boron_data)
    fresh = fresh_window(qtbot)
    pick_folder(monkeypatch, folder)
    fresh.open_run_action.trigger()
    read, done, _failed = held_loads[-1]
    fresh.close()
    done(read())
    assert fresh.run.folder is None
    assert fresh.document.path is None


def test_an_older_run_folder_read_that_ends_last_is_ignored(
    qtbot, tmp_path, write_setup, boron_data, held_loads
):
    import shutil

    _win, folder = a_run_of(qtbot, tmp_path, write_setup, boron_data)
    copy = tmp_path / "copy-runs" / folder.name
    shutil.copytree(folder, copy)
    fresh = fresh_window(qtbot)
    fresh.run.load(folder)
    older = held_loads[-1]
    fresh.run.load(copy)
    newer = held_loads[-1]
    older[1](older[0]())
    assert fresh.run.folder is None
    with qtbot.waitSignal(fresh.run.finished, timeout=RUN_TIMEOUT):
        newer[1](newer[0]())
    assert fresh.run.folder == copy


def test_a_run_folder_read_that_ends_after_a_run_started_is_ignored(
    qtbot, tmp_path, write_setup, boron_data, held_loads
):
    _win, folder = a_run_of(qtbot, tmp_path, write_setup, boron_data)
    fresh = fresh_window(qtbot)
    fresh.document.open(tmp_path / "boron" / "run.yaml")
    fresh.run.load(folder)
    read, done, _failed = held_loads[-1]
    with qtbot.waitSignal(fresh.run.finished, timeout=RUN_TIMEOUT):
        fresh.run.start(ParallelChoice())
    started = fresh.run.folder
    done(read())
    assert fresh.run.folder == started
    assert started != folder


def test_the_closing_flag_is_true_once_the_window_closes(qtbot):
    win = fresh_window(qtbot)
    assert not win.closing
    win.close()
    assert win.closing


def test_a_run_folder_is_the_first_recent_item_after_it_is_opened(
    qtbot, tmp_path, write_setup, boron_data, monkeypatch
):
    from srim.app.recent import RUN, SETUP, add_recent, load_recent, new_entry

    _win, folder = a_run_of(qtbot, tmp_path, write_setup, boron_data)
    other = tmp_path / "other.yaml"
    add_recent(new_entry(other, SETUP))  # something else is now the newest entry
    _open_run(qtbot, monkeypatch, folder)
    entries = load_recent()
    assert (entries[0].kind, entries[0].path) == (RUN, str(folder))
    assert [entry.path for entry in entries if entry.kind == SETUP] == [str(other)]


def test_a_run_whose_point_folders_are_gone_cannot_save_its_results(
    qtbot, tmp_path, write_setup, boron_data
):
    import shutil

    from srim.app.runs import MISSING_OUTPUTS_TEXT

    win, folder = a_run_of(qtbot, tmp_path, write_setup, boron_data)
    win.run.finished.emit(win.run.result)  # the window looks again: the points have outputs now
    assert win.save_results_action.isEnabled()
    shutil.rmtree(folder / "points")
    win.run.finished.emit(win.run.result)
    assert not win.save_results_action.isEnabled()
    assert win.save_results_action.toolTip() == MISSING_OUTPUTS_TEXT


def test_opening_a_run_folders_scan_yaml_as_a_setup_is_not_recorded_as_a_setup(
    qtbot, tmp_path, write_setup, boron_data, monkeypatch
):
    from srim.app.recent import load_recent

    _win, folder = a_run_of(qtbot, tmp_path, write_setup, boron_data)
    fresh = fresh_window(qtbot)
    monkeypatch.setattr(
        window_module.QFileDialog,
        "getOpenFileName",
        staticmethod(lambda *args, **kwargs: (str(folder / "scan.yaml"), "")),
    )
    fresh.open_setup_file()
    assert fresh.document.in_run_folder
    assert [entry.path for entry in load_recent() if entry.path.endswith("scan.yaml")] == []
