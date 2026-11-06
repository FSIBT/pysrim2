"""run_scan with precision targets: batches, decisions, warnings, determinism, stopping.

The fake runner copies test_files/1 (a real SRIM result) and writes the seed of the run into
the copy. ``seeded_value`` is an extractor whose value is a Gaussian draw per seed (for
merged batches, the ion-weighted mean of the draws), so the batch-means error is real
noise and the decisions depend on the seeds. Worker processes are started with ``spawn``,
so the runners are top-level functions. Every wait has a timeout, and a SIGALRM guard
(POSIX) fails a test that hangs instead of blocking the run.
"""

import json
import os
import random
import shutil
import signal
import threading
import time
from pathlib import Path

import pandas as pd
import pytest

from srim.output import Results
from srim.scan import MergedResults, Precision, Scan, derive_seed, run_scan
from srim.scan.events import (
    BatchFinished,
    PointFailed,
    PointFinished,
    PointProgress,
    PointStopped,
)
from srim.scan.extract import register
from srim.spec import RunSpec

FIXTURE = Path(__file__).resolve().parents[2] / "test_files" / "1"
STATE_VAR = "PYSRIM2_TEST_STATE"
SLOW_SEED_VAR = "PYSRIM2_TEST_SLOW_SEED"
GUARD_SECONDS = 120


def seed_draw(seed):
    """The value of a run with this seed: 10 plus a Gaussian draw (sigma 1)."""
    return 10.0 + random.Random(seed).gauss(0.0, 1.0)


def seeded_value(results, point=None):
    """Extractor: one run's seed draw, or for merged batches the ion-weighted mean."""
    if isinstance(results, MergedResults):
        pairs = [(b.seed, b.n_ions) for b in results.batches]
    else:
        pairs = [(int(Path(results.directory, "seed.txt").read_text()), 1)]
    total = sum(weight for _, weight in pairs)
    return {"seeded_value": sum(w * seed_draw(s) for s, w in pairs) / total}


register("seeded_value", seeded_value, replace=True)

