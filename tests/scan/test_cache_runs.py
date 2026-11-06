"""run_scan and the result cache: reruns, other scans, precision, failures, locks (no SRIM).

Runs are fake: the runner copies test_files/1 (a real SRIM result) and writes the seed of the
run into the copy, so the extractor ``cache_draw`` gives a seed-dependent value. The runner
appends ``seed ions`` to a calls file (shared with the parent through an environment variable),
which is how the tests count the runs that were really computed. Worker processes are started
with ``spawn``, so the runners are top-level functions. Every wait has a timeout, and a SIGALRM
guard (POSIX) fails a test that hangs instead of blocking the run.
"""

import json
import multiprocessing
import os
import shutil
import signal
import threading
import time
from pathlib import Path

import pandas as pd
import pytest
import yaml

from srim.output import Results
from srim.scan import MergedResults, Precision, Scan, run_scan
from srim.scan.cache import RunCache
from srim.scan.events import PointCached, PointFinished, PointStarted
from srim.scan.extract import register
from srim.spec import RunSpec

FIXTURE = Path(__file__).resolve().parents[2] / "test_files" / "1"
STATE_VAR = "PYSRIM2_TEST_STATE"
CALLS_VAR = "PYSRIM2_TEST_CALLS"
SLEEP_VAR = "PYSRIM2_TEST_SLEEP"
FAIL_VAR = "PYSRIM2_TEST_FAIL_ENERGY"
SHA_VAR = "PYSRIM2_TEST_SRIM_SHA"
GUARD_SECONDS = 120
#: seconds to wait for a child process or a file to appear
WAIT_SECONDS = 120


def draw(seed):
    """The value of a run with this seed: 10 plus a Gaussian draw (sigma 1)."""
    import random

    return 10.0 + random.Random(seed).gauss(0.0, 1.0)


def cache_draw(results, point=None):
    """Extractor: one run's draw, or for merged batches the ion-weighted mean of the draws."""
    if isinstance(results, MergedResults):
        pairs = [(b.seed, b.n_ions) for b in results.batches]
    else:
        pairs = [(int(Path(results.directory, "seed.txt").read_text()), 1)]
    total = sum(weight for _, weight in pairs)
    return {"cache_draw": sum(w * draw(s) for s, w in pairs) / total}


register("cache_draw", cache_draw, replace=True)

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


def energy_of(spec):
    return spec.to_dict()["ion"]["energy_keV"]


def recording_runner(spec):
    """Stands in for SRIM: records the call, optionally sleeps, returns a copy of test_files/1."""
    data = spec.to_dict()
    seed = data["seed"]
    with open(os.environ[CALLS_VAR], "a", encoding="utf-8") as f:
        f.write(f"{seed} {data['number_ions']}\n")
    sleep = float(os.environ.get(SLEEP_VAR, "0") or 0)
    if sleep:
        time.sleep(sleep)
    directory = Path(os.environ[STATE_VAR]) / f"run-{seed}-{time.monotonic_ns()}"
    shutil.copytree(FIXTURE, directory)
    (directory / "seed.txt").write_text(str(seed))
    sha = os.environ.get(SHA_VAR)
    if sha:  # the run records the SRIM build it ran with, as TRIM.run does
        document = {"version": 1, "spec": {}, "provenance": {"srim_sha256": sha}}
        (directory / "run.yaml").write_text(yaml.safe_dump(document))
    return Results(str(directory))


def switchable_runner(spec):
    """recording_runner, except that the energy in FAIL_VAR (if set) fails every time."""
    fail = os.environ.get(FAIL_VAR)
    if fail and energy_of(spec) == float(fail):
        with open(os.environ[CALLS_VAR], "a", encoding="utf-8") as f:
            f.write(f"{spec.to_dict()['seed']} failed\n")
        raise RuntimeError("SRIM said no")
    return recording_runner(spec)


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
    monkeypatch.setenv(CALLS_VAR, str(directory / "calls.txt"))
    monkeypatch.delenv(SLEEP_VAR, raising=False)
    monkeypatch.delenv(FAIL_VAR, raising=False)
    monkeypatch.delenv("PYSRIM2_CACHE", raising=False)
    return directory


