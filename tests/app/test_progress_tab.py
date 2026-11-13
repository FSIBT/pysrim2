"""The Progress tab: the run of the setup as it goes, and as it ended (until the next run).

The tab is driven by a fake controller (a QObject with the controller's signals and a ``state``)
and a :class:`ScanState` fed with scripted events, so no run, SRIM or worker is needed.
"""

from pathlib import Path

import pytest

pytest.importorskip("PySide6")
pytest.importorskip("pytestqt")

from PySide6.QtCore import QObject, Signal  # noqa: E402
from PySide6.QtGui import QAction  # noqa: E402
from runners import fixture_runner  # noqa: E402

from srim.app import progress_tab as progress_module  # noqa: E402
from srim.app.progress_tab import NO_RUN_TEXT, ProgressTab  # noqa: E402
from srim.app.runs import ParallelChoice  # noqa: E402
from srim.app.settings import AppSettings  # noqa: E402
from srim.app.window import MainWindow  # noqa: E402
from srim.scan import Scan  # noqa: E402
from srim.scan.events import (  # noqa: E402
    PointFailed,
    PointFinished,
    PointQueued,
    PointStarted,
    ScanFinished,
    ScanStarted,
    WorkerStats,
)
from srim.spec import RunSpec  # noqa: E402
from srim.ui.tui.state import ScanState  # noqa: E402

BASE = RunSpec.from_dict(
    {
        "ion": {"symbol": "B", "energy_keV": 20.0},
        "number_ions": 100,
        "seed": 1,
        "target": [
            {
                "name": "Si",
                "thickness_nm": 100.0,
                "density_g_cm3": 2.33,
                "elements": [{"symbol": "Si", "stoich": 1.0}],
            }
        ],
    }
)


def scan_of():
    """Three points: the energy of the ion is scanned."""
    return Scan(
        BASE, grid={"ion.energy_keV": [10.0, 20.0, 30.0]}, seed=7, extract=["implant_summary"]
    )


class FakeRun(QObject):
    """The controller's signals and state, without a run."""

    started = Signal()
    updated = Signal()
    finished = Signal(object)
    failed = Signal(str)

    def __init__(self):
        super().__init__()
        self.state = None


def scripted_state(tmp_path):
    """Three points: point 0 done, point 1 failed (its log written), point 2 queued."""
    scan = scan_of()
    state = ScanState(scan)
    ids = [point.id for point in scan.points()]
    folder = tmp_path / "run"
    state.apply(ScanStarted(3, 2, 7, str(folder), time=100.0))
    for point in scan.points():
        state.apply(PointQueued(point.index, point.id, time=100.0))
    state.apply(PointStarted(0, ids[0], 0, 1))
    state.apply(WorkerStats(0, 50.0))
    state.apply(PointFinished(0, ids[0], {"mean": 1.5}, 100, 2.0))
    state.apply(PointStarted(1, ids[1], 1, 1))
    state.apply(PointFailed(1, ids[1], "boom", 1, True))
    log = folder / "points" / ids[1][:16] / "error.log"
    log.parent.mkdir(parents=True)
    log.write_text("boom\n")
    return state, log


@pytest.fixture
def run():
    return FakeRun()


@pytest.fixture
def actions(qtbot):
    return QAction("Pause"), QAction("Stop")


@pytest.fixture
def tab(qtbot, run, actions):
    widget = ProgressTab(run, *actions)
    qtbot.addWidget(widget)
    return widget


@pytest.fixture
def running(tab, run, tmp_path):
    """The tab after a scripted run has started and reported its events."""
    state, log = scripted_state(tmp_path)
    run.state = state
    run.started.emit()
    run.updated.emit()
    return state, log


def statuses(tab):
    model = tab.points_table.model()
    column = [c for c, _ in tab.points_model.columns()].index("status")
    return [model.index(r, column).data() for r in range(model.rowCount())]


def test_no_run_shows_the_placeholder(tab):
    assert tab.summary.text() == "No run yet. Start one from the Run menu."
    assert tab.summary.text() == NO_RUN_TEXT
    assert tab.counts.text() == ""
    assert tab.points_table.model().rowCount() == 0
    assert tab.workers_table.rowCount() == 0
    assert tab.warnings.count() == 0
    assert not tab.open_log_button.isEnabled()
    assert not tab.timer.isActive()


