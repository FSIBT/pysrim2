"""prepare / run_job / collect for Slurm, with fake runners (no SRIM, no cluster).

The job folders are run in this process with the outputs of test_files/1 as the runner, so
the test checks the layout, the job folders, the collected table and the cache, and that the
collected result equals a local run of the same scan. Every wait has a timeout and a SIGALRM
guard fails a test that hangs.
"""

import os
import signal
import stat
import threading
from pathlib import Path

import pytest

from srim.output import Results
from srim.scan import Precision, Scan, ScanResult, collect, prepare, run_scan
from srim.scan.backends import read_job, run_job
from srim.spec import RunSpec, SpecError

FIXTURE = Path(__file__).resolve().parents[2] / "test_files" / "1"
GUARD_SECONDS = 120

BASE = RunSpec.from_dict(
    {
        "ion": {"symbol": "B", "energy_keV": 20.0},
        "number_ions": 10,
        "seed": 1,
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


def energy_of(spec):
    return spec.to_dict()["ion"]["energy_keV"]


def fixture_runner(spec):
    return Results(str(FIXTURE))


def failing_at_20(spec):
    if energy_of(spec) == 20.0:
        raise ValueError("SRIM said no\nsecond line")
    return fixture_runner(spec)


def never_runs(spec):
    raise AssertionError("a cached run must not be computed again")


@pytest.fixture(autouse=True)
def hang_guard():
    if not hasattr(signal, "SIGALRM") or threading.current_thread() is not threading.main_thread():
        yield
        return

    def alarm(signum, frame):
        raise TimeoutError(f"test ran longer than {GUARD_SECONDS} s")

    previous = signal.signal(signal.SIGALRM, alarm)
    signal.alarm(GUARD_SECONDS)
    try:
        yield
    finally:
        signal.alarm(0)
        signal.signal(signal.SIGALRM, previous)


def scan_of(energies=(10.0, 20.0, 30.0, 40.0), **kwargs):
    kwargs.setdefault("seed", 7)
    return Scan(BASE, grid={"ion.energy_keV": list(energies)}, **kwargs)


def run_all_jobs(folder, runner=fixture_runner):
    jobs = sorted((folder / "jobs").iterdir())
    return [run_job(job, runner=runner) for job in jobs]


# -- prepare -----------------------------------------------------------------------------------


def test_prepare_writes_the_scan_one_job_per_point_and_the_array_script(tmp_path):
    scan = scan_of()
    folder = prepare(scan, tmp_path / "s")
    recorded = Scan.from_yaml(folder / "scan.yaml")
    assert recorded.seed == 7
    assert [p.id for p in recorded.points()] == [p.id for p in scan.points()]
    jobs = sorted((folder / "jobs").iterdir())
    assert [j.name for j in jobs] == ["0000", "0001", "0002", "0003"]
    for index, job in enumerate(jobs):
        ((name, spec),) = read_job(job)
        assert name == scan.points()[index].id[:16]
        assert spec.number_ions == 10
    script = (folder / "submit.sbatch").read_text(encoding="utf-8")
    assert "#SBATCH --array=0-3" in script
    assert "#SBATCH --job-name=pysrim2-scan" in script
    assert "##SBATCH --time=" in script and "##SBATCH --mem=" in script
    assert 'pysrim2 run-job "$JOB_DIR"' in script
    assert 'printf %04d "$SLURM_ARRAY_TASK_ID"' in script
    assert f"cd {folder}" in script
    if os.name == "posix":
        assert os.stat(folder / "submit.sbatch").st_mode & stat.S_IXUSR


def test_jobs_deal_the_points_round_robin(tmp_path):
    scan = scan_of()
    folder = prepare(scan, tmp_path / "s", jobs=2)
    names = [[name for name, _ in read_job(j)] for j in sorted((folder / "jobs").iterdir())]
    ids = [p.id[:16] for p in scan.points()]
    assert names == [[ids[0], ids[2]], [ids[1], ids[3]]]
    assert "#SBATCH --array=0-1" in (folder / "submit.sbatch").read_text(encoding="utf-8")


def test_without_a_base_seed_prepare_records_one(tmp_path):
    scan = Scan(BASE, grid={"ion.energy_keV": [10.0, 20.0]})
    folder = prepare(scan, tmp_path / "s")
    seed = Scan.from_yaml(folder / "scan.yaml").seed
    assert isinstance(seed, int) and seed >= 1


def test_precision_targets_are_refused(tmp_path):
    scan = scan_of(
        ions=Precision(target="mean_depth_A", rel=0.05, min_ions=10, max_ions=20, batch_ions=10)
    )
    with pytest.raises(SpecError, match="use ions= for Slurm"):
        prepare(scan, tmp_path / "s")


@pytest.mark.parametrize("jobs", [0, -1, 2.5, True, 5])
def test_a_bad_job_count_is_refused(tmp_path, jobs):
    with pytest.raises(SpecError, match="jobs"):
        prepare(scan_of(), tmp_path / "s", jobs=jobs)


def test_a_prepared_folder_is_not_prepared_again(tmp_path):
    prepare(scan_of(), tmp_path / "s")
    with pytest.raises(FileExistsError, match="already holds a prepared scan"):
        prepare(scan_of(), tmp_path / "s")


# -- running the job folders and collecting -----------------------------------------------------


def test_run_job_skips_the_units_that_are_done(tmp_path):
    folder = prepare(scan_of(), tmp_path / "s", jobs=2)
    assert run_all_jobs(folder) == [0, 0]
    calls = []

    def counting(spec):
        calls.append(energy_of(spec))
        return fixture_runner(spec)

    assert run_all_jobs(folder, counting) == [0, 0]
    assert calls == []


def test_collect_after_all_jobs_equals_a_local_run(tmp_path):
    scan = scan_of()
    folder = prepare(scan, tmp_path / "s", jobs=2)
    run_all_jobs(folder)
    collected = collect(folder, cache=False)
    local = run_scan(scan, workers=2, runner=fixture_runner, scan_dir=tmp_path / "l", cache=False)
    assert isinstance(collected, ScanResult)
    assert collected.table["status"].tolist() == ["done"] * 4
    assert collected.table["seeds"].tolist() == local.table["seeds"].tolist()
    assert collected.table["n_ions"].tolist() == local.table["n_ions"].tolist()
    assert collected.table["mean_depth_A"].tolist() == local.table["mean_depth_A"].tolist()
    assert collected.table["path"].tolist() == [f"points/{p.id[:16]}" for p in scan.points()]
    point = folder / "points" / scan.points()[0].id[:16]
    assert (point / "RANGE_3D.txt").is_file()
    assert not (point / ".done").exists()
    assert (folder / "result" / "scan.csv").is_file()


def test_collect_reports_failed_and_not_run_points(tmp_path):
    scan = scan_of(energies=(10.0, 20.0, 30.0))
    folder = prepare(scan, tmp_path / "s")
    jobs = sorted((folder / "jobs").iterdir())
    run_job(jobs[0], runner=failing_at_20)  # 10 keV: done
    run_job(jobs[1], runner=failing_at_20)  # 20 keV: failed
    # 30 keV: its job is never run
    result = collect(folder, cache=False)
    assert result.table["status"].tolist() == ["done", "failed", "stopped"]
    assert result.table["error"].iloc[1] == "ValueError: SRIM said no second line"
    assert (folder / "points" / scan.points()[1].id[:16] / "error.log").is_file()
    assert result.table["error"].iloc[2] == "not run yet"
    assert result.table["n_ions"].isna().tolist() == [False, True, True]


def test_collected_finished_runs_are_cached_for_a_local_run(tmp_path):
    scan = scan_of()
    folder = prepare(scan, tmp_path / "s", jobs=2)
    run_all_jobs(folder)
    collected = collect(folder)  # default cache: <scan folder>/cache
    assert (folder / "cache" / "CACHEDIR.TAG").is_file()
    local = run_scan(
        scan,
        workers=2,
        runner=never_runs,  # every run must come from the cache
        scan_dir=tmp_path / "l",
        cache=folder / "cache",
    )
    assert local.table["status"].tolist() == ["cached"] * 4
    assert local.table["seeds"].tolist() == collected.table["seeds"].tolist()
    assert local.table["mean_depth_A"].tolist() == collected.table["mean_depth_A"].tolist()


def test_collect_reads_a_python_extractor_that_cannot_be_imported(tmp_path):
    folder = prepare(scan_of(energies=(10.0,)), tmp_path / "s")
    text = (folder / "scan.yaml").read_text(encoding="utf-8")
    text = text.replace("implant_summary", "python:no_such_module_for_pysrim2:f")
    (folder / "scan.yaml").write_text(text, encoding="utf-8")
    with pytest.raises(SpecError, match="cannot import"):
        collect(folder, cache=False)


def test_collect_needs_a_prepared_scan(tmp_path):
    with pytest.raises(FileNotFoundError, match="prepare the scan first"):
        collect(tmp_path / "nothing", cache=False)
