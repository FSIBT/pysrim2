"""run_batches: seeds, batch sizes and merging (mocked runner, no SRIM)."""

import warnings
from types import SimpleNamespace

import numpy as np
import pandas as pd
import pytest

from srim.output import Results
from srim.scan import derive_seed, run_batches
from srim.scan.batches import MAX_SEED
from srim.spec import RunSpec

SPEC = RunSpec.from_dict(
    {
        "ion": {"symbol": "B", "energy_keV": 20.0},
        "number_ions": 10,
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


def table(df, num_ions):
    return SimpleNamespace(df=df, metadata={"num_ions": num_ions, "source": "x"})


class FakeResults:
    """A real ``Results`` from test_files/1 with values scaled by ``factor``."""

    closed = 0

    def __init__(self, spec, factor):
        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            real = Results("test_files/1")
        self.spec = spec.to_dict()
        n = spec.number_ions
        for name in ("ioniz", "vacancy", "novac", "etorecoils", "phonons", "range"):
            df = getattr(real, name).df
            scaled = df.copy()
            scaled[scaled.columns[1:]] = df[df.columns[1:]] * factor
            setattr(self, name, table(scaled, n))
        r3 = real.range3d.df
        # ion numbers 1..n of this batch
        r3 = r3.iloc[:n].copy()
        r3["ion"] = np.arange(1, len(r3) + 1)
        r3["depth_A"] = r3["depth_A"] * factor
        self.range3d = table(r3, n)
        self.exyz = self.backscat = self.transmit = self.sputter = self.collision = None
        self.closed = False

    def close(self):
        self.closed = True

    def as_real(self):
        """A real ``Results`` (what a process pool needs: a directory)."""
        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            return Results("test_files/1")


class Runner:
    def __init__(self):
        self.calls = []

    def __call__(self, spec):
        self.calls.append((spec.number_ions, spec.seed))
        return FakeResults(spec, factor=1.0 + 0.5 * len(self.calls))


def test_derive_seed_is_stable_and_in_range():
    assert derive_seed(1, 0) == derive_seed(1, 0)
    # fixed values guard against changes across versions and platforms
    assert [derive_seed(12345, i) for i in range(3)] == [1340274841, 1503228842, 130931954]
    assert derive_seed(0, 0) == 827229657
    seeds = {derive_seed(7, i) for i in range(1000)}
    assert len(seeds) == 1000
    assert all(1 <= s <= MAX_SEED for s in seeds)
    assert derive_seed(1, 0) != derive_seed(2, 0)
    with pytest.raises(ValueError):
        derive_seed(-1, 0)


def test_batch_sizes_and_seeds():
    runner = Runner()
    merged = run_batches(SPEC, 25, 10, seed=99, runner=runner)
    assert [n for n, _ in runner.calls] == [10, 10, 5]
    assert [s for _, s in runner.calls] == [derive_seed(99, i) for i in range(3)]
    assert merged.seeds == [derive_seed(99, i) for i in range(3)]
    assert [b.n_ions for b in merged.batches] == [10, 10, 5]
    assert merged.n_ions == 25 and merged.metadata["base_seed"] == 99
    assert merged.spec["number_ions"] == 25
    # same base seed, same seeds
    assert run_batches(SPEC, 25, 10, seed=99, runner=Runner()).seeds == merged.seeds


def test_random_base_seed_is_recorded():
    merged = run_batches(SPEC, 20, 10, runner=Runner())
    assert merged.seeds == [derive_seed(merged.base_seed, i) for i in range(2)]


def test_depth_profile_is_weighted_mean():
    merged = run_batches(SPEC, 15, 10, seed=1, runner=Runner())
    one = FakeResults(SPEC, 1.0)
    f1, f2 = 1.5, 2.0  # factors of the two batches
    w1, w2 = 10 / 15, 5 / 15
    expected = one.ioniz.df.copy()
    cols = expected.columns[1:]
    expected[cols] = one.ioniz.df[cols] * (w1 * f1 + w2 * f2)
    pd.testing.assert_frame_equal(merged.ioniz.df, expected)
    assert merged.ioniz.metadata["num_ions"] == 15
    assert list(merged.to_dataframes(include=["ioniz", "range"])) == ["ioniz", "range"]
    assert merged.e2recoil is merged.etorecoils


def test_range3d_concat_has_global_ion_numbers():
    merged = run_batches(SPEC, 15, 10, seed=1, runner=Runner())
    df = merged.range3d.df
    assert list(df.columns)[:3] == ["ion", "batch", "depth_A"]
    first, second = df[df.batch == 0], df[df.batch == 1]
    assert first.ion.min() == 1 and second.ion.min() == 11
    assert df.ion.is_unique
    assert merged.range3d.metadata["seeds"] == merged.seeds
    assert merged.batches[0].summary["n_ions"] == len(first)


def test_different_bins_raise():
    class Shifted(Runner):
        def __call__(self, spec):
            r = super().__call__(spec)
            if len(self.calls) == 2:
                r.ioniz.df["depth_A"] = r.ioniz.df["depth_A"] + 1.0
            return r

    merged = run_batches(SPEC, 20, 10, seed=1, runner=Shifted())
    with pytest.raises(ValueError, match="depth bins"):
        _ = merged.ioniz.df


def test_parallel_with_fake_runner_and_close():
    runner = Runner()
    merged = run_batches(SPEC, 30, 10, seed=5, runner=runner, parallel=3)
    assert sorted(runner.calls) == sorted((10, derive_seed(5, i)) for i in range(3))
    assert merged.seeds == [derive_seed(5, i) for i in range(3)]
    parts = list(merged._parts)
    merged.close()
    assert all(p.closed for p in parts)


def test_keep_per_ion_false():
    merged = run_batches(SPEC, 20, 10, seed=1, runner=Runner(), keep_per_ion=False)
    assert merged.range3d is None and merged.collision is None
    assert "range3d" not in merged.to_dataframes()
    assert merged.batches[0].summary is not None


def test_failed_batch_closes_earlier_results():
    made = []

    def runner(spec):
        if made:
            raise RuntimeError("boom")
        made.append(FakeResults(spec, 1.0))
        return made[0]

    with pytest.raises(RuntimeError, match="boom"):
        run_batches(SPEC, 20, 10, seed=1, runner=runner)
    assert made[0].closed


def test_collision_is_streamed_across_batches():
    def events(offset_n):
        return [{"ion": i, "kind": "ion"} for i in range(1, offset_n + 1)]

    def runner(spec):
        r = FakeResults(spec, 1.0)
        r.collision = SimpleNamespace(
            iter_events=lambda n=spec.number_ions: iter(events(n)), metadata={"source": "c"}
        )
        return r

    merged = run_batches(SPEC, 15, 10, seed=1, runner=runner)
    got = list(merged.collision.iter_events())
    assert [e["ion"] for e in got] == list(range(1, 16))
    assert [e["batch"] for e in got] == [0] * 10 + [1] * 5
    assert hasattr(merged.collision.iter_events(), "__next__")


def test_failure_gets_note_sequential():
    def runner(spec):
        raise RuntimeError("boom")

    with pytest.raises(RuntimeError) as info:
        run_batches(SPEC, 20, 10, seed=1, runner=runner)
    assert f"batch 0 of 2, seed {derive_seed(1, 0)}, ions 10" in info.value.__notes__


def test_failure_cancels_queued_batches():
    import threading

    started = []
    lock = threading.Lock()

    def runner(spec):
        with lock:
            started.append(spec.seed)
        raise RuntimeError("boom")

    with pytest.raises(RuntimeError) as info:
        run_batches(SPEC, 100, 10, seed=1, runner=runner, parallel=2)
    assert len(started) < 10
    assert any("batch " in n for n in info.value.__notes__)


def pool_runner(spec):
    return FakeResults(spec, 1.0 + spec.number_ions / 10).as_real()


def test_process_pool_with_picklable_runner(tmp_path):
    merged = run_batches(SPEC, 20, 10, seed=3, runner=pool_runner, parallel=2, pool="process")
    assert merged.seeds == [derive_seed(3, i) for i in range(2)]
    assert merged.batches[0].n_ions == 10
    assert len(merged.range3d.df) > 0
    assert merged.ioniz.metadata["num_ions"] == 20
    merged.close()
