import os
import shutil

import numpy as np
import pandas as pd
import pytest

from srim.analysis import implant_summary
from srim.output import Range3D, Results
from srim.output._common import SRIMOutputParseError

D = "test_files"
# complete rows in the fixture (the last line of the SRIM file is truncated)
N_ROWS = 996


def test_parse_fixture():
    r = Range3D(os.path.join(D, "1"))
    df = r.df
    assert isinstance(df, pd.DataFrame)
    assert list(df.columns) == ["ion", "depth_A", "y_A", "z_A"]
    assert len(df) == N_ROWS == r.num_ions
    assert df["ion"].dtype.kind == "i"
    assert list(df["ion"][:3]) == [1, 2, 3]
    assert df["ion"].iloc[-1] == N_ROWS
    assert df.iloc[0].tolist() == [1, 14281.0, -3640.8, 1405.5]
    assert r.metadata["ion"] == "Ni"
    assert r.metadata["energy_eV"] == pytest.approx(5_000_000.0)
    assert r.metadata["num_ions"] == N_ROWS
    assert r.metadata["source"].endswith("RANGE_3D.txt")
    np.testing.assert_array_equal(df["depth_A"].to_numpy(), r.depth)


def test_comma_decimal_matches_dot():
    dot = Range3D(os.path.join(D, "1")).df
    comma = Range3D(os.path.join(D, "comma")).df
    pd.testing.assert_frame_equal(dot, comma)


def test_implant_summary_hand_computation():
    df = pd.DataFrame(
        {
            "ion": [1, 2, 3],
            "depth_A": [100.0, 200.0, 300.0],
            "y_A": [3.0, 0.0, -6.0],
            "z_A": [4.0, 2.0, 8.0],
        }
    )  # r = 5, 2, 10
    s = implant_summary(df)
    assert s["n_ions"] == 3
    assert s["mean_depth_A"] == pytest.approx(200.0)
    assert s["straggle_depth_A"] == pytest.approx(100.0)  # ddof=1
    assert s["mean_lateral_A"] == pytest.approx(17 / 3)
    assert s["lateral_straggle_A"] == pytest.approx(np.sqrt((25 + 4 + 100) / 3))


def test_implant_summary_fixture_matches_old_example_definition():
    df = Range3D(os.path.join(D, "1")).df
    s = implant_summary(df)
    assert s["mean_depth_A"] == pytest.approx(df["depth_A"].mean())
    assert s["lateral_straggle_A"] == pytest.approx((df["y_A"] ** 2 + df["z_A"] ** 2).mean() ** 0.5)
    assert s["straggle_depth_A"] == pytest.approx(df["depth_A"].std())  # pandas ddof=1


def test_results_range3d_present_and_in_dataframes():
    res = Results(os.path.join(D, "1"))
    assert isinstance(res.range3d, Range3D)
    assert "range3d" in res.to_dataframes()


def test_results_range3d_none_when_absent(tmp_path):
    for name in os.listdir(os.path.join(D, "1")):
        if name != "RANGE_3D.txt":
            shutil.copy(os.path.join(D, "1", name), tmp_path / name)
    res = Results(str(tmp_path))
    assert res.range3d is None
    assert "range3d" not in res.to_dataframes()


def test_unparseable_file_raises(tmp_path):
    (tmp_path / "RANGE_3D.txt").write_bytes(b"nothing here\r\n")
    with pytest.raises(SRIMOutputParseError):
        Range3D(str(tmp_path))
