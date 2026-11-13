import os
import shutil

import numpy as np
import pandas as pd
import pytest

from srim.output import EXYZ, Backscat, Results, Sputter, Transmit
from srim.output._common import SRIMOutputParseError

D = os.path.join("test_files", "outputs-0.8")

EVENT_COLUMNS = [
    "ion",
    "atomic_number",
    "energy_eV",
    "depth_A",
    "y_A",
    "z_A",
    "cos_x",
    "cos_y",
    "cos_z",
]

# (class, folder, file name, rows in the file)
EVENT_FILES = [
    (Backscat, "thin", "BACKSCAT.txt", 9),
    (Transmit, "thin", "TRANSMIT.txt", 11),
    (Sputter, "sputter", "SPUTTER.txt", 148),
]


def _dir(folder, comma=False):
    return os.path.join(D, folder, "comma") if comma else os.path.join(D, folder)


def test_exyz_fixture():
    e = EXYZ(_dir("exyz"))
    df = e.df
    assert list(df.columns) == [
        "ion",
        "energy_keV",
        "depth_A",
        "y_A",
        "z_A",
        "electronic_stopping_eV_per_A",
        "last_recoil_energy_eV",
    ]
    assert len(df) == 137
    assert df["ion"].dtype.kind == "i"
    assert df.iloc[0].tolist() == pytest.approx([1, 50.0, 0.0, 0.0, 0.0, 13.852, 0.0])
    assert df.iloc[1].tolist() == pytest.approx(
        [1, 48.769, 250.19, -0.90007, 3.0944, 13.642, 4.3448]
    )
    assert sorted(df["ion"].unique()) == list(range(1, 11))
    assert e.metadata["ion"] == "He"
    assert e.metadata["energy_eV"] == pytest.approx(50_000.0)
    assert e.metadata["energy_interval_eV"] == 1000.0
    assert e.metadata["num_ions"] == 10
    assert e.metadata["source"].endswith("EXYZ.txt")
    np.testing.assert_array_equal(df["energy_keV"].to_numpy(), e.energy)
    np.testing.assert_array_equal(df["depth_A"].to_numpy(), e.depth)
    np.testing.assert_array_equal(df["last_recoil_energy_eV"].to_numpy(), e.recoil_energy_loss)
    np.testing.assert_array_equal(df["electronic_stopping_eV_per_A"].to_numpy(), e.electronic_stop)
    # energy never rises along a path
    for _, ion in df.groupby("ion"):
        assert (np.diff(ion["energy_keV"]) <= 0).all()


@pytest.mark.parametrize(("cls", "folder", "name", "rows"), EVENT_FILES)
def test_event_columns_and_rows(cls, folder, name, rows):
    o = cls(_dir(folder))
    df = o.df
    assert list(df.columns) == EVENT_COLUMNS
    assert len(df) == rows == o.num_events
    assert df["ion"].dtype.kind == "i"
    assert df["atomic_number"].dtype.kind == "i"
    for col in EVENT_COLUMNS[2:]:
        assert df[col].dtype.kind == "f"
    norm = np.sqrt(df["cos_x"] ** 2 + df["cos_y"] ** 2 + df["cos_z"] ** 2)
    np.testing.assert_allclose(norm, 1.0, atol=1e-5)
    assert o.metadata["num_events"] == rows
    assert o.metadata["source"].endswith(name)
    np.testing.assert_array_equal(df["energy_eV"].to_numpy(), o.energy)
    np.testing.assert_array_equal(df["cos_z"].to_numpy(), o.cos_z)


def test_backscat_values():
    o = Backscat(_dir("thin"))
    assert o.metadata["ion"] == "H"
    assert o.metadata["energy_eV"] == pytest.approx(15_000.0)
    # depth: minus sign separated from the digits in the file ("-  6202433E-06")
    assert o.df.iloc[0].tolist() == pytest.approx(
        [3, 1, 826.19, -6.202433, -548.6, -1083.0, -0.9003611, -0.3765571, -0.2180701]
    )
    assert (o.depth < 0).all()
    assert (o.cos_x < 0).all()  # moving back out of the front surface
    assert (o.atomic_number == 1).all()


def test_transmit_values():
    o = Transmit(_dir("thin"))
    assert o.df.iloc[0].tolist() == pytest.approx(
        [1, 1, 9050.115, 1000.081, -246.6, -132.1, 0.5652167, -0.2070101, -0.7985468]
    )
    # all transmitted ions are beyond the 1000 A thick target and moving forward
    assert (o.depth >= 1000.0).all()
    assert (o.cos_x > 0).all()
    assert (o.energy < 15_000.0).all()