BASE = RunSpec.from_dict(
    {
        "ion": {"symbol": "B", "energy_keV": 20.0},
        "number_ions": 10,
        "seed": 1,
        "settings": {"ranges": 1},
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


def seeded_runner(spec):
    """Stands in for SRIM: a copy of test_files/1 that records the run's seed."""
    seed = spec.to_dict()["seed"]
    directory = Path(os.environ[STATE_VAR]) / f"run-{seed}-{time.monotonic_ns()}"
    shutil.copytree(FIXTURE, directory)
    (directory / "seed.txt").write_text(str(seed))
    return Results(str(directory))


def slow_runner(spec):
    """The batch with the seed in SLOW_SEED_VAR never finishes (until it is stopped)."""
    if spec.to_dict()["seed"] == int(os.environ[SLOW_SEED_VAR]):
        time.sleep(GUARD_SECONDS)
    return seeded_runner(spec)


def flaky_runner(spec):
    """Every run fails its first attempt (the marker file is shared with the parent)."""
    seed = spec.to_dict()["seed"]
    marker = Path(os.environ[STATE_VAR]) / f"tried-{seed}"
    if not marker.exists():
        marker.touch()
        raise RuntimeError("first try fails")
    return seeded_runner(spec)


@pytest.fixture(autouse=True)
def hang_guard():
    """Fail (instead of hanging) if a test runs longer than GUARD_SECONDS (POSIX)."""
    if not hasattr(signal, "SIGALRM") or threading.current_thread() is not threading.main_thread():
        yield
        return

    def alarm(signum, frame):
        raise TimeoutError(f"test ran longer than {GUARD_SECONDS} s")

    previous = signal.signal(signal.SIGALRM, alarm)
    signal.alarm(GUARD_SECONDS)
    try:
        yield
    finally:
        signal.alarm(0)
        signal.signal(signal.SIGALRM, previous)


@pytest.fixture
def state_dir(tmp_path, monkeypatch):
    directory = tmp_path / "state"
    directory.mkdir()
    monkeypatch.setenv(STATE_VAR, str(directory))
    return directory


class Recorder:
    """An ``on_event`` that keeps the events and checks that each one is JSON-safe."""

    def __init__(self, after=None):
        self.events = []
        self.after = after

    def __call__(self, event):
        json.loads(event.to_json())
        self.events.append(event)
        if self.after is not None:
            self.after(event)

    def of(self, kind):
        return [e for e in self.events if e.kind == kind]


def point_seed(scan, index):
    return derive_seed(scan.seed, int(scan.points()[index].id[:15], 16))


def precision_scan(precision, energies=(20.0,), seed=7):
    return Scan(
        BASE,
        grid={"ion.energy_keV": list(energies)},
        extract=["seeded_value"],
        ions=precision,
        seed=seed,
    )


def batch_dirs(folder, scan, index):
    return sorted(
        int(p.name)
        for p in (folder / "points" / scan.points()[index].id[:16] / "batches").iterdir()
    )


def test_a_point_converges_to_its_goal_and_every_batch_is_recorded(tmp_path, state_dir):
    scan = precision_scan(
        Precision("seeded_value", rel=0.02, min_ions=20, max_ions=2000, batch_ions=10)
    )
    folder = tmp_path / "s"
    recorder = Recorder()
    result = run_scan(scan, workers=2, runner=seeded_runner, scan_dir=folder, on_event=recorder)

    row = result.table.iloc[0]
    assert row["status"] == "done", row["error"]
    value, err = row["seeded_value"], row["seeded_value_err"]
    assert err <= 0.02 * abs(value)
    assert 20 <= row["n_ions"] <= 2000 and row["n_ions"] % 10 == 0
    n_batches = int(row["n_ions"]) // 10
    assert len(row["seeds"].split(",")) == n_batches
    assert batch_dirs(folder, scan, 0) == list(range(n_batches))
    seeds = [derive_seed(point_seed(scan, 0), b) for b in range(n_batches)]
    assert row["seeds"] == ",".join(str(s) for s in seeds)
    # the value is the ion-weighted mean of the batch draws, the error their standard error
    draws = [seed_draw(s) for s in seeds]
    assert value == pytest.approx(sum(draws) / n_batches)

    decisions = recorder.of("batch_finished")
    ions = [e.ions for e in decisions]
    assert ions == sorted(set(ions)) and ions[-1] == row["n_ions"]
    assert decisions[-1].value_estimates["seeded_value"][0] == pytest.approx(value)
    assert len(recorder.of("point_finished")) == 1
    finished = recorder.of("point_finished")[0]
    assert finished.values["seeded_value_err"] == pytest.approx(err)  # noqa: PD011 (an event)


def test_a_target_that_is_never_met_stops_at_max_ions_with_a_warning(tmp_path, state_dir):
    scan = precision_scan(
        Precision("seeded_value", abs=1e-9, min_ions=20, max_ions=60, batch_ions=10)
    )
    with pytest.warns(UserWarning, match=r"precision target not reached for scan point 0"):
        result = run_scan(
            scan, workers=2, runner=seeded_runner, scan_dir=tmp_path / "s", on_event=Recorder()
        )
    row = result.table.iloc[0]
    assert row["status"] == "max_ions"
    assert row["n_ions"] == 60
    assert row["seeded_value_err"] > 1e-9


def test_the_table_does_not_depend_on_the_number_of_workers(tmp_path, state_dir):
    scan = precision_scan(
        Precision("seeded_value", rel=0.05, min_ions=20, max_ions=400, batch_ions=10),
        energies=(10.0, 20.0),
    )
    one = run_scan(scan, workers=1, runner=seeded_runner, scan_dir=tmp_path / "one")
    three = run_scan(scan, workers=3, runner=seeded_runner, scan_dir=tmp_path / "three")
    pd.testing.assert_frame_equal(one.table, three.table)
    assert one.table["status"].tolist() == ["done", "done"]


def test_a_target_that_is_not_a_column_fails_the_point_without_retries(tmp_path, state_dir):
    scan = precision_scan(
        Precision("no_such_value", rel=0.1, min_ions=20, max_ions=100, batch_ions=10)
    )
    recorder = Recorder()
    result = run_scan(
        scan,
        workers=2,
        runner=seeded_runner,
        scan_dir=tmp_path / "s",
        retries=2,
        on_event=recorder,
    )
    row = result.table.iloc[0]
    assert row["status"] == "failed"
    assert "precision target 'no_such_value' is not an extracted column" in row["error"]
    assert "seeded_value" in row["error"]
    assert recorder.of("point_retried") == []
    final = [e for e in recorder.of("point_failed") if e.final]
    assert len(final) == 1


def test_a_stop_keeps_the_finished_batches_as_a_partial_estimate(tmp_path, state_dir, monkeypatch):
    scan = precision_scan(
        Precision("seeded_value", rel=0.001, min_ions=20, max_ions=2000, batch_ions=10)
    )
    # the second batch never finishes; the first one does, then the scan is stopped
    monkeypatch.setenv(SLOW_SEED_VAR, str(derive_seed(point_seed(scan, 0), 1)))
    stop = threading.Event()

    def stop_after_first_batch(event):
        if event.kind == "point_progress" and event.ions_done > 0:
            stop.set()

    recorder = Recorder(after=stop_after_first_batch)
    folder = tmp_path / "s"
    result = run_scan(
        scan,
        workers=2,
        runner=slow_runner,
        scan_dir=folder,
        stop_event=stop,
        on_event=recorder,
    )
    row = result.table.iloc[0]
    assert row["status"] == "stopped"
    assert row["n_ions"] == 10
    assert row["seeds"] == str(derive_seed(point_seed(scan, 0), 0))
    assert row["seeded_value"] == pytest.approx(seed_draw(derive_seed(point_seed(scan, 0), 0)))
    assert row["seeded_value_err"] != row["seeded_value_err"]  # NaN: one batch has no error
    assert batch_dirs(folder, scan, 0) == [0]  # the unfinished batch is removed
    assert [e.index for e in recorder.of("point_stopped")] == [0]
    assert isinstance(recorder.of("point_stopped")[0], PointStopped)


def test_a_failed_batch_is_retried_with_the_same_seed(tmp_path, state_dir):
    scan = precision_scan(
        Precision("seeded_value", rel=0.05, min_ions=20, max_ions=400, batch_ions=10)
    )
    recorder = Recorder()
    flaky = run_scan(
        scan, workers=1, runner=flaky_runner, scan_dir=tmp_path / "flaky", on_event=recorder
    )
    steady = run_scan(scan, workers=1, runner=seeded_runner, scan_dir=tmp_path / "steady")

    assert flaky.table["status"].tolist() == ["done"]
    assert recorder.of("point_retried")  # every batch failed once
    assert all(isinstance(e, PointFailed) and not e.final for e in recorder.of("point_failed"))
    pd.testing.assert_frame_equal(flaky.table, steady.table)


def test_the_decision_waits_for_every_batch_planned_so_far(tmp_path, state_dir):
    scan = precision_scan(
        Precision("seeded_value", abs=0.5, min_ions=20, max_ions=400, batch_ions=10)
    )
    recorder = Recorder()
    run_scan(scan, workers=3, runner=seeded_runner, scan_dir=tmp_path / "s", on_event=recorder)
    decisions = recorder.of("batch_finished")
    planned = [e.ions for e in decisions]
    assert planned == sorted(planned)
    progress = recorder.of("point_progress")
    assert all(isinstance(e, PointProgress) for e in progress)
    assert len(recorder.of("point_finished")) == 1
    assert isinstance(recorder.of("point_finished")[0], PointFinished)
    assert isinstance(decisions[0], BatchFinished)
