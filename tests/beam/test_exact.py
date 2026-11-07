"""Exact mode: sampled ions, TRIM.DAT, TRIM.IN for a distributed spec, and its run folder."""

import shutil
import subprocess
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from srim import TRIM
from srim.beam.exact import read_trim_dat, sample_ions, write_trim_dat
from srim.rundir import RunDir
from srim.spec import RunSpec, SpecError

TEST_FILES = Path(__file__).resolve().parent.parent.parent / "test_files"


def base():
    """A plain spec as plain data; a 500 nm gold target."""
    return {
        "ion": {"symbol": "B", "energy_keV": 20.0, "angle_deg": 7.0},
        "number_ions": 200,
        "calculation": 1,
        "seed": 12345,
        "settings": {"description": "beam test", "ranges": 1},
        "target": [
            {
                "name": "Au",
                "thickness_nm": 500.0,
                "density_g_cm3": 19.3,
                "elements": [{"symbol": "Au", "stoich": 1.0}],
            }
        ],
    }


def distributed(**ion):
    """``base()`` with a 20 keV +- 1 keV energy Gaussian and the given ``ion`` keys."""
    data = base()
    data["ion"]["energy_keV"] = {"gaussian": {"mean": 20.0, "sigma": 1.0}}
    data["ion"].update(ion)
    return data


def read_lines(path):
    """The CRLF lines of a file (without the trailing 0x1A)."""
    raw = Path(path).read_bytes()
    assert raw.endswith(b"\x1a")
    return raw[:-1].split(b"\r\n")[:-1]


# --- sampling ---------------------------------------------------------------------------


def test_sample_reproducible():
    spec = RunSpec.from_dict(distributed())
    first = sample_ions(spec)
    pd.testing.assert_frame_equal(first, sample_ions(spec))
    assert not np.array_equal(first.energy_keV, sample_ions(spec, seed=spec.seed + 1).energy_keV)
    with_position = RunSpec.from_dict(
        distributed(position={"y_nm": {"gaussian": {"mean": 0.0, "sigma": 2.0}}})
    )
    moved = sample_ions(with_position)
    assert not np.array_equal(first.y_A, moved.y_A)
    np.testing.assert_array_equal(first.energy_keV, moved.energy_keV)


def test_directions():
    spec = RunSpec.from_dict(distributed(energy_keV=20.0, angle_deg=60.0, azimuth_deg=0.0))
    ions = sample_ions(spec, n=5)
    assert list(ions.ion) == [1, 2, 3, 4, 5]
    # cos(60 deg) is 0.5000000000000001 in floating point: compare with a tolerance
    np.testing.assert_allclose(ions.cos_x, 0.5, rtol=0, atol=1e-12)
    np.testing.assert_allclose(ions.cos_y, np.sqrt(3) / 2, rtol=0, atol=1e-12)
    assert np.all(ions.cos_z == 0.0)
    np.testing.assert_allclose(ions.energy_eV, 20000.0)
    np.testing.assert_allclose(ions.depth_A, 0.0)


# --- TRIM.DAT file ------------------------------------------------------------------------


def test_trim_dat_bytes(tmp_path):
    spec = RunSpec.from_dict(distributed(angle_deg={"uniform": {"low": 0.0, "high": 10.0}}))
    ions = sample_ions(spec)
    path = tmp_path / "TRIM.DAT"
    max_eV = write_trim_dat(ions, path, atomic_number=5, description="beam test", seed=spec.seed)
    raw = path.read_bytes()
    assert raw.endswith(b"\x1a")
    assert b"\n" not in raw.replace(b"\r\n", b"")  # CRLF only
    lines = read_lines(path)
    assert len(lines) == 10 + len(ions)
    assert lines[7] == b"beam test"
    assert b"Seed: %d" % spec.seed in lines[2]
    assert b"Ions: %d" % len(ions) in lines[3]
    for line in lines[10:]:
        assert len(line.split()) == 9  # name, Z, energy, x, y, z, cosX, cosY, cosZ
        # SRIM reads the first 5 characters as the name: they must not run into the Z
        assert len(line.split()[0]) == 5 and line[5:7] == b"  "
        assert line.split()[1] == b"5"
    assert max_eV == max(float(line.split()[2]) for line in lines[10:])


def test_trim_dat_read_back_matches_the_sample(tmp_path):
    spec = RunSpec.from_dict(
        distributed(
            angle_deg={"uniform": {"low": 0.0, "high": 30.0}},
            azimuth_deg={"uniform": {"low": 0.0, "high": 360.0}},
            position={"depth_nm": {"uniform": {"low": 10.0, "high": 400.0}}},
        )
    )
    ions = sample_ions(spec)
    path = tmp_path / "TRIM.DAT"
    write_trim_dat(ions, path, atomic_number=5, description="beam test", seed=spec.seed)
    back = read_trim_dat(path)
    assert len(back) == len(ions)
    np.testing.assert_allclose(back.energy_keV, ions.energy_eV / 1000.0, rtol=1e-6)
    np.testing.assert_allclose(back.angle_deg, ions.angle_deg, atol=1e-3)
    np.testing.assert_allclose(back.depth_nm, ions.depth_A / 10.0, rtol=1e-6, atol=1e-6)
    diff = np.abs(back.azimuth_deg - ions.azimuth_deg)
    diff = np.minimum(diff, 360.0 - diff)  # the azimuth is circular
    assert np.max(diff) < 1e-3


