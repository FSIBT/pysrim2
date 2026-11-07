"""Scans of beams: binned points run as bin units, exact points split at the TRIM.DAT limit,
point folders record their units, and ``open_results`` re-opens a batched point (no SRIM).

Runners are top-level functions (worker processes import this module by name). Each run
writes its spec, as YAML, into its own file under ``$PYSRIM2_TEST_BEAM_SPECS``, which is how
the tests see the specs the workers got. The fake runners return ``test_files/1``; the
profile test copies it and scales the ionization profile by the bin energy, so that the units
of a point differ.
"""

import os
import shutil
import time
from pathlib import Path

import numpy as np
import pandas as pd
import pytest
import yaml

from srim.beam import open_results
from srim.beam.binned import make_bins
from srim.output import Results
from srim.scan import Precision, Scan, derive_seed, run_scan
from srim.scan.events import BatchFinished
from srim.scan.extract import register
from srim.spec import RunSpec

FIXTURE = Path(__file__).resolve().parents[2] / "test_files" / "1"
SPEC_DIR_VAR = "PYSRIM2_TEST_BEAM_SPECS"
BASE_SEED = 7


def peak_ioniz(results, point=None):
    """Extractor: the largest ionization by ions (differs between beams when units differ)."""
    return {"peak_ioniz": float(results.ioniz.df["ions_eV_per_A_per_ion"].max())}


register("peak_ioniz", peak_ioniz, replace=True)


