"""Starting, pausing and stopping a run of the app's setup (the run thread, its events, the lock).

The runs use fake runners (``tests/app/runners.py``): no SRIM, wine or container is needed.
Every wait is bounded; a run that never ends fails the test instead of hanging it.
"""

import multiprocessing
import re
import time
from copy import deepcopy

import pytest

pytest.importorskip("PySide6")
pytest.importorskip("pytestqt")

from PySide6.QtWidgets import QFileDialog, QMessageBox  # noqa: E402
from runners import (  # noqa: E402
    GATE_VAR,
    failing_runner,
    fixture_runner,
    gated_runner,
    slow_runner,
)

from srim.app import run_control  # noqa: E402
from srim.app.runs import ParallelChoice  # noqa: E402
from srim.app.scan_model import new_scan  # noqa: E402
from srim.app.settings import AppSettings, load_settings  # noqa: E402
from srim.app.window import STOP_AND_QUIT_TEXT, MainWindow  # noqa: E402
from srim.scan.executor import ScanBusy  # noqa: E402

#: the bound of a run that is expected to finish on its own
RUN_TIMEOUT = 60_000
#: the bound of a stop: the workers are killed at once, so this is generous
STOP_TIMEOUT = 15_000
STAMP = re.compile(r"\d{8}T\d{6}Z(-\d+)?")


@pytest.fixture
def make_window(qtbot):
    def make(runner=fixture_runner, route="wine"):
        win = MainWindow(AppSettings(route=route), checks_fn=lambda _route: [], run_runner=runner)
        qtbot.addWidget(win)
        return win

    return make


@pytest.fixture
def setup_file(tmp_path, boron_data, write_setup):
    """The boron reference setup, saved as ``boron/run.yaml``."""
    return write_setup(tmp_path / "boron" / "run.yaml", boron_data)


@pytest.fixture
def win(make_window, setup_file):
    window = make_window()
    window.document.open(setup_file)
    return window


def add_scan(win, energies, ions=10):
    """Make the open setup a scan of ``ion.energy_keV`` over ``energies``."""

    def fill(doc):
        scan = new_scan()
        scan["grid"] = {"ion.energy_keV": list(energies)}
        scan["ions"] = ions
        doc["_scan"] = scan

    win.document.edit(fill)


def answer(monkeypatch, button, asked=None):
    """Make the window's question answer ``button`` (and record the text it was asked)."""

    def question(parent, title, text, *args, **kwargs):
        if asked is not None:
            asked.append(text)
        return button

    monkeypatch.setattr(QMessageBox, "question", question)


def wait_for_status(qtbot, win, index, status):
    qtbot.waitUntil(lambda: win.run.state.points[index].status == status, timeout=RUN_TIMEOUT)


# -- a run that finishes ---------------------------------------------------------------------


def test_a_single_run_finishes_in_a_runs_folder_with_the_setup_seed(win, qtbot, setup_file):
    with qtbot.waitSignal(win.run.finished, timeout=RUN_TIMEOUT) as blocker:
        win.run.start(ParallelChoice())
    folder = win.run.folder
    assert folder.parent == setup_file.parent / "run-runs"
    assert STAMP.fullmatch(folder.name)
    assert blocker.args[0] is win.run.result
    scan_yaml = (folder / "scan.yaml").read_text()
    assert "keep_seed: true" in scan_yaml
    assert "seed: 12345" in scan_yaml
    assert win.run.state.scan.points()[0].spec.seed == 12345
    assert (folder / "points" / win.run.state.scan.points()[0].id[:16]).is_dir()
    assert not win.run.running


def test_a_two_point_scan_finishes_with_both_points_done(win, qtbot):
    add_scan(win, [10.0, 20.0])
    with qtbot.waitSignal(win.run.finished, timeout=RUN_TIMEOUT):
        win.run.start(ParallelChoice(workers=2))
    assert [win.run.state.points[i].status for i in (0, 1)] == ["done", "done"]


def test_a_point_that_fails_is_failed_and_has_a_log(make_window, setup_file, qtbot):
    win = make_window(failing_runner)
    win.document.open(setup_file)
    with qtbot.waitSignal(win.run.finished, timeout=RUN_TIMEOUT):
        win.run.start(ParallelChoice())
    assert win.run.state.points[0].status == "failed"
    log = win.run.state.log_path(0)
    assert log is not None and log.exists()


def test_a_finished_run_remembers_the_rate_and_saves_it(win, qtbot):
    with qtbot.waitSignal(win.run.finished, timeout=RUN_TIMEOUT):
        win.run.start(ParallelChoice())
    assert win.settings.ions_per_s["1"] > 0
    assert load_settings().ions_per_s["1"] == win.settings.ions_per_s["1"]


# -- stop, pause and the lock ----------------------------------------------------------------


