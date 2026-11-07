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
from test_beam_scan import FIXTURE, SPEC_DIR_VAR, beam_base, plain_runner

from srim.beam import run_beam, sampled_ions
from srim.beam.exact import sample_ions, write_trim_dat
from srim.output import Results
from srim.scan.batches import MergedResults
from srim.scan.events import ScanStarted


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
