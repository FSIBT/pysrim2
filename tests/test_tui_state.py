"""The live views' state, from scan events (no terminal, no textual)."""

import math
from pathlib import Path

import pytest

from srim.scan import Precision, Scan
from srim.scan.events import (
    BatchFinished,
    PointCached,
    PointFailed,
    PointFinished,
    PointProgress,
    PointQueued,
    PointRetried,
    PointStarted,
    PointStopped,
    ScanFinished,
    ScanPaused,
    ScanStarted,
    WorkerStats,
)
from srim.spec import RunSpec
from srim.ui.tui.state import ScanState, format_hms, format_value, sparkline, status_line

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


def scan_of(energies=(10.0, 20.0, 30.0), **kwargs):
    kwargs.setdefault("extract", ["implant_summary"])
    return Scan(BASE, grid={"ion.energy_keV": list(energies)}, seed=7, **kwargs)


def started(state, scan, workers=2, t=100.0):
    state.apply(ScanStarted(len(scan.points()), workers, 7, "/scan", time=t))
    for point in scan.points():
        state.apply(PointQueued(point.index, point.id, time=t))


def run_point(state, scan, index, *, worker=0, seconds=2.0, values=None):
    point = scan.points()[index]
    state.apply(PointStarted(index, point.id, worker, 1))
    state.apply(PointProgress(index, 0, 100))
    state.apply(PointFinished(index, point.id, values or {"mean": 1.5}, 100, seconds))
    state.apply(WorkerStats(worker, 100 / seconds))


def test_counts_follow_the_points_through_their_statuses():
    scan = scan_of()
    state = ScanState(scan)
    started(state, scan)
    assert state.counts() == {
        "queued": 3,
        "running": 0,
        "done": 0,
        "cached": 0,
        "failed": 0,
        "stopped": 0,
    }
    state.apply(PointStarted(0, scan.points()[0].id, 0, 1))
    assert state.counts()["running"] == 1 and state.counts()["queued"] == 2
    run_point(state, scan, 0)
    assert state.counts()["done"] == 1 and state.finished_points() == 1
    state.apply(PointFailed(1, scan.points()[1].id, "boom", 1, final=True))
    assert state.counts()["failed"] == 1
    state.apply(PointStopped(2, scan.points()[2].id, None))
    assert state.counts()["stopped"] == 1 and state.counts()["queued"] == 0


def test_a_point_whose_units_all_came_from_the_cache_is_cached():
    scan = scan_of((10.0,))
    state = ScanState(scan)
    started(state, scan, workers=1)
    point = scan.points()[0]
    state.apply(PointCached(0, point.id, 0))
    state.apply(PointFinished(0, point.id, {"mean": 2.0}, 100, 0.0))
    assert state.points[0].status == "cached"
    assert state.counts()["cached"] == 1 and state.finished_points() == 1


def test_a_retried_point_is_queued_again_and_its_error_is_a_warning():
    scan = scan_of((10.0,))
    state = ScanState(scan)
    started(state, scan, workers=1)
    point = scan.points()[0]
    state.apply(PointStarted(0, point.id, 0, 1))
    state.apply(PointFailed(0, point.id, "first try fails", 1, final=False))
    state.apply(PointRetried(0, point.id, 2))
    assert state.points[0].status == "queued"
    assert state.points[0].error == "first try fails"
    assert state.last_warnings() == [("warning", "point 0 attempt 1 failed: first try fails")]
    assert state.last_failed is None


def test_a_final_failure_is_an_error_and_is_the_failed_point_to_open():
    scan = scan_of()
    state = ScanState(scan)
    started(state, scan)
    point = scan.points()[1]
    state.apply(PointFailed(1, point.id, "SRIM said no", 2, final=True))
    assert state.last_warnings() == [("error", "point 1: SRIM said no")]
    assert state.failed_point().index == 1