def calls(state):
    """The runs computed so far, as ``(seed, ions)`` pairs (failed attempts included)."""
    path = state / "calls.txt"
    if not path.exists():
        return []
    return [tuple(line.split()) for line in path.read_text().splitlines() if line]


class Recorder:
    """An ``on_event`` that keeps the events and checks that each one is JSON-safe."""

    def __init__(self):
        self.events = []

    def __call__(self, event):
        json.loads(event.to_json())
        self.events.append(event)

    def of(self, kind):
        return [e for e in self.events if e.kind == kind]


def fixed_scan(energies=(10.0, 20.0), seed=7):
    return Scan(BASE, grid={"ion.energy_keV": list(energies)}, extract=["cache_draw"], seed=seed)


def precision_scan(precision, energies=(20.0,), seed=7):
    return Scan(
        BASE,
        grid={"ion.energy_keV": list(energies)},
        extract=["cache_draw"],
        ions=precision,
        seed=seed,
    )


def test_a_cache_path_that_holds_other_files_is_refused_before_a_run(tmp_path, state_dir):
    folder = tmp_path / "documents"
    folder.mkdir()
    (folder / "thesis.txt").write_text("keep me")
    with pytest.raises(ValueError, match="not a pysrim2 result cache"):
        run_scan(
            fixed_scan(), workers=1, scan_dir=tmp_path / "s", runner=recording_runner, cache=folder
        )
    assert (folder / "thesis.txt").read_text() == "keep me"
    assert calls(state_dir) == []


def test_an_entry_of_another_srim_build_is_computed_again_and_replaced(
    tmp_path, state_dir, monkeypatch
):
    import srim.scan.executor as executor_module

    cache_dir = tmp_path / "cache"

    def scan(name):
        return run_scan(
            fixed_scan((10.0,)),
            workers=1,
            scan_dir=tmp_path / name,
            runner=recording_runner,
            cache=cache_dir,
        )

    monkeypatch.setattr(executor_module, "current_srim_sha", lambda: "a" * 64)
    monkeypatch.setenv(SHA_VAR, "a" * 64)
    scan("s1")
    assert len(calls(state_dir)) == 1

    monkeypatch.setattr(executor_module, "current_srim_sha", lambda: "b" * 64)
    monkeypatch.setenv(SHA_VAR, "b" * 64)
    scan("s2")  # the entry is another build: computed again, and the entry replaced
    assert len(calls(state_dir)) == 2
    (entry,) = cache_dir.glob("??/*")
    assert yaml.safe_load((entry / "run.yaml").read_text())["provenance"]["srim_sha256"] == "b" * 64

    result = scan("s3")  # the replaced entry is the build's: a hit
    assert len(calls(state_dir)) == 2
    assert result.table["status"].tolist() == ["cached"]


def test_the_cache_folder_is_prepared_once_per_scan(tmp_path, state_dir, monkeypatch):
    calls_to_prepare = []
    original = RunCache.prepare

    def counting(self):
        calls_to_prepare.append(1)
        return original(self)

    monkeypatch.setattr(RunCache, "prepare", counting)
    run_scan(
        fixed_scan(),
        workers=2,
        scan_dir=tmp_path / "s",
        runner=recording_runner,
        cache=tmp_path / "cache",
    )
    assert len(calls_to_prepare) == 1


def test_a_rerun_of_a_finished_scan_runs_nothing_and_gives_the_same_values(tmp_path, state_dir):
    cache_dir = tmp_path / "cache"
    first = run_scan(
        fixed_scan(),
        workers=2,
        scan_dir=tmp_path / "s1",
        runner=recording_runner,
        cache=cache_dir,
        retries=0,
    )
    assert len(calls(state_dir)) == 2
    assert set(first.table["status"]) == {"done"}

    events = Recorder()
    second = run_scan(
        fixed_scan(),
        workers=2,
        scan_dir=tmp_path / "s2",
        runner=recording_runner,
        cache=cache_dir,
        on_event=events,
        retries=0,
    )
    assert len(calls(state_dir)) == 2  # no run was computed again
    assert set(second.table["status"]) == {"cached"}
    assert second.table["cache_draw"].tolist() == first.table["cache_draw"].tolist()
    assert len(events.of("point_cached")) == 2
    assert events.of("point_started") == []
    assert [e.index for e in events.of("point_finished")] == [0, 1]