def test_stop_ends_a_slow_run_with_its_point_stopped_and_no_child_left(
    make_window, setup_file, qtbot
):
    win = make_window(slow_runner)
    win.document.open(setup_file)
    win.run.start(ParallelChoice())
    wait_for_status(qtbot, win, 0, "running")
    started = time.monotonic()
    with qtbot.waitSignal(win.run.finished, timeout=STOP_TIMEOUT):
        win.run.stop()
    assert time.monotonic() - started < STOP_TIMEOUT / 1000
    assert win.run.state.points[0].status == "stopped"
    assert not win.run.running
    assert multiprocessing.active_children() == []


def test_a_paused_run_starts_no_new_point_until_it_is_resumed(
    make_window, setup_file, qtbot, tmp_path, monkeypatch
):
    gate = tmp_path / "gate"
    monkeypatch.setenv(GATE_VAR, str(gate))  # the workers inherit it when they start
    win = make_window(gated_runner)
    win.document.open(setup_file)
    add_scan(win, [10.0, 20.0])
    win.run.start(ParallelChoice(workers=1))
    wait_for_status(qtbot, win, 0, "running")  # the first point is out, blocked by the gate
    win.run.pause(True)
    gate.touch()
    wait_for_status(qtbot, win, 0, "done")
    qtbot.wait(1500)
    assert win.run.state.paused
    assert win.run.state.points[1].status == "queued"
    assert win.run.running
    with qtbot.waitSignal(win.run.finished, timeout=RUN_TIMEOUT):
        win.run.pause(False)
    assert [win.run.state.points[i].status for i in (0, 1)] == ["done", "done"]


def test_the_setup_is_locked_while_a_run_goes_and_unlocked_after(make_window, setup_file, qtbot):
    win = make_window(slow_runner)
    win.document.open(setup_file)
    win.run.start(ParallelChoice())
    assert win.document.locked
    assert not any(editor.isEnabled() for editor in win.editors.values())
    with pytest.raises(RuntimeError, match="locked while a run is going"):
        win.document.edit(lambda doc: doc.__setitem__("seed", 5))
    with qtbot.waitSignal(win.run.finished, timeout=STOP_TIMEOUT):
        win.run.stop()
    assert not win.document.locked
    assert all(editor.isEnabled() for editor in win.editors.values())
    win.document.edit(lambda doc: doc.__setitem__("seed", 5))
    assert win.document.doc["seed"] == 5


def test_a_scan_already_running_in_the_folder_fails_with_a_readable_message(
    win, qtbot, monkeypatch
):
    def busy(*args, **kwargs):
        raise ScanBusy("a scan is already running in /some/folder")

    monkeypatch.setattr(run_control, "run_scan", busy)
    with qtbot.waitSignal(win.run.failed, timeout=RUN_TIMEOUT) as blocker:
        win.run.start(ParallelChoice())
    assert "already running" in blocker.args[0]
    assert not win.run.running
    assert not win.document.locked


# -- through the window ----------------------------------------------------------------------


def test_start_switches_to_the_progress_tab(win, qtbot):
    with qtbot.waitSignal(win.run.finished, timeout=RUN_TIMEOUT):
        win.start_run()
        assert win.tabs.tabText(win.tabs.currentIndex()) == "Progress"


def test_start_on_an_unsaved_setup_asks_for_a_file_first(
    make_window, boron_data, tmp_path, qtbot, monkeypatch
):
    win = make_window()
    win.document.edit(lambda doc: doc.update(deepcopy(boron_data)))  # complete, no path yet
    target = tmp_path / "new" / "unsaved.yaml"
    target.parent.mkdir()
    monkeypatch.setattr(
        QFileDialog,
        "getSaveFileName",
        lambda *args, **kwargs: (str(target), "Run setup (*.yaml *.yml)"),
    )
    assert win.start_action.isEnabled()
    with qtbot.waitSignal(win.run.finished, timeout=RUN_TIMEOUT):
        win.start_run()
    assert target.exists()
    assert win.run.folder.parent == target.parent / "unsaved-runs"
    assert not win.document.is_modified


def test_cancelling_the_save_starts_no_run(make_window, boron_data, monkeypatch):
    win = make_window()
    win.document.edit(lambda doc: doc.update(deepcopy(boron_data)))
    monkeypatch.setattr(QFileDialog, "getSaveFileName", lambda *args, **kwargs: ("", ""))
    win.start_run()
    assert not win.run.running
    assert win.run.folder is None
    assert win.document.path is None


def test_a_setup_that_cannot_be_planned_shows_the_error_and_starts_nothing(win, monkeypatch):
    from srim.spec import SpecError

    def refuse(*args, **kwargs):
        raise SpecError("the ions are not a whole number")

    shown = []
    monkeypatch.setattr(run_control, "plan_run", refuse)
    monkeypatch.setattr("srim.app.window.plan_run", refuse)  # the window plans the advice too
    monkeypatch.setattr(
        QMessageBox, "warning", lambda parent, title, text, *a, **k: shown.append(text)
    )
    win.start_run()
    assert shown == ["the ions are not a whole number"]
    assert not win.run.running
    assert not win.document.locked