def test_sputter_values():
    o = Sputter(_dir("sputter"))
    assert o.metadata["ion"] == "Ar"
    assert o.metadata["energy_eV"] == pytest.approx(2000.0)
    assert o.df.iloc[0].tolist() == pytest.approx(
        [1, 29, 6.257294, 0.0, 6.889, -8.758, -0.7026575, -0.3020339, -0.6442422]
    )
    assert (o.atomic_number == 29).all()  # copper
    assert (o.depth == 0.0).all()
    assert (o.cos_x < 0).all()  # leaving the surface
    assert o.num_ions <= 20


@pytest.mark.parametrize(
    ("cls", "folder"),
    [(EXYZ, "exyz"), (Backscat, "thin"), (Transmit, "thin"), (Sputter, "sputter")],
)
def test_comma_decimal_matches_dot(cls, folder):
    dot = cls(_dir(folder)).df
    comma = cls(_dir(folder, comma=True)).df
    assert len(dot) > 0
    pd.testing.assert_frame_equal(dot, comma)


def test_comma_fixtures_really_use_commas():
    for folder, name in [
        ("exyz", "EXYZ.txt"),
        ("thin", "BACKSCAT.txt"),
        ("sputter", "SPUTTER.txt"),
    ]:
        with open(os.path.join(_dir(folder), name), "rb") as f:
            dot = f.read()
        with open(os.path.join(_dir(folder, comma=True), name), "rb") as f:
            comma = f.read()
        assert len(dot) == len(comma)
        assert b"\r\n" in comma
        assert b".0000E+00" not in comma and b".9003611" not in comma


def test_empty_event_file(tmp_path):
    with open(os.path.join(_dir("thin"), "BACKSCAT.txt"), "rb") as f:
        text = f.read()
    header_end = text.index(b"Cos(Z) \r\n") + len(b"Cos(Z) \r\n")
    (tmp_path / "BACKSCAT.txt").write_bytes(text[:header_end])
    o = Backscat(str(tmp_path))
    assert list(o.df.columns) == EVENT_COLUMNS
    assert len(o.df) == 0
    assert o.num_events == 0


def test_event_line_corrupted_warns(tmp_path):
    with open(os.path.join(_dir("sputter"), "SPUTTER.txt"), "rb") as f:
        lines = f.read().split(b"\r\n")
    first = next(i for i, ln in enumerate(lines) if ln.startswith(b"S "))
    lines[first + 3] = b"S    1 29 garbage"
    (tmp_path / "SPUTTER.txt").write_bytes(b"\r\n".join(lines))
    with pytest.warns(UserWarning, match="could not be parsed"):
        o = Sputter(str(tmp_path))
    assert o.num_events == 147


def test_exyz_line_corrupted_warns(tmp_path):
    with open(os.path.join(_dir("exyz"), "EXYZ.txt"), "rb") as f:
        lines = f.read().split(b"\r\n")
    lines[20] = b"0000002 not a number"
    (tmp_path / "EXYZ.txt").write_bytes(b"\r\n".join(lines))
    with pytest.warns(UserWarning, match="EXYZ.txt data section"):
        assert len(EXYZ(str(tmp_path)).df) == 136


def test_truncated_last_line_is_ignored_silently(tmp_path, recwarn):
    with open(os.path.join(_dir("exyz"), "EXYZ.txt"), "rb") as f:
        data = f.read()
    (tmp_path / "EXYZ.txt").write_bytes(data.rstrip(b"\r\n")[:-30])
    assert len(EXYZ(str(tmp_path)).df) == 136
    assert not [w for w in recwarn if "could not be parsed" in str(w.message)]


@pytest.mark.parametrize("cls", [EXYZ, Backscat, Transmit, Sputter])
def test_unparseable_file_raises(cls, tmp_path):
    name = {
        EXYZ: "EXYZ.txt",
        Backscat: "BACKSCAT.txt",
        Transmit: "TRANSMIT.txt",
        Sputter: "SPUTTER.txt",
    }[cls]
    (tmp_path / name).write_bytes(b"nothing here\r\n")
    with pytest.raises(SRIMOutputParseError):
        cls(str(tmp_path))


def _results_dir(tmp_path, folders):
    for name in os.listdir(os.path.join("test_files", "1")):
        shutil.copy(os.path.join("test_files", "1", name), tmp_path / name)
    for folder, name in folders:
        shutil.copy(os.path.join(_dir(folder), name), tmp_path / name)
    return str(tmp_path)