def test_the_default_cache_is_inside_the_scan_folder(tmp_path, state_dir):
    folder = tmp_path / "scan"
    run_scan(fixed_scan((10.0,)), workers=1, scan_dir=folder, runner=recording_runner)
    assert (folder / "cache" / "CACHEDIR.TAG").is_file()
    assert len(list((folder / "cache").glob("??/*/.complete"))) == 1


def test_pysrim2_cache_overrides_the_default_location(tmp_path, state_dir, monkeypatch):
    shared = tmp_path / "shared-cache"
    monkeypatch.setenv("PYSRIM2_CACHE", str(shared))
    folder = tmp_path / "scan"
    run_scan(fixed_scan((10.0,)), workers=1, scan_dir=folder, runner=recording_runner)
    assert not (folder / "cache").exists()
    assert (shared / "CACHEDIR.TAG").is_file()
    run_scan(fixed_scan((10.0,)), workers=1, scan_dir=tmp_path / "again", runner=recording_runner)
    assert len(calls(state_dir)) == 1


def test_cache_false_stores_nothing(tmp_path, state_dir):
    folder = tmp_path / "scan"
    run_scan(fixed_scan((10.0,)), workers=1, scan_dir=folder, runner=recording_runner, cache=False)
    run_scan(fixed_scan((10.0,)), workers=1, scan_dir=folder, runner=recording_runner, cache=False)
    assert not (folder / "cache").exists()
    assert len(calls(state_dir)) == 2


def test_failed_runs_are_never_cached(tmp_path, state_dir, monkeypatch):
    cache_dir = tmp_path / "cache"
    monkeypatch.setenv(FAIL_VAR, "20.0")
    first = run_scan(
        fixed_scan(),
        workers=2,
        scan_dir=tmp_path / "s1",
        runner=switchable_runner,
        cache=cache_dir,
        retries=0,
    )
    assert sorted(first.table["status"]) == ["done", "failed"]
    monkeypatch.delenv(FAIL_VAR)
    second = run_scan(
        fixed_scan(),
        workers=2,
        scan_dir=tmp_path / "s2",
        runner=switchable_runner,
        cache=cache_dir,
        retries=0,
    )
    assert sorted(second.table["status"]) == ["cached", "done"]
    attempts = calls(state_dir)
    assert sum(1 for c in attempts if c[1] == "failed") == 1
    assert len(attempts) == 3  # 10 keV, the failed 20 keV, then 20 keV again (10 keV is cached)


def _scan_in_child(scan_dir, cache_dir):
    """Child process: one scan of one point (the runner sleeps first, holding the key)."""
    run_scan(
        fixed_scan((20.0,)),
        workers=1,
        scan_dir=scan_dir,
        runner=recording_runner,
        cache=cache_dir,
        retries=0,
    )


def test_two_scans_that_need_the_same_run_compute_it_once(tmp_path, state_dir, monkeypatch):
    cache_dir = tmp_path / "cache"
    monkeypatch.setenv(SLEEP_VAR, "2")
    ctx = multiprocessing.get_context("spawn")
    children = [
        ctx.Process(target=_scan_in_child, args=(str(tmp_path / f"scan{n}"), str(cache_dir)))
        for n in range(2)
    ]
    for child in children:
        child.start()
    for child in children:
        child.join(timeout=WAIT_SECONDS)
    assert [child.exitcode for child in children] == [0, 0]
    assert len(calls(state_dir)) == 1  # one run for the key, however many scans asked
    statuses = sorted(
        status
        for n in range(2)
        for status in pd.read_csv(tmp_path / f"scan{n}" / "result" / "scan.csv")["status"]
    )
    assert statuses == ["cached", "done"]


