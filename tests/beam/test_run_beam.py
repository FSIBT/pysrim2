"""run_beam: one TRIM run for an exact beam of up to 99 999 ions, a one-point scan otherwise;
the temporary folder; sampled_ions reads the sampled beam back from TRIM.DAT.

Runners are top-level functions (worker processes import this module by name). The fake
runners of ``test_beam_scan`` return ``test_files/1``; :func:`dat_runner` also writes the
TRIM.DAT and run.yaml that a real exact run would leave.
"""

import os
import shutil
import time
from pathlib import Path

import numpy as np
import pytest
import yaml
from test_beam_scan import FIXTURE, SPEC_DIR_VAR, beam_base, plain_runner

from srim.beam import beam_scan, run_beam, sampled_ions
from srim.beam.exact import sample_ions, write_trim_dat
from srim.input import TRIMInput
from srim.output import Results
from srim.scan import Precision, run_scan
from srim.scan.batches import MergedResults
from srim.scan.events import ScanStarted
from srim.spec import RunSpec
from srim.srim import TRIM


def _work_dir() -> Path:
    spec_dir = Path(os.environ[SPEC_DIR_VAR])
    return spec_dir.parent / "work" / f"{os.getpid()}-{time.monotonic_ns()}"


def dat_runner(spec):
    """Stands in for an exact SRIM run: the fixture plus the TRIM.DAT of the spec's ions."""
    directory = _work_dir()
    shutil.copytree(FIXTURE, directory)
    write_trim_dat(
        sample_ions(spec),
        directory / "TRIM.DAT",
        atomic_number=5,
        description="test beam",
        seed=spec.seed,
    )
    spec.to_yaml(directory / "run.yaml")
    return Results(str(directory))


def failing_runner(spec):
    """Stands in for a SRIM run that breaks."""
    raise RuntimeError("the SRIM run broke")


@pytest.fixture
def spec_dir(tmp_path, monkeypatch):
    directory = tmp_path / "specs"
    directory.mkdir()
    monkeypatch.setenv(SPEC_DIR_VAR, str(directory))
    return directory


def recorded(spec_dir) -> int:
    return len(list(spec_dir.glob("*.yaml")))


def exact_spec(number_ions):
    return beam_base(mode="exact").with_overrides([f"number_ions={number_ions}"])


def test_exact_small_beam_is_one_run(spec_dir):
    results = run_beam(exact_spec(500), runner=plain_runner)
    assert isinstance(results, Results)
    assert recorded(spec_dir) == 1


def test_exact_large_beam_splits_into_units(spec_dir, tmp_path):
    output = tmp_path / "out"
    with run_beam(exact_spec(150_000), runner=plain_runner, output_dir=output) as results:
        assert isinstance(results, MergedResults)
        assert results.n_ions == 150_000
        assert [b.n_ions for b in results.batches] == [99_999, 50_001]
    assert recorded(spec_dir) == 2


def test_binned_beam_is_merged_with_its_bins(spec_dir):
    spec = beam_base(mode="binned", bins={"energy_keV": 3})
    with run_beam(spec, runner=plain_runner) as results:
        assert isinstance(results, MergedResults)
        assert results.n_ions == 10
        assert results.beam_bins is not None and len(results.beam_bins) == 3


def test_temporary_folder_is_removed_on_close(spec_dir):
    folders = []

    def on_event(event):
        if isinstance(event, ScanStarted):
            folders.append(Path(event.scan_dir))

    results = run_beam(
        beam_base(mode="binned", bins={"energy_keV": 2}),
        runner=plain_runner,
        on_event=on_event,
    )
    (folder,) = folders
    assert folder.is_dir()
    results.close()
    assert not folder.exists()


def test_output_dir_is_kept_on_close(spec_dir, tmp_path):
    output = tmp_path / "out"
    results = run_beam(
        beam_base(mode="binned", bins={"energy_keV": 2}),
        runner=plain_runner,
        output_dir=output,
    )
    results.close()
    assert (output / "points").is_dir()


def test_failing_runner_raises_runtime_error(spec_dir):
    with pytest.raises(RuntimeError, match="the SRIM run broke"):
        run_beam(exact_spec(150_000), runner=failing_runner)


def test_failing_single_run_raises_runtime_error(spec_dir):
    with pytest.raises(RuntimeError, match="the SRIM run broke"):
        run_beam(exact_spec(500), runner=failing_runner)


def test_sampled_ions_of_one_exact_run_matches_the_sampled_beam(spec_dir):
    spec = exact_spec(500)
    with run_beam(spec, runner=dat_runner) as results:
        frame = sampled_ions(results)
    reference = sample_ions(spec)
    assert len(frame) == 500
    assert list(frame.columns) == list(reference.columns)
    assert (frame["ion"] == np.arange(1, 501)).all()
    np.testing.assert_allclose(frame["energy_keV"], reference["energy_keV"], rtol=1e-6)


def test_sampled_ions_of_exact_units_has_batch_and_global_ion(spec_dir, tmp_path):
    with run_beam(exact_spec(150_000), runner=dat_runner, output_dir=tmp_path / "o") as results:
        frame = sampled_ions(results)
    assert len(frame) == 150_000
    assert (frame["ion"] == np.arange(1, 150_001)).all()
    assert (frame["batch"].iloc[:99_999] == frame["batch"].iloc[0]).all()
    assert (frame["batch"].iloc[99_999:] != frame["batch"].iloc[0]).all()


def test_sampled_ions_of_a_binned_run_is_refused(spec_dir):
    with run_beam(beam_base(mode="binned", bins={"energy_keV": 2}), runner=plain_runner) as r:
        with pytest.raises(ValueError, match="beam_bins"):
            sampled_ions(r)