def test_write_refusals(tmp_path):
    ions = sample_ions(RunSpec.from_dict(distributed()), n=3)
    bad_direction = ions.copy()
    bad_direction["cos_x"] = -0.5
    with pytest.raises(ValueError):
        write_trim_dat(bad_direction, tmp_path / "a", atomic_number=5, description="d", seed=1)
    not_unit = ions.copy()
    not_unit["cos_y"] = 0.5
    with pytest.raises(ValueError):
        write_trim_dat(not_unit, tmp_path / "b", atomic_number=5, description="d", seed=1)
    too_many = sample_ions(RunSpec.from_dict(distributed()), n=100_000)
    with pytest.raises(ValueError):
        write_trim_dat(too_many, tmp_path / "c", atomic_number=5, description="d", seed=1)


# --- TRIM.IN for an exact spec -------------------------------------------------------------


def trim_in_lines(trim, tmp_path, name):
    directory = tmp_path / name
    directory.mkdir()
    trim._write_input_files(str(directory))
    return (directory / "TRIM.IN").read_bytes().split(b"\r\n"), directory


@pytest.mark.parametrize(("calculation", "srim_cascade"), [(1, "4"), (2, "5")])
def test_trim_in_for_exact(tmp_path, calculation, srim_cascade):
    data = distributed(angle_deg={"uniform": {"low": 0.0, "high": 10.0}})
    data["calculation"] = calculation
    spec = RunSpec.from_dict(data)
    trim = TRIM.from_spec(spec)
    lines, directory = trim_in_lines(trim, tmp_path, "w")
    assert lines[4].split()[0] == srim_cascade.encode()
    ion_fields = lines[2].split()
    energy_keV = float(ion_fields[2])
    dat = read_lines(directory / "TRIM.DAT")[10:]
    energies = [float(line.split()[2]) for line in dat]
    assert int(ion_fields[4]) == len(dat) == spec.number_ions
    assert energy_keV * 1000.0 >= max(energies)
    assert abs(energy_keV * 1000.0 - max(energies)) <= 1e-9 * max(energies)


@pytest.mark.parametrize(
    "energy",
    [
        12.3456789,  # fixed: written as 12345.68 eV by .7g
        {"uniform": {"low": 0.1234567, "high": 9.87654321}},
        {"gaussian": {"mean": 0.3, "sigma": 0.0123456}},
    ],
)
def test_trim_in_energy_is_never_below_the_file(tmp_path, energy):
    spec = RunSpec.from_dict(distributed(energy_keV=energy, angle_deg=0.0, azimuth_deg=0.0))
    lines, directory = trim_in_lines(TRIM.from_spec(spec), tmp_path, "w")
    energy_keV = float(lines[2].split()[2])
    energies = [float(line.split()[2]) for line in read_lines(directory / "TRIM.DAT")[10:]]
    assert energy_keV * 1000.0 >= max(energies)
    assert abs(energy_keV * 1000.0 - max(energies)) <= 1e-9 * max(energies)


def test_trim_beam_is_the_sample():
    spec = RunSpec.from_dict(distributed())
    trim = TRIM.from_spec(spec)
    pd.testing.assert_frame_equal(trim.beam, sample_ions(spec))
    assert TRIM.from_spec(RunSpec.from_dict(base())).beam is None


def test_trim_round_trip():
    spec = RunSpec.from_dict(distributed(angle_deg={"uniform": {"low": 0.0, "high": 10.0}}))
    spec_calc = spec.with_overrides(["calculation=2"])
    for original in (spec, spec_calc):
        assert TRIM.from_spec(original).to_spec().to_dict() == original.to_dict()


def test_refusals():
    too_many = RunSpec.from_dict({**distributed(), "number_ions": 100_000})
    with pytest.raises(SpecError, match="srim.beam.run_beam"):
        TRIM.from_spec(too_many)
    binned = RunSpec.from_dict({**distributed(), "beam": {"mode": "binned"}})
    with pytest.raises(SpecError, match="run_beam"):
        TRIM.from_spec(binned)


# --- running: fake SRIM -------------------------------------------------------------------


