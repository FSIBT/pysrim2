"""Several pysrim2 processes run SRIM at the same time (``pytest -m srim``).

Each ``run()`` has a private run directory (and wine prefix), so the runs must not
disturb each other, whatever happens to one of them.
"""

import concurrent.futures as cf
import multiprocessing
import os
import shutil
import sys
import threading
from pathlib import Path

import pytest

from srim import TRIM, Ion, Layer, Target
from srim.paths import srim_dir
from srim.rundir import RunDir

ENERGIES_EV = (20e3, 60e3, 150e3, 400e3)


def make_trim(energy):
    target = Target([Layer.from_formula("Si", density=2.33, width=40000.0)])
    return TRIM(target, Ion("B", energy=energy), number_ions=60, calculation=1, random_seed=4711)


def mean_depth(results):
    r = results.range
    return float((r.depth * r.ions).sum() / r.ions.sum())


def worker(energy, base):
    """Run one TRIM in this process; return (energy, mean depth, run directory)."""
    results = make_trim(energy).run(work_dir=base)
    return energy, mean_depth(results), results.directory, results.spec["ion"]["energy_keV"]


def failing_worker(base):
    """A run whose SRIM is killed half-way (or made to fail where wine is not used)."""
    import srim.srim as srim_module

    rd = RunDir.create(base)
    if rd.wine_prefix is not None:
        threading.Timer(4.0, rd.stop_wine).start()  # wineserver -k in this run's prefix only
    else:
        script = "import time; time.sleep(4); raise SystemExit(3)"
        srim_module._launch_command = lambda exe: [sys.executable, "-c", script]
    target = Target([Layer.from_formula("Si", density=2.33, width=40000.0)])
    big = TRIM(target, Ion("B", energy=100e3), number_ions=100000, calculation=1)
    try:
        big.run(run_dir=rd)
    except Exception as exc:
        return type(exc).__name__, str(rd.path), rd.path.is_dir()
    return "no error", str(rd.path), True


def snapshot(root):
    """What "unchanged" means: the names, and the size and mtime of every regular file.
    Not link counts, ctimes or folder mtimes (creating a hard link changes those)."""
    out = {}
    for dirpath, dirs, files in os.walk(root):
        for name in dirs:
            out[os.path.relpath(os.path.join(dirpath, name), root)] = None
        for name in files:
            path = os.path.join(dirpath, name)
            st = os.lstat(path)
            out[os.path.relpath(path, root)] = (st.st_size, st.st_mtime_ns)
    return out


@pytest.fixture
def pool():
    ctx = multiprocessing.get_context("spawn")
    with cf.ProcessPoolExecutor(max_workers=5, mp_context=ctx) as executor:
        yield executor


def test_four_concurrent_runs_give_distinct_correct_results(pool, tmp_path):
    before = snapshot(srim_dir())
    futures = [pool.submit(worker, e, str(tmp_path / "runs")) for e in ENERGIES_EV]
    done = [f.result(timeout=1200) for f in futures]
    # every process got the results of its own energy (run.yaml of its own run directory)
    assert [d[3] for d in done] == [e / 1e3 for e in ENERGIES_EV]
    assert len({d[2] for d in done}) == 4
    depths = [d[1] for d in done]
    print("mean depths (A):", depths)
    assert depths == sorted(depths) and len(set(depths)) == 4  # deeper with energy
    # the installed SRIM was not written to
    assert snapshot(srim_dir()) == before
    # a serial run of one energy agrees with its concurrent twin (same seed)
    serial = mean_depth(make_trim(ENERGIES_EV[1]).run(work_dir=tmp_path / "serial"))
    assert serial == pytest.approx(depths[1], rel=1e-6)


def test_a_killed_run_does_not_disturb_the_others(pool, tmp_path):
    base = str(tmp_path / "runs")
    bad = pool.submit(failing_worker, base)
    good = [pool.submit(worker, e, base) for e in ENERGIES_EV[:3]]
    kind, kept, exists = bad.result(timeout=1200)
    assert kind != "no error"
    assert exists and kept.startswith(base)  # kept for debugging
    for future, energy in zip(good, ENERGIES_EV, strict=False):
        e, depth, directory, _ = future.result(timeout=1200)
        assert e == energy and depth > 0 and Path(directory, "RANGE.txt").is_file()


def test_in_place_runs_in_a_copy_take_turns(pool, tmp_path):
    copy = tmp_path / "srim copy"
    shutil.copytree(srim_dir(), copy)
    futures = [pool.submit(in_place_worker, str(copy), e) for e in ENERGIES_EV[:2]]
    depths = [f.result(timeout=1200) for f in futures]
    assert depths[0] < depths[1]


def in_place_worker(directory, energy):
    return mean_depth(make_trim(energy).run(srim_directory=directory))