# -- beam_scan: the scan run_beam runs for a spec -------------------------------------------


def plain_exact_spec(number_ions, seed=9):
    """A run with no beam section at all (a fixed energy): not a distributed beam."""
    return RunSpec.from_dict(
        {
            "ion": {"symbol": "B", "energy_keV": 10.0, "angle_deg": 7.0},
            "number_ions": number_ions,
            "calculation": 1,
            "seed": seed,
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


def test_beam_scan_of_a_plain_spec_keeps_its_seed():
    spec = plain_exact_spec(500)
    scan = beam_scan(spec)
    assert scan.keep_seed is True
    assert scan.seed == spec.seed
    assert scan.ions == 500
    assert scan.batch_ions is None
    assert scan.extract == []
    assert len(scan.points()) == 1


def test_beam_scan_matches_run_beam_for_binned_and_large_exact():
    binned = beam_scan(beam_base(bins={"energy_keV": 5}))
    assert binned.keep_seed is False
    assert binned.seed == beam_base(bins={"energy_keV": 5}).seed

    large = beam_scan(exact_spec(150_000))
    assert large.keep_seed is False
    assert large.batch_ions == 99_999
    assert large.ions == 150_000


def test_beam_scan_with_a_precision_target_is_not_a_kept_seed_run():
    scan = beam_scan(exact_spec(500), precision=Precision("sputter_yield", rel=0.05))
    assert scan.keep_seed is False
    assert isinstance(scan.ions, Precision)


def test_trim_in_of_a_keep_seed_point_equals_the_plain_run(spec_dir, tmp_path):
    spec = plain_exact_spec(500)
    run_scan(
        beam_scan(spec),
        workers=1,
        runner=plain_runner,
        scan_dir=tmp_path / "scan",
        cache=False,
    )
    (recorded_file,) = sorted(spec_dir.glob("*.yaml"))
    unit_spec = RunSpec.from_dict(yaml.safe_load(recorded_file.read_text(encoding="utf-8")))

    def trim_in(s, name):
        directory = tmp_path / name
        directory.mkdir()
        TRIMInput(TRIM.from_spec(s)).write(str(directory))
        return (directory / "TRIM.IN").read_bytes()

    assert trim_in(unit_spec, "unit") == trim_in(spec, "plain")


# --- open_results(live=True): the live record of a point that is still running -----------


def _live_point(tmp_path, units, bins=None, spec=None):
    """A point folder with the outputs of ``units`` (test_files/1 copies) and its live record."""
    from srim.scan.batches import write_point_record

    spec = spec or RunSpec.from_dict(
        {
            "ion": {"symbol": "B", "energy_keV": 20.0},
            "number_ions": 20,
            "seed": 5,
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
    point = tmp_path / "point"
    for unit in units:
        shutil.copytree(FIXTURE, point / "batches" / str(unit))
    if bins is None:
        rows = [(unit, unit, None, 10, 100 + unit) for unit in units]
    else:  # a binned point: the unit is the bin's run of the (only) beam batch
        rows = [(unit, 0, unit, 10, 100 + unit) for unit in units]
    write_point_record(point, spec, rows, bins, live=True)
    return point


def test_open_results_live_merges_the_finished_units_of_the_live_record(tmp_path):
    from srim.beam import open_results

    point = _live_point(tmp_path, units=[0, 1])
    with open_results(point, live=True) as merged:
        assert merged.n_ions == 20
        assert [b.index for b in merged.batches] == [0, 1]


def test_a_live_record_is_not_read_as_a_finished_point(tmp_path):
    from srim.beam import open_results

    point = _live_point(tmp_path, units=[0])
    assert (point / "units.live.csv").is_file()
    with pytest.raises(FileNotFoundError):
        open_results(point)


def test_open_results_live_without_a_live_record_is_an_error(tmp_path):
    from srim.beam import open_results

    empty = tmp_path / "empty"
    empty.mkdir()
    with pytest.raises(FileNotFoundError):
        open_results(empty, live=True)


def test_the_live_bins_of_a_binned_point_are_read_with_their_units(tmp_path):
    from srim.beam import open_results
    from srim.beam.binned import bins_frame, make_bins

    spec = RunSpec.from_dict(
        {
            "ion": {
                "symbol": "B",
                "energy_keV": {"uniform": {"low": 10, "high": 20}},
                "angle_deg": 7.0,
            },
            "number_ions": 20,
            "seed": 5,
            "settings": {"ranges": 1},
            "beam": {"mode": "binned", "bins": {"energy_keV": 2}},
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
    bins = make_bins(spec)
    point = _live_point(tmp_path, units=[0], bins=bins_frame(bins, [10] * len(bins)), spec=spec)
    assert (point / "beam_bins.live.csv").is_file()
    with open_results(point, live=True) as merged:
        assert merged.beam_bins is not None
        assert merged.n_ions == 10


def test_open_results_live_reads_the_spec_after_the_units(tmp_path, monkeypatch):
    # the writer replaces the spec before the units; a reader that reads the units first
    # never gets a spec older than them, so a spec written while it reads is the one it gets
    import pandas as pd

    from srim.beam import open_results
    from srim.scan.batches import write_point_record

    point = _live_point(tmp_path, units=[0])
    newer = RunSpec.from_yaml(point / "run.live.yaml").with_overrides(["number_ions=30"])
    real_read_csv = pd.read_csv

    def writer_moves_on(path, *args, **kwargs):
        frame = real_read_csv(path, *args, **kwargs)
        if Path(path).name == "units.live.csv":
            write_point_record(point, newer, [(0, 0, None, 10, 100)], live=True)
        return frame

    monkeypatch.setattr(pd, "read_csv", writer_moves_on)
    with open_results(point, live=True) as merged:
        assert merged.spec["number_ions"] == 30
