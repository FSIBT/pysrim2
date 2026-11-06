"""The live views (Textual): the plot, the dashboard and their keys, driven with a pilot on a
synthetic event stream (no SRIM, no workers). Every wait has a timeout."""

import asyncio
import queue
import threading

import pytest

pytest.importorskip("textual")
pytest.importorskip("textual_plotext")

from srim.scan import Scan  # noqa: E402
from srim.scan.events import (  # noqa: E402
    PointFailed,
    PointFinished,
    PointQueued,
    PointStarted,
    ScanFinished,
    ScanPaused,
    ScanStarted,
)
from srim.spec import RunSpec  # noqa: E402
from srim.ui.tui.app import DashboardScreen, LogScreen, PlotScreen, ScanApp  # noqa: E402

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
TIMEOUT = 15.0


def scan_of(energies=(10.0, 20.0, 30.0)):
    return Scan(BASE, grid={"ion.energy_keV": list(energies)}, extract=["implant_summary"], seed=7)


def feed_start(events, scan, folder):
    events.put(ScanStarted(len(scan.points()), 2, 7, str(folder), time=0.0))
    for point in scan.points():
        events.put(PointQueued(point.index, point.id))


def feed_result(events, scan, index, value, err=None):
    point = scan.points()[index]
    events.put(PointStarted(index, point.id, 0, 1))
    values = {"mean": value} if err is None else {"mean": value, "mean_err": err}
    events.put(PointFinished(index, point.id, values, 100, 1.0))


async def until(predicate, timeout=TIMEOUT):
    """Wait until ``predicate()`` is true (polling the event loop); fail after ``timeout``."""

    async def poll():
        while not predicate():
            await asyncio.sleep(0.05)

    await asyncio.wait_for(poll(), timeout)


def run(coro):
    return asyncio.run(asyncio.wait_for(coro, 120))


def make_app(scan, tmp_path, view="plot", plot=None):
    events = queue.Queue()
    stop, pause = threading.Event(), threading.Event()
    app = ScanApp(scan, events, stop_event=stop, pause_event=pause, view=view, plot=plot)
    return app, events, stop, pause


def test_the_plot_view_draws_the_series_as_the_results_come_in(tmp_path):
    scan = scan_of()
    app, events, _, _ = make_app(scan, tmp_path)
    feed_start(events, scan, tmp_path)
    feed_result(events, scan, 0, 1.0, err=0.5)
    feed_result(events, scan, 2, 3.0)

    async def scenario():
        async with app.run_test(size=(120, 40)) as pilot:
            await until(lambda: isinstance(app.screen, PlotScreen) and app.screen.data.series)
            screen = app.screen
            assert screen.data.y_label == "mean" and screen.data.x_label == "ion.energy_keV"
            assert len(screen.data.series) == 1  # no second parameter: one series
            assert screen.data.series[0].x == (10.0, 30.0)  # the points with a value
            assert screen.data.series[0].err == (0.5, None)
            assert "done 2/3" in screen.status_text and "queued 1" in screen.status_text
            events.put(None)  # the scan ends: the app exits
            await pilot.pause()

    run(scenario())


def test_the_dashboard_lists_every_point_and_sorts_by_a_column(tmp_path):
    scan = scan_of()
    app, events, _, _ = make_app(scan, tmp_path, view="dashboard")
    feed_start(events, scan, tmp_path)
    feed_result(events, scan, 0, 3.0)
    feed_result(events, scan, 1, 1.0)

    async def scenario():
        async with app.run_test(size=(140, 50)) as pilot:
            await until(lambda: isinstance(app.screen, DashboardScreen))
            await until(lambda: app.screen.table.row_count == 3)
            screen = app.screen
            await until(lambda: "value:mean" in screen.column_ids)
            screen.sort_by("value:mean")
            keys = [row.key.value for row in screen.table.ordered_rows]
            assert keys == ["1", "0", "2"]  # numbers first (1.0, 3.0), the missing last
            screen.sort_by("value:mean", reverse=True)
            keys = [row.key.value for row in screen.table.ordered_rows]
            assert keys == ["0", "1", "2"]
            events.put(None)
            await pilot.pause()

    run(scenario())


def test_p_pauses_and_resumes_the_scan(tmp_path):
    scan = scan_of()
    app, events, _, pause = make_app(scan, tmp_path)
    feed_start(events, scan, tmp_path)

    async def scenario():
        async with app.run_test() as pilot:
            await pilot.press("p")
            assert pause.is_set()
            await pilot.press("p")
            assert not pause.is_set()
            events.put(None)
            await pilot.pause()

    run(scenario())


def test_q_stops_the_scan_and_the_app_exits_when_the_scan_ends(tmp_path):
    scan = scan_of()
    app, events, stop, _ = make_app(scan, tmp_path)
    feed_start(events, scan, tmp_path)

    async def scenario():
        async with app.run_test() as pilot:
            await pilot.press("q")
            assert stop.is_set()
            assert "stopping" in app.note
            assert app.return_code is None  # still running: the scan has not ended
            events.put(ScanFinished(n_done=0, n_failed=0, seconds=1.0, stopped=True))
            events.put(None)  # the scan thread is done
            await until(lambda: app.return_code is not None)
        assert app.return_code == 0

    run(scenario())


def test_ctrl_c_stops_the_scan_too(tmp_path):
    scan = scan_of()
    app, events, stop, _ = make_app(scan, tmp_path)
    feed_start(events, scan, tmp_path)

    async def scenario():
        async with app.run_test() as pilot:
            await pilot.press("ctrl+c")
            assert stop.is_set()
            events.put(None)
            await until(lambda: app.return_code is not None)

    run(scenario())


def test_v_switches_between_the_plot_and_the_dashboard(tmp_path):
    scan = scan_of()
    app, events, _, _ = make_app(scan, tmp_path)
    feed_start(events, scan, tmp_path)

    async def scenario():
        async with app.run_test() as pilot:
            await until(lambda: isinstance(app.screen, PlotScreen))
            await pilot.press("v")
            await until(lambda: isinstance(app.screen, DashboardScreen))
            await pilot.press("v")
            await until(lambda: isinstance(app.screen, PlotScreen))
            events.put(None)
            await pilot.pause()

    run(scenario())


def test_l_opens_the_error_log_of_the_last_failed_point(tmp_path):
    scan = scan_of()
    app, events, _, _ = make_app(scan, tmp_path)
    feed_start(events, scan, tmp_path)
    point = scan.points()[1]
    log_dir = tmp_path / "points" / point.id[:16]
    log_dir.mkdir(parents=True)
    (log_dir / "error.log").write_text("attempt 2: SRIM said no\nTraceback here\n")
    events.put(PointFailed(1, point.id, "SRIM said no", 2, final=True))

    async def scenario():
        async with app.run_test() as pilot:
            await until(lambda: app.state.last_failed == 1)
            await pilot.press("l")
            await until(lambda: isinstance(app.screen, LogScreen))
            assert "Traceback here" in app.screen.text
            await pilot.press("escape")
            await until(lambda: not isinstance(app.screen, LogScreen))
            events.put(None)
            await pilot.pause()

    run(scenario())


def test_a_pause_from_the_scan_shows_in_the_status_line(tmp_path):
    scan = scan_of()
    app, events, _, _ = make_app(scan, tmp_path)
    feed_start(events, scan, tmp_path)
    events.put(ScanPaused(True))

    async def scenario():
        async with app.run_test() as pilot:
            await until(lambda: "[paused]" in app.screen.status_text)
            events.put(None)
            await pilot.pause()

    run(scenario())
