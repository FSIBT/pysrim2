"""run_scan with a fixed ion count split into batches (Scan(batch_ions=)).

A point of ``ions`` ions runs as ``ceil(ions / batch_ions)`` batch units, each with the seed
``derive_seed(point_seed, b)`` and the last batch smaller; the batches are merged like a
precision point's (MergedResults), and the ``_err`` columns come from the batch means. The
fake runner copies test_files/1 and records the spec of each run (ions and seed) in a file,
and writes the seed into the copy, so the extractor's values depend on the seeds only.
Worker processes start with ``spawn``, so the runners are top-level functions.
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
from srim.scan import MergedResults, Scan, derive_seed, run_scan
from srim.scan.events import PointFinished, PointProgress
from srim.scan.extract import register
from srim.spec import RunSpec, SpecError

FIXTURE = Path(__file__).resolve().parents[2] / "test_files" / "1"
STATE_VAR = "PYSRIM2_TEST_STATE"
GUARD_SECONDS = 120


def seed_draw(seed):
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
    """Stands in for SRIM: a copy of test_files/1 with the run's seed written into it, and
    the run's ion count and seed appended to the state file."""
    data = spec.to_dict()
    seed = data["seed"]
    state = Path(os.environ[STATE_VAR])
    # one file per call: appends from several processes can lose lines on Windows
    folder = state / "calls"
    folder.mkdir(exist_ok=True)
    name = f"{time.time_ns():020d}-{os.getpid()}-{random.getrandbits(64):016x}.json"
    (folder / name).write_text(json.dumps({"ions": data["number_ions"], "seed": seed}))
    directory = state / f"run-{seed}-{time.monotonic_ns()}"
    shutil.copytree(FIXTURE, directory)
    (directory / "seed.txt").write_text(str(seed), encoding="utf-8")
    return Results(str(directory))


def never_runs(spec):
    raise AssertionError("a cached batch must not be computed again")


@pytest.fixture(autouse=True)
def hang_guard():
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


def calls(state):
    folder = state / "calls"
    if not folder.exists():
        return []
    return [json.loads(p.read_text()) for p in sorted(folder.iterdir())]


def batched_scan(ions=25, batch_ions=10, energies=(20.0,), seed=7, **kwargs):
    kwargs.setdefault("extract", ["seeded_value"])
    return Scan(
        BASE,
        grid={"ion.energy_keV": list(energies)},
        ions=ions,
        batch_ions=batch_ions,
        seed=seed,
        **kwargs,
    )


def test_a_point_runs_as_batches_with_derived_seeds_and_a_smaller_last_batch(tmp_path, state_dir):
    scan = batched_scan(ions=25, batch_ions=10)
    result = run_scan(scan, workers=2, runner=seeded_runner, scan_dir=tmp_path / "s", cache=False)
    point = scan.points()[0]
    base = derive_seed(7, int(point.id[:15], 16))  # the point's seed
    expected = [(10, derive_seed(base, 0)), (10, derive_seed(base, 1)), (5, derive_seed(base, 2))]
    assert sorted((c["ions"], c["seed"]) for c in calls(state_dir)) == sorted(expected)
    row = result.table.iloc[0]
    assert row["status"] == "done"
    assert row["n_ions"] == 25
    assert row["seeds"] == ",".join(str(seed) for _, seed in expected)
    assert row["seeded_value_err"] > 0
    batches = tmp_path / "s" / "points" / point.id[:16] / "batches"
    assert sorted(p.name for p in batches.iterdir()) == ["0", "1", "2"]


def test_the_values_do_not_depend_on_the_number_of_workers(tmp_path, state_dir):
    one = run_scan(
        batched_scan(), workers=1, runner=seeded_runner, scan_dir=tmp_path / "a", cache=False
    )
    three = run_scan(
        batched_scan(), workers=3, runner=seeded_runner, scan_dir=tmp_path / "b", cache=False
    )
    for column in ("seeded_value", "seeded_value_err", "seeds", "n_ions", "status"):
        assert one.table[column].tolist() == three.table[column].tolist()


def test_without_batch_ions_a_point_is_one_run(tmp_path, state_dir):
    scan = Scan(BASE, grid={"ion.energy_keV": [20.0]}, ions=25, seed=7, extract=["seeded_value"])
    result = run_scan(scan, workers=2, runner=seeded_runner, scan_dir=tmp_path / "s", cache=False)
    assert [c["ions"] for c in calls(state_dir)] == [25]
    assert result.table["status"].tolist() == ["done"]


def test_progress_counts_the_ions_of_the_batches(tmp_path, state_dir):
    events = []
    run_scan(
        batched_scan(ions=25, batch_ions=10),
        workers=1,
        runner=seeded_runner,
        scan_dir=tmp_path / "s",
        cache=False,
        on_event=events.append,
    )
    progress = [e for e in events if isinstance(e, PointProgress)]
    assert progress[0].ions_total == 25
    assert progress[-1].ions_done == 25
    finished = [e for e in events if isinstance(e, PointFinished)]
    assert [e.n_ions for e in finished] == [25]