@pytest.fixture
def fake_srim(tmp_path, monkeypatch):
    """A SRIM folder whose 'TRIM.exe' only writes RANGE.txt (no real SRIM, no wine)."""
    directory = tmp_path / "srim"
    directory.mkdir()
    (directory / "TRIM.exe").write_bytes(b"x" * 100)
    (directory / ".pysrim2-installed").write_text("abc123\n")
    monkeypatch.setenv("PYSRIM2_HOME", str(tmp_path / "home"))
    monkeypatch.setattr(shutil, "which", lambda name: None)

    def fake_call(cmd, **kw):
        shutil.copy(TEST_FILES / "1" / "RANGE.txt", Path(kw["cwd"]) / "RANGE.txt")

    monkeypatch.setattr(subprocess, "check_call", fake_call)

    class FakeResults:
        def __init__(self, path):
            self.path = path

    monkeypatch.setattr("srim.srim.Results", FakeResults)
    return directory


def histogram_spec(tmp_path):
    (tmp_path / "h.txt").write_text("19.0 1\n20.0 3\n21.0 1\n")
    data = distributed(energy_keV={"histogram": {"file": "h.txt"}})
    return RunSpec.from_dict(data, base_dir=tmp_path)


def test_run_keeps_trim_dat_and_histogram(fake_srim, tmp_path):
    spec = histogram_spec(tmp_path)
    out = tmp_path / "out"
    TRIM.from_spec(spec).run(srim_directory=str(fake_srim), output_dir=out)
    assert (out / "TRIM.DAT").is_file()
    copies = list((out / "beam").glob("*-h.txt"))
    assert len(copies) == 1 and copies[0].name.endswith("-h.txt")
    assert len(copies[0].name.split("-")[0]) == 8
    assert f"file: {copies[0].relative_to(out).as_posix()}" in (out / "run.yaml").read_text()
    moved = tmp_path / "moved"
    shutil.move(str(out), str(moved))
    again = RunSpec.from_yaml(moved / "run.yaml")
    assert again.is_distributed
    assert sample_ions(again).equals(sample_ions(spec))


def test_stale_trim_dat_removed(fake_srim, tmp_path):
    run = RunDir.create(tmp_path / "work", source=fake_srim, wine=False)
    try:
        TRIM.from_spec(RunSpec.from_dict(distributed())).run(run_dir=run)
        assert (run.path / "TRIM.DAT").is_file()
        out = tmp_path / "plain"
        TRIM.from_spec(RunSpec.from_dict(base())).run(run_dir=run, output_dir=out)
        assert not (run.path / "TRIM.DAT").exists()
        assert not (out / "TRIM.DAT").exists()
        assert not (out / "beam").exists()
    finally:
        run.cleanup()


def test_copy_output_files_onto_the_same_folder_keeps_the_beam(tmp_path):
    folder = tmp_path / "run"
    (folder / "beam").mkdir(parents=True)
    (folder / "beam" / "abc-h.txt").write_text("1 1\n")
    TRIM.copy_output_files(str(folder), str(folder))
    assert (folder / "beam" / "abc-h.txt").is_file()


def test_in_place_runs_keep_files_pysrim2_did_not_write(fake_srim, tmp_path):
    own = b"a user's own TRIM.DAT\r\n"
    (fake_srim / "TRIM.DAT").write_bytes(own)
    (fake_srim / "beam").mkdir()
    (fake_srim / "beam" / "notes.txt").write_text("not a histogram copy\n")
    (fake_srim / "beam" / "0123abcd-h.txt").write_text("19.0 1\n")  # a copy pysrim2 made
    TRIM.from_spec(RunSpec.from_dict(base())).run(srim_directory=str(fake_srim))
    assert (fake_srim / "TRIM.DAT").read_bytes() == own
    assert (fake_srim / "beam" / "notes.txt").is_file()
    assert not (fake_srim / "beam" / "0123abcd-h.txt").exists()


def test_in_place_exact_run_refuses_to_overwrite_a_foreign_trim_dat(fake_srim):
    own = b"a user's own TRIM.DAT\r\n"
    (fake_srim / "TRIM.DAT").write_bytes(own)
    with pytest.raises(FileExistsError, match="not written by pysrim2"):
        TRIM.from_spec(RunSpec.from_dict(distributed())).run(srim_directory=str(fake_srim))
    assert (fake_srim / "TRIM.DAT").read_bytes() == own


def test_in_place_runs_replace_and_remove_their_own_trim_dat(fake_srim, tmp_path):
    TRIM.from_spec(RunSpec.from_dict(distributed())).run(srim_directory=str(fake_srim))
    first = (fake_srim / "TRIM.DAT").read_bytes()
    spec = RunSpec.from_dict({**distributed(), "seed": 7})
    TRIM.from_spec(spec).run(srim_directory=str(fake_srim))  # ours: replaced, not refused
    assert (fake_srim / "TRIM.DAT").read_bytes() != first
    TRIM.from_spec(RunSpec.from_dict(base())).run(srim_directory=str(fake_srim))
    assert not (fake_srim / "TRIM.DAT").exists()