def test_results_none_when_absent_and_keys_omitted():
    res = Results(os.path.join("test_files", "1"))
    assert res.exyz is res.backscat is res.transmit is res.sputter is None
    assert not {"exyz", "backscat", "transmit", "sputter"} & set(res.to_dataframes())


def test_results_finds_event_files(tmp_path):
    files = [
        ("exyz", "EXYZ.txt"),
        ("thin", "BACKSCAT.txt"),
        ("thin", "TRANSMIT.txt"),
        ("sputter", "SPUTTER.txt"),
    ]
    res = Results(_results_dir(tmp_path, files))
    assert isinstance(res.exyz, EXYZ)
    assert isinstance(res.backscat, Backscat)
    assert isinstance(res.transmit, Transmit)
    assert isinstance(res.sputter, Sputter)
    frames = res.to_dataframes()
    assert {"exyz", "backscat", "transmit", "sputter"} <= set(frames)
    assert len(frames["sputter"]) == 148


def test_results_finds_event_files_in_srim_outputs_subfolder(tmp_path):
    directory = _results_dir(tmp_path, [])
    (tmp_path / "SRIM Outputs").mkdir()
    shutil.copy(os.path.join(_dir("thin"), "TRANSMIT.txt"), tmp_path / "SRIM Outputs")
    assert len(Results(directory).transmit.df) == 11


FILE_PARSERS = [
    (EXYZ, "EXYZ.txt", "exyz"),
    (Backscat, "BACKSCAT.txt", "backscat"),
    (Transmit, "TRANSMIT.txt", "transmit"),
    (Sputter, "SPUTTER.txt", "sputter"),
]


@pytest.mark.parametrize(("cls", "name", "key"), FILE_PARSERS)
def test_zero_byte_file_gives_empty_frame_and_warning(cls, name, key, tmp_path):
    (tmp_path / name).write_bytes(b"")
    with pytest.warns(UserWarning, match="is empty"):
        o = cls(str(tmp_path))
    expected = EXYZ(_dir("exyz")).df.columns if cls is EXYZ else Backscat(_dir("thin")).df.columns
    assert list(o.df.columns) == list(expected)
    assert len(o.df) == 0
    assert o.metadata["ion"] is None


@pytest.mark.parametrize(("cls", "name", "key"), FILE_PARSERS)
def test_results_does_not_raise_on_bad_event_file_until_accessed(cls, name, key, tmp_path):
    directory = _results_dir(tmp_path, [])
    (tmp_path / name).write_bytes(b"garbage\r\n")
    res = Results(directory)  # must not raise
    with pytest.raises(SRIMOutputParseError):
        getattr(res, key)


@pytest.mark.parametrize(("cls", "name", "key"), FILE_PARSERS)
def test_results_zero_byte_event_file(cls, name, key, tmp_path):
    directory = _results_dir(tmp_path, [])
    (tmp_path / name).write_bytes(b"")
    res = Results(directory)
    with pytest.warns(UserWarning, match="is empty"):
        assert len(getattr(res, key).df) == 0


def test_results_parses_lazily_and_caches(tmp_path, monkeypatch):
    directory = _results_dir(tmp_path, [("thin", "TRANSMIT.txt")])
    calls = []
    original = Transmit.__init__

    def counting(self, *args, **kwargs):
        calls.append(args)
        original(self, *args, **kwargs)

    monkeypatch.setattr(Transmit, "__init__", counting)
    res = Results(directory)
    assert calls == []
    first = res.transmit
    assert res.transmit is first
    assert len(calls) == 1


def test_streaming_chunks_give_same_result():
    import re

    from srim.output._common import _read_numeric_table

    header_end = re.compile(rb"-+(?:[ \t]+-+)+[ \t]*")
    row = re.compile(
        rb"[ \t]*(\d+)[ \t]+(\S+)[ \t]+(\S+)[ \t]+(\S+)[ \t]+(\S+)[ \t]+(\S+)[ \t]+(\S+)[ \t]*"
    )
    cols = [(0, "i")] + [(j, "f") for j in range(1, 7)]
    path = os.path.join(_dir("exyz"), "EXYZ.txt")
    _, a, _ = _read_numeric_table(path, header_end, row, cols, "x", chunk_rows=7)
    _, b, _ = _read_numeric_table(path, header_end, row, cols, "x")
    for x, y in zip(a, b, strict=True):
        np.testing.assert_array_equal(x, y)
    assert len(a[0]) == 137
