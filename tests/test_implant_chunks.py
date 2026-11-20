"""The streamed implant summary equals the whole-table one (srim.analysis.implant_summary_chunks)"""

import math
import os
import warnings
from types import SimpleNamespace

import numpy as np
import pandas as pd
import pytest

from srim.analysis import implant_summary, implant_summary_chunks
from srim.output import Range3D
from srim.scan import batches

D = "test_files"
KEYS = (
    "n_ions",
    "mean_depth_A",
    "straggle_depth_A",
    "lateral_range_A",
    "lateral_straggle_A",
    "radial_range_A",
    "radial_straggle_A",
    "skewness",
    "kurtosis",
)
EMPTY_KEYS = KEYS[1:] + ("n_ions",)  # the order implant_summary gives for an empty table


def assert_same_summary(got, expected, rtol=1e-12):
    assert list(got) == list(expected)
    assert type(got["n_ions"]) is type(expected["n_ions"]) is int
    assert got["n_ions"] == expected["n_ions"]
    keys = [k for k in expected if k != "n_ions"]
    np.testing.assert_allclose(
        [got[k] for k in keys],
        [expected[k] for k in keys],
        rtol=rtol,
        atol=0.0,
        equal_nan=True,
        err_msg=repr((got, expected)),
    )


def split(df, rows):
    return [df.iloc[i : i + rows] for i in range(0, len(df), rows)]


def offset_frame(n=1024, offset=1e6, seed=3):
    """Depth ~1e6 A with a spread of a few hundred A (a quarter-Angstrom grid, so the
    whole-table sums are exact and the reference is accurate)."""
    rng = np.random.default_rng(seed)
    depth = offset + 0.25 * rng.integers(0, 4096, n)
    y = 0.25 * rng.integers(-800, 800, n) + 1e5
    z = 0.25 * rng.integers(-800, 800, n)
    return pd.DataFrame({"ion": np.arange(1, n + 1), "depth_A": depth, "y_A": y, "z_A": z})


@pytest.mark.parametrize("directory", ["1", "comma"])
@pytest.mark.parametrize("chunk_rows", [1, 7, 10**6])
def test_range3d_fixtures_streamed_equal_whole_table(directory, chunk_rows):
    r = Range3D(os.path.join(D, directory))
    expected = implant_summary(r.df)
    assert expected["n_ions"] == 996
    assert_same_summary(implant_summary_chunks(r.iter_chunks(chunk_rows)), expected)


@pytest.mark.parametrize("chunk_rows", [1, 7, 10**6])
def test_large_offset_small_spread_streamed_equal_whole_table(chunk_rows):
    df = offset_frame()
    expected = implant_summary(df)
    assert expected["mean_depth_A"] > 9.9e5 and expected["straggle_depth_A"] < 1e3
    assert_same_summary(implant_summary_chunks(split(df, chunk_rows)), expected)


@pytest.mark.parametrize("rows", [1, 2, 3])
def test_few_ions_match_including_nan_entries(rows):
    df = pd.DataFrame(
        {
            "ion": [1, 2, 3],
            "depth_A": [100.0, 200.0, 350.0],
            "y_A": [3.0, 0.0, -6.0],
            "z_A": [4.0, 2.0, 8.0],
        }
    )
    for part in (df.iloc[:1], df.iloc[:2], df):
        expected = implant_summary(part)
        assert_same_summary(implant_summary_chunks(split(part, rows)), expected)


def test_one_ion_gives_nan_spreads_and_no_warnings():
    df = pd.DataFrame({"ion": [1], "depth_A": [5.0], "y_A": [1.0], "z_A": [2.0]})
    with warnings.catch_warnings():
        warnings.simplefilter("error")
        s = implant_summary_chunks([df])
    assert s["n_ions"] == 1
    assert math.isnan(s["straggle_depth_A"]) and math.isnan(s["radial_straggle_A"])
    assert math.isnan(s["skewness"]) and math.isnan(s["kurtosis"])
    assert s["lateral_straggle_A"] == pytest.approx(np.std([1.0, 2.0], ddof=1))


def test_identical_depths_give_nan_skewness_like_whole_table():
    df = pd.DataFrame(
        {"ion": [1, 2, 3], "depth_A": [7.0, 7.0, 7.0], "y_A": [1.0, 2.0, 3.0], "z_A": [0.0] * 3}
    )
    expected = implant_summary(df)
    assert_same_summary(implant_summary_chunks(split(df, 2)), expected)
    assert math.isnan(expected["skewness"]) and math.isnan(expected["kurtosis"])


def test_empty_table_identical_to_whole_table_without_warnings():
    empty = pd.DataFrame({"ion": [], "depth_A": [], "y_A": [], "z_A": []})
    expected = implant_summary(empty)
    with warnings.catch_warnings():
        warnings.simplefilter("error")
        from_frame = implant_summary_chunks([empty])
        from_nothing = implant_summary_chunks([])
    for got in (from_frame, from_nothing):
        assert list(got) == list(expected) == list(EMPTY_KEYS)
        assert got["n_ions"] == 0 and type(got["n_ions"]) is int
        assert all(math.isnan(v) for k, v in got.items() if k != "n_ions")


def test_batch_summary_reads_range3d_in_chunks_not_df(monkeypatch):
    path = os.path.join(D, "1")
    expected = implant_summary(Range3D(path).df)
    calls = []
    original = Range3D.iter_chunks

    def spy(self, chunk_rows=50_000):
        calls.append(chunk_rows)
        return original(self, chunk_rows)

    def no_whole_table(self):
        raise AssertionError("the per-point summary read the whole RANGE_3D table")

    monkeypatch.setattr(Range3D, "iter_chunks", spy)
    monkeypatch.setattr(Range3D, "df", property(no_whole_table))
    summary = batches._summary(SimpleNamespace(range3d=Range3D(path)))
    assert len(calls) == 1 and calls[0] >= 1
    monkeypatch.undo()
    assert_same_summary(summary, expected)


def test_batch_summary_without_range3d_is_none():
    assert batches._summary(SimpleNamespace(range3d=None)) is None