def test_the_counts_and_the_points_follow_the_state(tab, running):
    state, _ = running
    assert tab.counts.text() == "done 1 · queued 1 · failed 1"
    model = tab.points_table.model()
    assert model.rowCount() == 3
    columns = [column_id for column_id, _ in state.table_columns()]
    for row, point in enumerate(state.sorted_rows()):
        for col, column_id in enumerate(columns):
            assert model.index(row, col).data() == state.cell(point, column_id)
    assert statuses(tab) == ["done", "failed", "queued"]


def test_the_summary_is_the_status_line(tab, running):
    state, _ = running
    assert tab.summary.text().startswith("done 1/3")
    assert "failed 1" in tab.summary.text()


def test_clicking_the_status_header_sorts_by_status(tab, running):
    header = tab.points_table.horizontalHeader()
    section = [column_id for column_id, _ in tab.points_model.columns()].index("status")
    header.sectionClicked.emit(section)
    assert statuses(tab) == ["queued", "done", "failed"]
    header.sectionClicked.emit(section)
    assert statuses(tab) == ["failed", "done", "queued"]


def test_the_warnings_show_the_failure_first(tab, running):
    assert tab.warnings.count() == 1
    assert tab.warnings.item(0).text() == "Error: point 1: boom"


def test_the_workers_table_shows_each_worker(tab, running):
    assert tab.workers_table.rowCount() == 2
    row0 = [tab.workers_table.item(0, c).text() for c in range(3)]
    row1 = [tab.workers_table.item(1, c).text() for c in range(3)]
    assert row0 == ["0", "—", "50"]
    assert row1 == ["1", "—", "—"]


def test_open_log_is_enabled_on_the_failed_point_and_opens_its_file(
    tab, running, monkeypatch, qtbot
):
    _, log = running
    opened = []
    monkeypatch.setattr(
        progress_module.QDesktopServices, "openUrl", staticmethod(lambda url: opened.append(url))
    )
    assert tab.open_log_button.isEnabled()
    tab.open_log_button.click()
    assert [Path(url.toLocalFile()) for url in opened] == [Path(log)]


def test_open_log_follows_the_selected_row(tab, running):
    tab.points_table.selectRow(1)  # point 1 is the failed one (rows sorted by index)
    assert tab.open_log_button.isEnabled()
    tab.points_table.selectRow(0)  # point 0 is done and has no log
    assert not tab.open_log_button.isEnabled()


def test_the_timer_runs_only_while_a_run_goes(tab, run):
    assert not tab.timer.isActive()
    run.started.emit()
    assert tab.timer.isActive()
    run.finished.emit(None)
    assert not tab.timer.isActive()


def test_a_failed_run_stops_the_timer_too(tab, run):
    run.started.emit()
    run.failed.emit("The run did not finish")
    assert not tab.timer.isActive()


def test_the_final_state_stays_until_the_next_run(tab, run, running, tmp_path):
    state, _ = running
    run.state.apply(ScanFinished(1, 1, 3.0, False))
    run.finished.emit(None)
    run.updated.emit()
    assert tab.summary.text().startswith("finished 1/3")
    assert tab.points_table.model().rowCount() == 3
    fresh = ScanState(scan_of())
    run.state = fresh
    run.started.emit()
    run.updated.emit()
    assert tab.points_table.model().rowCount() == 3
    assert tab.counts.text() == "queued 3"
    assert tab.summary.text().startswith("done 0/3")


def test_the_window_has_the_progress_tab_with_the_run_actions(qtbot):
    win = MainWindow(AppSettings(route="wine"), checks_fn=lambda route: [])
    qtbot.addWidget(win)
    assert isinstance(win.progress, ProgressTab)
    assert win.tabs.tabText(win.tabs.indexOf(win.progress)) == "Progress"
    assert win.progress.pause_button.defaultAction() is win.pause_action
    assert win.progress.stop_button.defaultAction() is win.stop_action
    assert [win.tabs.tabText(i) for i in range(win.tabs.count())][-2:] == ["Progress", "Results"]


def test_a_real_run_ends_with_its_final_state_on_the_tab(qtbot, tmp_path, boron_data, write_setup):
    """The window's own run (a fake SRIM runner) fills the tab and leaves the final state."""
    win = MainWindow(
        AppSettings(route="wine"), checks_fn=lambda route: [], run_runner=fixture_runner
    )
    qtbot.addWidget(win)
    win.document.open(write_setup(tmp_path / "boron" / "run.yaml", boron_data))
    win.run.start(ParallelChoice())
    with qtbot.waitSignal(win.run.finished, timeout=60_000):
        pass
    assert win.progress.timer.isActive() is False
    assert win.progress.counts.text() == "done 1"
    assert win.progress.summary.text().startswith("finished 1/1")