def beam_base(*, mode="binned", bins=None, energy=None):
    """A run spec (B in Si, 10-20 keV) with a beam section; ``bins`` as ``beam.bins``."""
    beam = {"mode": mode}
    if bins is not None:
        beam["bins"] = bins
    return RunSpec.from_dict(
        {
            "ion": {
                "symbol": "B",
                "energy_keV": energy or {"uniform": {"low": 10.0, "high": 20.0}},
                "angle_deg": 7.0,
            },
            "number_ions": 10,
            "calculation": 1,
            "seed": 1,
            "settings": {"ranges": 1},
            "beam": beam,
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


def recording_runner(spec):
    """Stands in for SRIM: records the spec (inline, so histograms are complete)."""
    directory = Path(os.environ[SPEC_DIR_VAR])
    name = f"{os.getpid()}-{time.monotonic_ns()}.yaml"
    (directory / name).write_text(
        yaml.safe_dump(spec.to_dict(inline=True), sort_keys=True), encoding="utf-8"
    )
    return Results(str(FIXTURE))


def scale_ioniz(path: Path, factor: float) -> None:
    """Multiply the ionization columns of an IONIZ.txt table by ``factor`` (CRLF lines are
    re-written as LF, which the parser does not mind)."""
    lines = path.read_bytes().splitlines(keepends=True)
    start = next(i for i, line in enumerate(lines) if line.startswith(b"-----")) + 1
    out = []
    for i, line in enumerate(lines):
        fields = line.split()
        if i >= start and len(fields) == 3:
            try:
                float(fields[0])  # the depth must be a number too
                ions, recoils = float(fields[1]), float(fields[2])
            except ValueError:
                out.append(line)
                continue
            line = f"{fields[0].decode()} {ions * factor:.6E} {recoils * factor:.6E}\n".encode()
        out.append(line)
    path.write_bytes(b"".join(out))


def scaled_runner(spec):
    """The fixture with its ionization profile scaled by (bin energy / 10 keV)."""
    energy = spec.to_dict()["ion"]["energy_keV"]
    directory = Path(os.environ[SPEC_DIR_VAR]) / f"unit-{spec.seed}"
    shutil.copytree(FIXTURE, directory)
    scale_ioniz(directory / "IONIZ.txt", float(energy) / 10.0)
    return Results(str(directory))


def point_folder(scan_dir: Path, scan: Scan, index: int = 0) -> Path:
    return Path(scan_dir) / "points" / scan.points()[index].id[:16]


def point_seed(scan: Scan, index: int = 0) -> int:
    return derive_seed(BASE_SEED, int(scan.points()[index].id[:15], 16))


def recorded(directory: Path) -> list[dict]:
    return [yaml.safe_load(p.read_text(encoding="utf-8")) for p in sorted(directory.iterdir())]


@pytest.fixture
def specs_dir(tmp_path, monkeypatch):
    directory = tmp_path / "specs"
    directory.mkdir()
    monkeypatch.setenv(SPEC_DIR_VAR, str(directory))
    return directory


def test_binned_point_units(tmp_path, specs_dir):
    scan = Scan(beam_base(bins={"energy_keV": 5}), ions=1000, seed=BASE_SEED)
    scan_dir = tmp_path / "scan"
    run_scan(scan, workers=2, runner=recording_runner, scan_dir=scan_dir, cache=False)

    specs = recorded(specs_dir)
    assert len(specs) == 5
    bins = make_bins(scan.points()[0].spec)
    assert sorted(s["ion"]["energy_keV"] for s in specs) == sorted(b.energy_keV for b in bins)
    assert all(s["number_ions"] == 200 for s in specs)
    assert all("beam" not in s and isinstance(s["ion"]["energy_keV"], float) for s in specs)
    seeds = {s["seed"] for s in specs}
    assert seeds == {derive_seed(point_seed(scan), u) for u in range(5)}

    folder = point_folder(scan_dir, scan)
    assert (folder / "beam_bins.csv").is_file()
    units = pd.read_csv(folder / "units.csv")
    assert list(units.columns) == ["unit", "beam_batch", "bin", "n_ions", "seed"]
    assert list(units["bin"]) == [0, 1, 2, 3, 4]
    assert set(units["beam_batch"]) == {0}
    beam_bins = pd.read_csv(folder / "beam_bins.csv")
    assert list(beam_bins["n_ions"]) == [200] * 5

    with open_results(folder) as merged:
        assert merged.n_ions == 1000
        assert merged.beam_bins is not None
        df = merged.range3d.df
        assert list(df.columns[1:6]) == [
            "batch",
            "beam_batch",
            "bin",
            "bin_energy_keV",
            "bin_angle_deg",
        ]
        assert set(df["bin"]) == {0, 1, 2, 3, 4}
        assert set(df["bin_energy_keV"].round(9)) == {round(b.energy_keV, 9) for b in bins}


def test_binned_profiles_are_the_ion_weighted_mean_of_the_unit_profiles(tmp_path, specs_dir):
    scan = Scan(beam_base(bins={"energy_keV": [10.0, 12.0, 20.0]}), ions=1000, seed=BASE_SEED)
    scan_dir = tmp_path / "scan"
    run_scan(scan, workers=2, runner=scaled_runner, scan_dir=scan_dir, cache=False)
    folder = point_folder(scan_dir, scan)

    units = pd.read_csv(folder / "units.csv")
    assert list(units["n_ions"]) == [200, 800]  # the bins 10-12 and 12-20 keV: 0.2 and 0.8
    expected = None
    for unit, n_ions in zip(units["unit"], units["n_ions"], strict=True):
        with Results(str(folder / "batches" / str(unit))) as part:
            frame = part.ioniz.df.copy()
        if expected is None:
            expected = frame.copy()
            expected[frame.columns[1:]] = 0.0
        expected[frame.columns[1:]] += frame[frame.columns[1:]].to_numpy() * n_ions / 1000.0

    with open_results(folder) as merged:
        profile = merged.ioniz.df
    assert list(profile.columns) == list(expected.columns)
    np.testing.assert_allclose(profile.to_numpy(), expected.to_numpy(), rtol=1e-12)


def test_binned_precision_runs_whole_beams_and_decides_per_beam(tmp_path, specs_dir):
    precision = Precision(target="peak_ioniz", rel=0.5, batch_ions=500, max_ions=2000)
    scan = Scan(
        beam_base(bins={"energy_keV": [10.0, 12.0, 20.0]}),
        ions=precision,
        extract=["peak_ioniz"],
        seed=BASE_SEED,
    )
    scan_dir = tmp_path / "scan"
    events = []
    result = run_scan(
        scan,
        workers=2,
        runner=scaled_runner,
        scan_dir=scan_dir,
        cache=False,
        on_event=events.append,
    )
    folder = point_folder(scan_dir, scan)

    row = result.table.iloc[0]
    assert row["status"] == "done"
    assert row["n_ions"] == 1000
    assert "peak_ioniz" in result.table.columns and "peak_ioniz_err" in result.table.columns
    units = pd.read_csv(folder / "units.csv")
    per_beam = units.groupby("beam_batch")["n_ions"].sum()
    assert list(per_beam) == [500, 500]
    decisions = [e for e in events if isinstance(e, BatchFinished)]
    assert len(decisions) == 1  # one decision after the two beams, not one per unit
    assert decisions[0].ions == 1000


def test_binned_precision_stops_at_max_ions_by_beams(tmp_path, specs_dir):
    # 1/3 and 2/3 of the ions: the two 500-ion beams split 167/333 and 166/334, so their values
    # differ and the goal (1e-9 relative) is not reached before the maximum
    precision = Precision(target="peak_ioniz", rel=1e-9, batch_ions=500, max_ions=1000)
    scan = Scan(
        beam_base(bins={"energy_keV": [10.0, 13.333333333333334, 20.0]}),
        ions=precision,
        extract=["peak_ioniz"],
        seed=BASE_SEED,
    )
    with pytest.warns(UserWarning, match="precision target not reached"):
        result = run_scan(
            scan, workers=2, runner=scaled_runner, scan_dir=tmp_path / "scan", cache=False
        )
    row = result.table.iloc[0]
    assert row["status"] == "max_ions"
    assert row["n_ions"] == 1000
    assert row["peak_ioniz_err"] > 0


def test_mixed_scan_runs_exact_and_binned_points(tmp_path, specs_dir):
    scan = Scan(
        beam_base(mode="exact", bins={"energy_keV": [10.0, 12.0, 20.0]}),
        grid={"beam.mode": ["exact", "binned"]},
        ions=1000,
        seed=BASE_SEED,
    )
    result = run_scan(
        scan, workers=2, runner=recording_runner, scan_dir=tmp_path / "s", cache=False
    )
    assert list(result.table["status"]) == ["done", "done"]
    specs = recorded(specs_dir)
    distributed = [s for s in specs if isinstance(s["ion"]["energy_keV"], dict)]
    fixed = [s for s in specs if isinstance(s["ion"]["energy_keV"], float)]
    assert len(distributed) == 1  # the exact point is one run with its beam
    assert distributed[0]["number_ions"] == 1000
    assert sorted(s["number_ions"] for s in fixed) == [200, 800]


def test_a_rerun_of_a_binned_scan_runs_nothing(tmp_path, specs_dir):
    scan = Scan(beam_base(bins={"energy_keV": 5}), ions=1000, seed=BASE_SEED)
    cache = tmp_path / "cache"
    run_scan(scan, workers=2, runner=recording_runner, scan_dir=tmp_path / "a", cache=cache)
    runs = len(list(specs_dir.iterdir()))
    again = run_scan(scan, workers=2, runner=recording_runner, scan_dir=tmp_path / "b", cache=cache)
    assert len(list(specs_dir.iterdir())) == runs
    assert list(again.table["status"]) == ["cached"]


def test_exact_points_split_into_trim_dat_sized_batches(tmp_path, specs_dir):
    scan = Scan(beam_base(mode="exact"), ions=150_000, batch_ions=99_999, seed=BASE_SEED)
    scan_dir = tmp_path / "scan"
    run_scan(scan, workers=2, runner=recording_runner, scan_dir=scan_dir, cache=False)

    specs = recorded(specs_dir)
    assert sorted(s["number_ions"] for s in specs) == [50_001, 99_999]
    assert len({s["seed"] for s in specs}) == 2
    folder = point_folder(scan_dir, scan)
    units = pd.read_csv(folder / "units.csv")
    assert units["bin"].isna().all()
    assert list(units["beam_batch"]) == list(units["unit"])
    with open_results(folder) as merged:
        assert merged.n_ions == 150_000


def test_a_histogram_energy_in_exact_mode_runs_through_the_executor(tmp_path, specs_dir):
    histogram = {"histogram": {"edges": [10.0, 12.0, 20.0], "counts": [1.0, 3.0]}}
    scan = Scan(beam_base(mode="exact", energy=histogram), ions=20, seed=BASE_SEED)
    result = run_scan(
        scan, workers=1, runner=recording_runner, scan_dir=tmp_path / "s", cache=False
    )
    assert list(result.table["status"]) == ["done"]
    (spec,) = recorded(specs_dir)
    assert spec["ion"]["energy_keV"] == histogram


