import os
import shutil

import numpy as np
import pandas as pd
import pytest

from srim.analysis import implant_summary
from srim.output import Range, Range3D, Results
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
    assert s["lateral_range_A"] == pytest.approx((3.5 + 1.0 + 7.0) / 3)
    assert s["lateral_straggle_A"] == pytest.approx(np.std([3, 0, -6, 4, 2, 8], ddof=1))
    assert s["radial_range_A"] == pytest.approx(17 / 3)
    assert s["radial_straggle_A"] == pytest.approx(np.std([5, 2, 10], ddof=1))
    assert s["skewness"] == pytest.approx(0.0)


@pytest.mark.parametrize("directory", ["1", "comma"])
def test_range_header_summary(directory):
    s = Range(os.path.join(D, directory)).summary
    assert s == pytest.approx(
        {
            "mean_depth_A": 15950.0,
            "straggle_depth_A": 3080.0,
            "lateral_range_A": 2843.0,
            "lateral_straggle_A": 3560.0,
            "radial_range_A": 4435.0,
            "radial_straggle_A": 2284.0,
            "skewness": -0.9924,
            "kurtosis": 4.5863,
        }
    )


def test_range_summary_nan_when_header_lines_absent():
    s = Range(os.path.join(D, "SiC-C")).summary
    assert s["mean_depth_A"] == 0.0
    assert np.isnan(s["skewness"]) and np.isnan(s["kurtosis"])


def test_implant_summary_matches_srim_header():
    header = Range(os.path.join(D, "1")).summary
    s = implant_summary(Range3D(os.path.join(D, "1")).df)
    for key, value in header.items():
        assert s[key] == pytest.approx(value, rel=0.02), key


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


def test_results_range3d_found_in_srim_outputs_subfolder(tmp_path):
    for name in os.listdir(os.path.join(D, "1")):
        if name != "RANGE_3D.txt":
            shutil.copy(os.path.join(D, "1", name), tmp_path / name)
    (tmp_path / "SRIM Outputs").mkdir()
    shutil.copy(os.path.join(D, "1", "RANGE_3D.txt"), tmp_path / "SRIM Outputs")
    assert len(Results(str(tmp_path)).range3d.df) == N_ROWS


def _fixture_bytes():
    with open(os.path.join(D, "1", "RANGE_3D.txt"), "rb") as f:
        return f.read()


def test_corrupted_middle_line_warns(tmp_path):
    data = _fixture_bytes()
    start = data.index(b"0000500")
    corrupted = data[:start] + b"0000500   garbage here\r\n" + data[data.index(b"\n", start) + 1 :]
    (tmp_path / "RANGE_3D.txt").write_bytes(corrupted)
    with pytest.warns(UserWarning, match="could not be parsed"):
        r = Range3D(str(tmp_path))
    assert r.num_ions == N_ROWS - 1


def test_truncated_last_line_does_not_warn(recwarn):
    assert len(Range3D(os.path.join(D, "1")).df) == N_ROWS
    assert not [w for w in recwarn if "could not be parsed" in str(w.message)]


def test_truncated_mantissa_last_line_does_not_warn(tmp_path, recwarn):
    data = _fixture_bytes().rstrip(b" ")
    assert not data.endswith(b"\n")
    (tmp_path / "RANGE_3D.txt").write_bytes(data[:-3] + b"-0.")
    assert len(Range3D(str(tmp_path)).df) == N_ROWS
    assert not [w for w in recwarn if "could not be parsed" in str(w.message)]