def test_start_is_disabled_for_an_incomplete_setup_and_while_a_run_goes(
    make_window, setup_file, qtbot
):
    win = make_window(slow_runner)
    assert not win.start_action.isEnabled()
    win.document.open(setup_file)
    assert win.start_action.isEnabled()
    win.run.start(ParallelChoice())
    assert not win.start_action.isEnabled()
    assert win.stop_action.isEnabled() and win.pause_action.isEnabled()
    with qtbot.waitSignal(win.run.finished, timeout=STOP_TIMEOUT):
        win.run.stop()
    assert win.start_action.isEnabled()
    assert not win.stop_action.isEnabled()


def test_quit_during_a_run_asks_and_no_keeps_it_going(make_window, setup_file, qtbot, monkeypatch):
    win = make_window(slow_runner)
    win.document.open(setup_file)
    win.run.start(ParallelChoice())
    asked = []
    answer(monkeypatch, QMessageBox.StandardButton.No, asked)
    assert win.close() is False
    assert asked == [STOP_AND_QUIT_TEXT]
    assert win.run.running
    answer(monkeypatch, QMessageBox.StandardButton.Yes)
    with qtbot.waitSignal(win.run.finished, timeout=STOP_TIMEOUT):
        assert win.close() is True
    assert not win.run.running
    assert multiprocessing.active_children() == []


# -- opening a run folder (RunController.load) ---------------------------------------------


def run_once(win, qtbot):
    """Run the window's setup once with the fake runner; returns the run folder."""
    with qtbot.waitSignal(win.run.finished, timeout=RUN_TIMEOUT):
        win.run.start(ParallelChoice())
    return win.run.folder


def test_a_finished_run_loads_into_a_fresh_window(win, make_window, qtbot):
    folder = run_once(win, qtbot)
    fresh = make_window()
    with qtbot.waitSignal(fresh.run.finished, timeout=RUN_TIMEOUT):
        fresh.run.load(folder)
    assert fresh.document.path == folder / "scan.yaml"
    assert not fresh.document.is_modified
    assert fresh.run.snapshot == fresh.document.doc
    assert fresh.run.folder == folder
    assert fresh.run.result is not None
    assert fresh.run.state.finished
    assert fresh.run.state.counts()["done"] == 1
    assert not fresh.run.partial
    assert not fresh.run.running and not fresh.run.loading


def test_a_run_folder_without_a_result_loads_with_its_points_and_a_note(win, make_window, qtbot):
    import shutil

    from srim.app.runs import UNFINISHED_TEXT

    folder = run_once(win, qtbot)
    shutil.rmtree(folder / "result")
    # A real SRIM run writes run.yaml next to its outputs; the fake runner does not.
    (next((folder / "points").iterdir()) / "run.yaml").write_text("x", encoding="utf-8")
    fresh = make_window()
    notes = []
    fresh.run.notice.connect(notes.append)
    with qtbot.waitSignal(fresh.run.finished, timeout=RUN_TIMEOUT):
        fresh.run.load(folder)
    assert notes == [UNFINISHED_TEXT]
    assert fresh.run.partial
    assert fresh.run.state.counts()["done"] == 1


def test_loading_reads_in_a_thread_and_is_loading_until_it_ends(win, make_window, qtbot):
    folder = run_once(win, qtbot)
    fresh = make_window()
    with qtbot.waitSignal(fresh.run.finished, timeout=RUN_TIMEOUT):
        fresh.run.load(folder)
        assert fresh.run.loading  # the GUI thread returns before the read is done
    assert not fresh.run.loading


def test_a_folder_that_is_not_a_run_fails_with_the_problem(make_window, tmp_path, qtbot):
    fresh = make_window()
    messages = []
    fresh.run.failed.connect(messages.append)
    fresh.run.load(tmp_path)
    assert messages == ["The run did not open: not a pysrim2 run folder"]
    assert fresh.document.path is None
    assert fresh.run.folder is None


def test_a_run_folder_whose_setup_cannot_be_read_fails_and_keeps_the_window(
    make_window, tmp_path, qtbot
):
    folder = tmp_path / "broken-runs" / "20261112T000000Z"
    folder.mkdir(parents=True)
    (folder / ".pysrim2-scan").write_text("pysrim2 scan folder\n", encoding="utf-8")
    fresh = make_window()
    messages = []
    fresh.run.failed.connect(messages.append)
    with qtbot.waitSignal(fresh.run.failed, timeout=RUN_TIMEOUT):
        fresh.run.load(folder)
    assert messages and messages[0].startswith("The run did not open: ")
    assert fresh.run.folder is None
    assert not fresh.run.loading


def test_loading_is_refused_while_a_run_goes(make_window, setup_file, qtbot, tmp_path):
    win = make_window(slow_runner)
    win.document.open(setup_file)
    win.run.start(ParallelChoice())
    try:
        with pytest.raises(RuntimeError, match="already going"):
            win.run.load(tmp_path)
    finally:
        with qtbot.waitSignal(win.run.finished, timeout=STOP_TIMEOUT):
            win.run.stop()