def test_worker_rows_show_the_point_each_worker_runs_and_its_rate():
    scan = scan_of()
    state = ScanState(scan)
    started(state, scan)
    state.apply(PointStarted(0, scan.points()[0].id, 1, 1))
    state.apply(PointProgress(0, 40, 100))
    assert state.workers[1].point == 0
    state.apply(PointFinished(0, scan.points()[0].id, {"mean": 1.0}, 100, 4.0))
    assert state.workers[1].point is None  # free again when its point is done
    state.apply(WorkerStats(1, 25.0))
    assert state.workers[1].ions_per_s == 25.0
    assert list(state.throughput) == [25.0]


def test_paused_and_finished_flags_follow_their_events():
    scan = scan_of((10.0,))
    state = ScanState(scan)
    started(state, scan, workers=1, t=100.0)
    state.apply(ScanPaused(True))
    assert state.paused is True
    state.apply(ScanPaused(False))
    assert state.paused is False
    state.apply(ScanFinished(n_done=0, n_failed=0, seconds=3.0, stopped=True, time=130.0))
    assert state.finished is True and state.stopped is True
    assert state.elapsed(now=500.0) == pytest.approx(30.0)  # frozen at the end


def test_elapsed_runs_until_now_while_the_scan_runs():
    state = ScanState(scan_of((10.0,)))
    state.apply(ScanStarted(1, 1, 7, "/scan", time=100.0))
    assert state.elapsed(now=142.5) == pytest.approx(42.5)
    assert ScanState(scan_of((10.0,))).elapsed(now=5.0) == 0.0  # not started


def test_the_eta_of_a_fixed_count_scan_is_the_points_left_times_their_mean_over_workers():
    scan = scan_of()  # 3 points, 2 workers
    state = ScanState(scan)
    started(state, scan)
    assert state.eta_seconds() is None  # nothing has finished yet
    run_point(state, scan, 0, worker=0, seconds=4.0)
    run_point(state, scan, 1, worker=1, seconds=6.0)
    # one point left, mean 5 s, 2 workers
    assert state.eta_seconds() == pytest.approx(1 * 5.0 / 2)


def test_the_eta_is_zero_when_nothing_is_left():
    scan = scan_of((10.0,))
    state = ScanState(scan)
    started(state, scan, workers=1)
    run_point(state, scan, 0, seconds=2.0)
    assert state.eta_seconds() == 0.0


def precision_scan():
    precision = Precision(target="mean", rel=0.01, min_ions=100, max_ions=1000, batch_ions=100)
    return scan_of((10.0, 20.0), ions=precision)


def test_precision_points_show_their_estimate_and_whether_the_target_is_met():
    scan = precision_scan()
    state = ScanState(scan)
    started(state, scan, workers=1)
    state.apply(PointStarted(0, scan.points()[0].id, 0, 1))
    state.apply(BatchFinished(0, 0, 100, {"mean": [1.0, 0.5]}))  # 0.5 > 1 % of 1.0: not met
    assert state.points[0].values["mean"] == 1.0
    assert state.points[0].values["mean_err"] == 0.5
    assert state.points[0].reached is False
    state.apply(BatchFinished(0, 1, 200, {"mean": [1.0, 0.001]}))
    assert state.points[0].reached is True


def test_the_eta_of_a_precision_scan_uses_the_expected_ions_per_second():
    scan = precision_scan()
    state = ScanState(scan)
    started(state, scan, workers=1)
    state.apply(PointStarted(0, scan.points()[0].id, 0, 1))
    state.apply(PointProgress(0, 100, 100))
    state.apply(PointFinished(0, scan.points()[0].id, {"mean": 1.0, "mean_err": 0.0}, 100, 2.0))
    # point 1 is queued: it is expected to take its first batches (two of 100 ions here),
    # at 0.02 s per ion, on one worker
    assert state.eta_seconds() == pytest.approx(200 * 0.02 / 1)


def test_the_value_columns_come_from_the_first_results_and_skip_error_columns():
    scan = scan_of()
    state = ScanState(scan)
    started(state, scan)
    assert state.value_names == []
    run_point(state, scan, 0, values={"mean": 1.0, "peak": 2.0, "mean_err": 0.1})
    assert state.value_names == ["mean", "peak"]
    assert "value:peak" in [column for column, _ in state.table_columns()]


