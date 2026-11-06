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
