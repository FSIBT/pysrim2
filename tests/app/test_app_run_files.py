"""The files an app run leaves in its folder: the same ones a detached run writes (runner.json,
events.jsonl), so a reader with no window (the headless backend here) reads the run's state.

The runs use the fake runners of ``tests/app/runners.py``; the readers are headless backends.
"""

import json
import os
import subprocess
import sys

import pytest

pytest.importorskip("PySide6")
pytest.importorskip("pytestqt")

from runners import fixture_runner, slow_runner  # noqa: E402

from srim.app.runs import ParallelChoice  # noqa: E402
from srim.app.settings import AppSettings  # noqa: E402
from srim.app.window import MainWindow  # noqa: E402
from srim.mcp.detached import EVENTS_FILE, PID_FILE  # noqa: E402
from srim.mcp.headless import HeadlessBackend  # noqa: E402

RUN_TIMEOUT = 60_000
STOP_TIMEOUT = 15_000


@pytest.fixture
def make_window(qtbot):
    def make(runner=fixture_runner):
        win = MainWindow(AppSettings(route="wine"), checks_fn=lambda route: [], run_runner=runner)
        qtbot.addWidget(win)
        return win

    return make


@pytest.fixture
def setup_file(tmp_path, boron_data, write_setup):
    return write_setup(tmp_path / "boron" / "run.yaml", boron_data)


def reader(tmp_path, setup_file):
    """A headless backend that has opened the setup: a second reader with no window."""
    backend = HeadlessBackend(tmp_path, settings=AppSettings(route="wine"))
    backend.open_setup(str(setup_file))
    return backend


def run_once(qtbot, win):
    with qtbot.waitSignal(win.run.finished, timeout=RUN_TIMEOUT):
        win.start_run_with(ParallelChoice(workers=1))
    return win.run.folder


def test_an_app_run_writes_the_runner_file_and_the_events_a_detached_run_writes(
    qtbot, make_window, setup_file
):
    win = make_window()
    win.document.open(setup_file)
    folder = run_once(qtbot, win)
    runner = json.loads((folder / PID_FILE).read_text(encoding="utf-8"))
    assert runner["pid"] == os.getpid()
    assert runner["backend"] == "app"
    assert runner["workers"] == 1
    assert isinstance(runner["started"], float)
    assert "error" not in runner
    events = (folder / EVENTS_FILE).read_text(encoding="utf-8").splitlines()
    assert any(json.loads(line)["kind"] == "scan_finished" for line in events)


def test_an_app_run_stays_readable_after_the_window_moves_on(
    qtbot, make_window, setup_file, tmp_path
):
    win = make_window()
    win.document.open(setup_file)
    folder = run_once(qtbot, win)
    other = make_window()  # the window moves on: nothing of the run is kept in memory
    other.document.open(setup_file)
    second = reader(tmp_path, setup_file)
    assert second.run_status(str(folder))["status"] == "finished"
    assert [row["status"] for row in second.list_runs()] == ["finished"]
    assert second.run_status()["folder"] == str(folder)  # the newest run of the setup


def test_a_still_running_app_run_reads_running_from_another_reader(
    qtbot, make_window, setup_file, tmp_path
):
    win = make_window(runner=slow_runner)
    win.document.open(setup_file)
    win.start_run_with(ParallelChoice(workers=1))
    try:
        folder = win.run.folder
        qtbot.waitUntil(lambda: (folder / "scan.yaml").is_file(), timeout=RUN_TIMEOUT)
        assert reader(tmp_path, setup_file).run_status(str(folder))["status"] == "running"
    finally:
        win.run.stop()
        qtbot.waitUntil(lambda: not win.run.running, timeout=STOP_TIMEOUT)
    assert reader(tmp_path, setup_file).run_status(str(folder))["status"] == "stopped"


def test_an_app_that_died_mid_run_reads_as_crashed(qtbot, make_window, setup_file, tmp_path):
    win = make_window(runner=slow_runner)
    win.document.open(setup_file)
    win.start_run_with(ParallelChoice(workers=1))
    folder = win.run.folder
    try:
        gone = subprocess.Popen([sys.executable, "-c", "pass"])
        gone.wait()  # a pid that is not running any more, as the app's would be after it died
        runner = json.loads((folder / PID_FILE).read_text(encoding="utf-8"))
        runner["pid"] = gone.pid
        (folder / PID_FILE).write_text(json.dumps(runner), encoding="utf-8")
        assert reader(tmp_path, setup_file).run_status(str(folder))["status"] == "crashed"
    finally:
        win.run.stop()
        qtbot.waitUntil(lambda: not win.run.running, timeout=STOP_TIMEOUT)


def test_a_run_that_fails_as_a_whole_reads_as_failed_with_its_error(
    qtbot, make_window, setup_file, tmp_path, monkeypatch
):
    from srim.app import run_control

    def broken_scan(*args, **kwargs):
        raise RuntimeError("the scan broke")

    monkeypatch.setattr(run_control, "run_scan", broken_scan)
    win = make_window()
    win.document.open(setup_file)
    with qtbot.waitSignal(win.run.failed, timeout=RUN_TIMEOUT):
        win.start_run_with(ParallelChoice(workers=1))
    folder = win.run.folder
    state = reader(tmp_path, setup_file).run_status(str(folder))
    assert state["status"] == "failed"
    assert state["errors"] == ["RuntimeError: the scan broke"]


def test_a_second_reader_cannot_pause_or_stop_an_app_run(qtbot, make_window, setup_file, tmp_path):
    from srim.mcp.backend import BackendError
    from srim.ui.web import CONTROL_FILE

    win = make_window(runner=slow_runner)
    win.document.open(setup_file)
    win.start_run_with(ParallelChoice(workers=1))
    folder = win.run.folder
    try:
        qtbot.waitUntil(lambda: (folder / "scan.yaml").is_file(), timeout=RUN_TIMEOUT)
        with pytest.raises(BackendError, match="in the pysrim2 app"):
            reader(tmp_path, setup_file).control_run("pause", str(folder))
        # nothing was written where no run reads it
        assert not (folder / CONTROL_FILE).exists() or (folder / CONTROL_FILE).read_bytes() == b""
        assert reader(tmp_path, setup_file).run_status(str(folder))["status"] == "running"
    finally:
        win.run.stop()
        qtbot.waitUntil(lambda: not win.run.running, timeout=STOP_TIMEOUT)