def test_cells_and_sort_keys_order_the_points():
    scan = scan_of()
    state = ScanState(scan)
    started(state, scan)
    run_point(state, scan, 0, values={"mean": 3.0})
    run_point(state, scan, 2, values={"mean": 1.0})
    assert [state.cell(state.points[0], c) for c in ("index", "param:ion.energy_keV")] == [
        "0",
        "10.0",
    ]
    assert state.cell(state.points[0], "value:mean") == "3"
    assert state.cell(state.points[1], "value:mean") == "—"
    by_value = [row.index for row in state.sorted_rows("value:mean")]
    assert by_value == [2, 0, 1]  # numbers first, in order, then the missing value
    assert [row.index for row in state.sorted_rows("value:mean", reverse=True)] == [0, 2, 1]
    # statuses in their order (queued, running, done, ...): the queued point first
    assert [row.index for row in state.sorted_rows("status")] == [1, 0, 2]
    assert [row.index for row in state.sorted_rows("index", reverse=True)] == [2, 1, 0]


def test_log_path_is_the_points_error_log_in_the_scan_folder():
    scan = scan_of((10.0,))
    state = ScanState(scan)
    assert state.log_path(0) is None  # the folder is known from scan_started only
    started(state, scan, workers=1)
    point = scan.points()[0]
    # a Path, compared as one: its str uses the separators of the platform
    assert state.log_path(0) == Path("/scan") / "points" / point.id[:16] / "error.log"
    assert state.log_path(9) is None


def test_format_helpers():
    assert format_hms(3725.4) == "1:02:05"
    assert format_value(1.23456, 0.0123) == "1.235 ± 0.012"
    assert format_value(2.0) == "2"
    assert format_value(float("nan"), 0.1) == "—"
    assert format_value(None) == "—"


def test_sparkline_scales_values_and_skips_missing_ones():
    assert sparkline([]) == ""
    assert sparkline([None, math.nan]) == ""
    assert sparkline([1, 1, 1]) == "▅▅▅"
    line = sparkline([0, None, 7])
    assert line == "▁█"
    tail = sparkline(range(100), width=10)
    assert tail == sparkline(range(90, 100))  # only the last ten numbers are drawn
    assert tail[0] == "▁" and tail[-1] == "█" and len(tail) == 10


def test_the_status_line_counts_the_points_and_says_when_paused_or_stopping():
    scan = scan_of()
    state = ScanState(scan)
    started(state, scan, workers=2, t=100.0)
    run_point(state, scan, 0, worker=0, seconds=4.0)
    state.apply(PointStarted(1, scan.points()[1].id, 1, 1))
    assert status_line(state, now=110.0) == (
        "done 1/3  running 1  queued 1  failed 0  ions 100  elapsed 0:00:10  eta 0:00:04"
    )
    state.apply(ScanPaused(True))
    assert status_line(state, now=110.0, note="stopping…").endswith("[paused]  stopping…")


def test_the_status_line_of_a_finished_scan_gives_the_outcome():
    scan = scan_of((10.0,))
    state = ScanState(scan)
    started(state, scan, workers=1, t=100.0)
    run_point(state, scan, 0, seconds=2.0)
    state.apply(ScanFinished(n_done=1, n_failed=0, seconds=3.0, stopped=False, time=103.0))
    assert status_line(state, now=999.0) == (
        "finished 1/1  failed 0  stopped 0  ions 100  elapsed 0:00:03"
    )


def test_progress_and_worker_stats_bump_only_the_live_version():
    scan = scan_of()
    state = ScanState(scan)
    started(state, scan, workers=1)
    data, rows, live = state.data_version, state.rows_version, state.live_version
    state.apply(PointProgress(0, 10, 100))
    state.apply(WorkerStats(0, 5.0))
    assert (state.data_version, state.rows_version) == (data, rows)  # no table or plot work
    assert state.live_version == live + 2
    state.apply(PointStarted(0, scan.points()[0].id, 0, 1))
    assert state.rows_version > rows and state.data_version == data  # its row changed
    run_point(state, scan, 0)
    assert state.data_version > data  # a finished point changes the plot's data