def test_a_longer_run_reuses_the_batches_it_has_in_common(tmp_path, state_dir):
    cache = tmp_path / "cache"
    run_scan(
        batched_scan(ions=25, batch_ions=10),
        workers=2,
        runner=seeded_runner,
        scan_dir=tmp_path / "a",
        cache=cache,
    )
    first_calls = len(calls(state_dir))
    assert first_calls == 3
    longer = run_scan(
        batched_scan(ions=35, batch_ions=10),
        workers=2,
        runner=seeded_runner,
        scan_dir=tmp_path / "b",
        cache=cache,
    )
    # batches 0 and 1 (10 ions each) are cached; batch 2 now has 10 ions and batch 3 has 5
    assert len(calls(state_dir)) - first_calls == 2
    assert longer.table["status"].tolist() == ["done"]
    assert longer.table["n_ions"].tolist() == [35]


def test_a_rerun_of_a_batched_scan_runs_nothing(tmp_path, state_dir):
    cache = tmp_path / "cache"
    first = run_scan(
        batched_scan(), workers=2, runner=seeded_runner, scan_dir=tmp_path / "a", cache=cache
    )
    again = run_scan(
        batched_scan(), workers=2, runner=never_runs, scan_dir=tmp_path / "b", cache=cache
    )
    assert again.table["status"].tolist() == ["cached"]
    assert again.table["seeded_value"].tolist() == first.table["seeded_value"].tolist()
    assert again.table["seeded_value_err"].tolist() == first.table["seeded_value_err"].tolist()


def test_a_batch_ions_scan_with_a_precision_target_is_refused():
    from srim.scan import Precision

    with pytest.raises(SpecError):
        Scan(
            BASE,
            grid={"ion.energy_keV": [20.0]},
            ions=Precision(target="seeded_value", rel=0.05),
            batch_ions=10,
        )


# --- the live record of a batched point while it runs ---------------------------------------

LIVE_NAMES = ("run.live.yaml", "units.live.csv", "beam_bins.live.csv")


def live_units(point_dir):
    """The live record's units (None while there is no live record)."""
    path = point_dir / "units.live.csv"
    return pd.read_csv(path) if path.is_file() else None


def no_live_record(point_dir):
    return not any((point_dir / name).exists() for name in LIVE_NAMES)


def failing_last_batch(spec):
    """Stands in for SRIM: the 5-ion batch of a 25-ion point fails, the others run."""
    if spec.to_dict()["number_ions"] == 5:
        raise RuntimeError("the last batch fails")
    return seeded_runner(spec)


def test_a_batched_point_has_a_live_record_that_follows_its_finished_units(tmp_path, state_dir):
    scan = batched_scan(ions=25, batch_ions=10)
    point_dir = tmp_path / "s" / "points" / scan.points()[0].id[:16]
    seen = []

    def on_event(event):
        if isinstance(event, PointProgress):
            units = live_units(point_dir)
            seen.append((event.ions_done, None if units is None else int(units["n_ions"].sum())))

    run_scan(
        scan,
        workers=1,
        runner=seeded_runner,
        scan_dir=tmp_path / "s",
        cache=False,
        on_event=on_event,
    )
    # the live record never holds more ions than are finished, and it has the finished ones
    assert all(live is None or live == done for done, live in seen)
    assert (10, 10) in seen
    # the final record replaces it
    assert no_live_record(point_dir)
    assert (point_dir / "units.csv").is_file()


def test_the_final_record_is_the_same_when_the_batches_come_from_the_cache(tmp_path, state_dir):
    cache = tmp_path / "cache"
    run_scan(batched_scan(), workers=1, runner=seeded_runner, scan_dir=tmp_path / "a", cache=cache)
    run_scan(batched_scan(), workers=1, runner=never_runs, scan_dir=tmp_path / "b", cache=cache)
    point = batched_scan().points()[0].id[:16]
    for name in ("run.yaml", "units.csv"):
        first = (tmp_path / "a" / "points" / point / name).read_bytes()
        second = (tmp_path / "b" / "points" / point / name).read_bytes()
        assert first == second


def test_a_stopped_point_keeps_no_live_record(tmp_path, state_dir):
    scan = batched_scan(ions=25, batch_ions=10)
    point_dir = tmp_path / "s" / "points" / scan.points()[0].id[:16]
    stop = threading.Event()

    def on_event(event):
        if isinstance(event, PointProgress) and event.ions_done == 10:
            stop.set()

    result = run_scan(
        scan,
        workers=1,
        runner=seeded_runner,
        scan_dir=tmp_path / "s",
        cache=False,
        stop_event=stop,
        on_event=on_event,
    )
    assert result.table["status"].tolist() == ["stopped"]
    assert no_live_record(point_dir)
    assert len(pd.read_csv(point_dir / "units.csv")) == 1  # the finished unit is kept, as before


def test_a_failed_point_keeps_no_live_record(tmp_path, state_dir):
    scan = batched_scan(ions=25, batch_ions=10)
    point_dir = tmp_path / "s" / "points" / scan.points()[0].id[:16]
    result = run_scan(
        scan,
        workers=1,
        runner=failing_last_batch,
        scan_dir=tmp_path / "s",
        cache=False,
        retries=0,
    )
    assert result.table["status"].tolist() == ["failed"]
    assert no_live_record(point_dir)