def test_a_precision_rerun_with_a_tighter_goal_runs_only_the_new_batches(tmp_path, state_dir):
    cache_dir = tmp_path / "cache"
    loose = Precision("cache_draw", rel=0.05, min_ions=20, max_ions=2000, batch_ions=10)
    tight = Precision("cache_draw", rel=0.02, min_ions=20, max_ions=2000, batch_ions=10)
    run_scan(
        precision_scan(loose),
        workers=2,
        scan_dir=tmp_path / "loose",
        runner=recording_runner,
        cache=cache_dir,
        retries=0,
    )
    first_runs = calls(state_dir)
    assert first_runs

    rerun = run_scan(
        precision_scan(tight),
        workers=2,
        scan_dir=tmp_path / "tight",
        runner=recording_runner,
        cache=cache_dir,
        retries=0,
    )
    new_runs = calls(state_dir)[len(first_runs) :]
    assert new_runs  # the tighter goal needs more batches
    assert len(new_runs) == len(set(new_runs))  # no batch computed twice
    assert not set(new_runs) & set(first_runs)
    assert rerun.table["status"].tolist() == ["done"]
    batch_folders = list((tmp_path / "tight" / "points").glob("*/batches/*"))
    assert len(batch_folders) == len(first_runs) + len(new_runs)

    uncached = run_scan(
        precision_scan(tight),
        workers=2,
        scan_dir=tmp_path / "plain",
        runner=recording_runner,
        cache=False,
        retries=0,
    )
    assert rerun.table["cache_draw"].tolist() == uncached.table["cache_draw"].tolist()
    assert rerun.table["n_ions"].tolist() == uncached.table["n_ions"].tolist()


def test_a_precision_point_that_is_all_cached_has_status_cached(tmp_path, state_dir):
    cache_dir = tmp_path / "cache"
    scan = precision_scan(
        Precision("cache_draw", rel=0.05, min_ions=20, max_ions=500, batch_ions=10)
    )
    run_scan(scan, workers=1, scan_dir=tmp_path / "a", runner=recording_runner, cache=cache_dir)
    before = len(calls(state_dir))
    again = run_scan(
        scan, workers=1, scan_dir=tmp_path / "b", runner=recording_runner, cache=cache_dir
    )
    assert len(calls(state_dir)) == before
    assert again.table["status"].tolist() == ["cached"]


def test_an_all_cached_point_that_hit_max_ions_keeps_max_ions(tmp_path, state_dir):
    cache_dir = tmp_path / "cache"
    scan = precision_scan(
        Precision("cache_draw", rel=0.0001, min_ions=10, max_ions=20, batch_ions=10)
    )
    with pytest.warns(UserWarning, match="max_ions"):
        first = run_scan(
            scan, workers=1, scan_dir=tmp_path / "a", runner=recording_runner, cache=cache_dir
        )
    assert first.table["status"].tolist() == ["max_ions"]
    before = len(calls(state_dir))
    with pytest.warns(UserWarning, match="max_ions"):
        again = run_scan(
            scan, workers=1, scan_dir=tmp_path / "b", runner=recording_runner, cache=cache_dir
        )
    assert len(calls(state_dir)) == before  # every batch came from the cache
    assert again.table["status"].tolist() == ["max_ions"]


def test_a_cached_unit_gives_a_point_cached_event_before_its_finish(tmp_path, state_dir):
    cache_dir = tmp_path / "cache"
    first = run_scan(
        fixed_scan((10.0,)),
        workers=1,
        scan_dir=tmp_path / "a",
        runner=recording_runner,
        cache=cache_dir,
    )
    events = Recorder()
    run_scan(
        fixed_scan((10.0,)),
        workers=1,
        scan_dir=tmp_path / "b",
        runner=recording_runner,
        cache=cache_dir,
        on_event=events,
    )
    kinds = [e.kind for e in events.events if getattr(e, "index", None) == 0]
    assert kinds.index("point_cached") < kinds.index("point_finished")
    assert not any(isinstance(e, PointStarted) for e in events.events)
    cached = events.of("point_cached")[0]
    assert isinstance(cached, PointCached) and (cached.index, cached.batch) == (0, 0)
    done_event = events.of("point_finished")[0]
    assert isinstance(done_event, PointFinished)
    assert done_event.to_dict()["values"]["cache_draw"] == first.table["cache_draw"].iloc[0]
