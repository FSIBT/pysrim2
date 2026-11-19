"""Headless runs: a run of the saved setup goes in its own folder, keeps the setup's seed, and
another backend (a new server) finds, follows, stops, reopens and deletes it. Fake runners only
(no SRIM): the runner comes from ``PYSRIM2_SCAN_RUNNER`` in the environment of the test.

Every test that starts a runner ends with its runners killed (see ``no_runner_left``), so no
test leaves a process behind. Waits have a 60 s timeout.
"""

import json
import os
import signal
import time
from pathlib import Path

import pytest

from srim.mcp.backend import BackendError
from srim.mcp.detached import PID_FILE
from srim.mcp.headless import HeadlessBackend
from srim.scan import Scan
from srim.scan.executor import mark_scan_folder
from srim.spec import RunSpec

TIMEOUT = 60.0


def wait_until(predicate, timeout=TIMEOUT):
    """Poll ``predicate`` until it is true; fail when ``timeout`` seconds pass first."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return
        time.sleep(0.1)
    raise AssertionError("condition not met in time")


def kill_runner(folder) -> None:
    """Kill the runner of ``folder`` and the workers it started, if they run."""
    pid = json.loads((folder / PID_FILE).read_text(encoding="utf-8"))["pid"]
    try:
        if os.name == "nt":
            os.kill(pid, signal.SIGTERM)
        else:
            os.killpg(pid, signal.SIGKILL)
    except (ProcessLookupError, PermissionError, OSError):
        pass


@pytest.fixture(autouse=True)
def no_runner_left(tmp_path):
    """Kill the runners the test started, however it ends."""
    yield
    for meta in tmp_path.glob(f"**/{PID_FILE}"):
        kill_runner(meta.parent)


@pytest.fixture
def fast(monkeypatch):
    """Runs that finish at once (the fixture outputs of test_files/1)."""
    monkeypatch.setenv("PYSRIM2_SCAN_RUNNER", "tests.app.runners:fixture_runner")


@pytest.fixture
def slow(monkeypatch):
    """Runs that go on until they are stopped."""
    monkeypatch.setenv("PYSRIM2_SCAN_RUNNER", "tests.app.runners:slow_runner")


def boron_setup(backend, seed=7, ions=10):
    """A plain boron run on 100 nm silicon, set up through the backend."""
    backend.set_section("ion", {"symbol": "B", "energy_keV": 20.0})
    backend.set_section(
        "target",
        [
            {
                "name": "Si",
                "thickness_nm": 100.0,
                "density_g_cm3": 2.33,
                "elements": [{"symbol": "Si", "stoich": 1.0}],
            }
        ],
    )
    backend.set_values([f"number_ions={ions}", f"seed={seed}"])
    return backend


def finished(backend, folder=None):
    return lambda: backend.run_status(folder)["status"] == "finished"


def make_run_folder(folder: Path, setup: Path) -> Path:
    """A run folder of the setup that never started (no runner.json)."""
    folder.mkdir(parents=True)
    mark_scan_folder(folder)
    Scan(RunSpec.from_yaml(str(setup)), seed=1).to_yaml(folder / "scan.yaml")
    return folder


def test_start_needs_a_saved_setup(tmp_path, fast):
    b = boron_setup(HeadlessBackend(tmp_path))
    with pytest.raises(BackendError, match="save_setup"):
        b.start_run()


def test_a_started_run_can_be_followed_and_reopened(tmp_path, fast):
    b = boron_setup(HeadlessBackend(tmp_path))
    b.save_setup("boron.yaml")
    started = b.start_run()
    assert Path(started["folder"]).parent.name == "boron-runs"
    assert set(started["estimate"]) == {"seconds", "workers", "cores", "units", "ions"}
    wait_until(finished(b))
    assert HeadlessBackend(tmp_path).list_runs("boron.yaml")[0]["status"] == "finished"
    opened = HeadlessBackend(tmp_path).open_run(started["folder"])
    assert opened["finished"] is True
    assert [p["status"] for p in opened["points"]] == ["done"]


def test_a_new_server_finds_and_stops_a_running_run(tmp_path, slow):
    b = boron_setup(HeadlessBackend(tmp_path))
    b.save_setup("boron.yaml")
    folder = b.start_run()["folder"]
    wait_until(lambda: b.run_status()["status"] == "running")
    other = HeadlessBackend(tmp_path)
    other.open_setup("boron.yaml")
    assert other.run_status()["folder"] == folder
    other.control_run("stop")
    wait_until(lambda: other.run_status()["status"] == "stopped")


def test_the_run_keeps_the_seed_of_the_saved_setup(tmp_path, fast):
    b = boron_setup(HeadlessBackend(tmp_path), seed=7)
    b.save_setup("boron.yaml")
    folder = Path(b.start_run()["folder"])
    wait_until(finished(b))
    scan = Scan.from_yaml(folder / "scan.yaml")
    (point,) = scan.points()
    # a single run is a one-point scan that keeps its seed: its spec is the saved run.yaml's
    assert point.spec.seed == 7
    assert point.spec.to_dict() == RunSpec.from_yaml(str(tmp_path / "boron.yaml")).to_dict()


def test_start_saves_an_unsaved_change_to_its_own_path(tmp_path, fast):
    b = boron_setup(HeadlessBackend(tmp_path))
    b.save_setup("boron.yaml")
    b.set_values(["number_ions=12"])
    assert b.has_unsaved_changes()
    started = b.start_run()
    assert not b.has_unsaved_changes()
    assert RunSpec.from_yaml(str(tmp_path / "boron.yaml")).number_ions == 12
    assert started["estimate"]["ions"] == 12
    wait_until(finished(b))


def test_a_blocking_problem_refuses_the_start_and_writes_nothing(tmp_path, fast):
    b = boron_setup(HeadlessBackend(tmp_path))
    b.save_setup("boron.yaml")
    b.set_section("target", [])  # unsaved: the file still has its layer
    with pytest.raises(BackendError, match="not complete"):
        b.start_run()
    assert not (tmp_path / "boron-runs").exists()
    assert RunSpec.from_yaml(str(tmp_path / "boron.yaml")).target


def test_a_bad_worker_count_is_refused(tmp_path, fast):
    b = boron_setup(HeadlessBackend(tmp_path))
    b.save_setup("boron.yaml")
    with pytest.raises(BackendError, match="workers"):
        b.start_run(workers=0)
    assert not (tmp_path / "boron-runs").exists()


def test_estimate_picks_the_workers_from_the_cores(tmp_path):
    b = boron_setup(HeadlessBackend(tmp_path, cores=8))
    est = b.estimate()
    assert est["cores"] == 8
    assert est["workers"] == 1  # one unit: no more workers than units
    assert est["units"] == 1
    assert est["ions"] == 10
    assert est["seconds"] > 0
    assert b.estimate(workers=3)["workers"] == 3


def test_list_runs_is_newest_first(tmp_path):
    b = boron_setup(HeadlessBackend(tmp_path))
    b.save_setup("boron.yaml")
    runs = tmp_path / "boron-runs"
    names = ["20260101T000000Z", "20261115T000000Z", "20261115T000000Z-2", "20261115T000000Z-10"]
    for name in names:
        make_run_folder(runs / name, tmp_path / "boron.yaml")
    (runs / "not-a-run").mkdir()  # no scan.yaml: not a run
    listed = HeadlessBackend(tmp_path).list_runs("boron.yaml")
    assert [Path(r["folder"]).name for r in listed] == list(reversed(names))
    assert {r["status"] for r in listed} == {"not started"}


def test_run_status_without_a_run_says_so(tmp_path):
    b = boron_setup(HeadlessBackend(tmp_path))
    b.save_setup("boron.yaml")
    with pytest.raises(BackendError, match="no run"):
        b.run_status()


def test_a_finished_run_cannot_be_stopped(tmp_path, fast):
    b = boron_setup(HeadlessBackend(tmp_path))
    b.save_setup("boron.yaml")
    folder = b.start_run()["folder"]
    wait_until(finished(b))
    with pytest.raises(BackendError, match="finished"):
        b.control_run("stop", folder)


def test_delete_removes_a_finished_run(tmp_path, fast):
    b = boron_setup(HeadlessBackend(tmp_path))
    b.save_setup("boron.yaml")
    folder = b.start_run()["folder"]
    wait_until(finished(b))
    assert b.delete_run(folder)["deleted"] is True
    assert not Path(folder).exists()


def test_delete_refuses_a_running_run(tmp_path, slow):
    b = boron_setup(HeadlessBackend(tmp_path))
    b.save_setup("boron.yaml")
    folder = b.start_run()["folder"]
    wait_until(lambda: b.run_status()["status"] == "running")
    try:
        with pytest.raises(BackendError, match="running"):
            b.delete_run(folder)
        assert Path(folder).is_dir()
    finally:
        b.control_run("stop")
        wait_until(lambda: b.run_status()["status"] == "stopped")


def test_delete_refuses_anything_outside_the_setups_run_folders(tmp_path):
    b = boron_setup(HeadlessBackend(tmp_path))
    b.save_setup("boron.yaml")
    outside = make_run_folder(tmp_path / "elsewhere", tmp_path / "boron.yaml")
    other_setup = make_run_folder(
        tmp_path / "other-runs" / "20260101T000000Z", tmp_path / "boron.yaml"
    )
    runs = tmp_path / "boron-runs"
    for folder in (
        outside,
        other_setup,
        runs,  # the folder that holds the runs, not one of them
        runs / ".." / "elsewhere",  # a way out of the folder
        tmp_path,
    ):
        with pytest.raises(BackendError, match="not a run folder"):
            b.delete_run(str(folder))
    assert outside.is_dir() and other_setup.is_dir()


def test_a_run_of_another_setup_is_not_reported_after_opening_one(tmp_path, fast):
    b = boron_setup(HeadlessBackend(tmp_path))
    b.save_setup("boron.yaml")
    b.start_run()
    wait_until(finished(b))
    other = boron_setup(HeadlessBackend(tmp_path), seed=3)
    other.save_setup("other.yaml")
    b.open_setup("other.yaml")
    with pytest.raises(BackendError, match="no run"):
        b.run_status()
    with pytest.raises(BackendError, match="no run"):
        b.control_run("stop")
    b.save_setup("moved.yaml")  # the run of boron.yaml is not this setup's run either
    with pytest.raises(BackendError, match="no run"):
        b.run_status()


def test_starting_an_opened_unedited_setup_does_not_rewrite_the_file(tmp_path, fast):
    b = boron_setup(HeadlessBackend(tmp_path))
    b.save_setup("boron.yaml")
    path = tmp_path / "boron.yaml"
    os.utime(path, (1_000_000, 1_000_000))
    before, mtime = path.read_bytes(), path.stat().st_mtime_ns
    opened = HeadlessBackend(tmp_path)
    opened.open_setup("boron.yaml")
    assert not opened.has_unsaved_changes()
    opened.start_run()
    assert path.read_bytes() == before
    assert path.stat().st_mtime_ns == mtime
    wait_until(finished(opened))


def test_a_folder_that_exists_already_is_refused(tmp_path, fast, monkeypatch):
    b = boron_setup(HeadlessBackend(tmp_path))
    b.save_setup("boron.yaml")
    taken = tmp_path / "taken"
    taken.mkdir()
    (taken / "keep.txt").write_text("x", encoding="utf-8")
    monkeypatch.setattr("srim.mcp.headless.run_folder", lambda setup, now: taken)
    with pytest.raises(BackendError, match="exists"):
        b.start_run()
    assert (taken / "keep.txt").read_text(encoding="utf-8") == "x"


def test_a_run_that_cannot_start_leaves_no_folder(tmp_path, fast, monkeypatch):
    b = boron_setup(HeadlessBackend(tmp_path))
    b.save_setup("boron.yaml")

    def refuse(folder, workers=None, **options):
        raise RuntimeError("no interpreter")

    monkeypatch.setattr("srim.mcp.headless.start_detached", refuse)
    with pytest.raises(BackendError, match="could not start"):
        b.start_run()
    assert list((tmp_path / "boron-runs").iterdir()) == []
    with pytest.raises(BackendError, match="no run"):
        b.run_status()


def test_delete_refuses_a_symlinked_run_folder(tmp_path):
    b = boron_setup(HeadlessBackend(tmp_path))
    b.save_setup("boron.yaml")
    real = make_run_folder(tmp_path / "elsewhere", tmp_path / "boron.yaml")
    runs = tmp_path / "boron-runs"
    runs.mkdir()
    link = runs / "20260101T000000Z"
    try:
        os.symlink(real, link, target_is_directory=True)
    except (OSError, NotImplementedError):
        pytest.skip("symbolic links cannot be made here")
    with pytest.raises(BackendError, match="is a symbolic link"):
        b.delete_run(str(link))
    assert real.is_dir() and link.is_symlink()


def test_a_run_folder_that_cannot_be_removed_is_a_backend_error(tmp_path, monkeypatch):
    b = boron_setup(HeadlessBackend(tmp_path))
    b.save_setup("boron.yaml")
    folder = make_run_folder(tmp_path / "boron-runs" / "20260101T000000Z", tmp_path / "boron.yaml")

    def busy(path, *args, **kwargs):
        raise OSError("busy")

    monkeypatch.setattr("srim.mcp.headless.shutil.rmtree", busy)
    with pytest.raises(BackendError, match="cannot delete"):
        b.delete_run(str(folder))
    assert folder.is_dir()


def test_delete_refuses_a_paused_run(tmp_path, slow):
    b = boron_setup(HeadlessBackend(tmp_path))
    b.save_setup("boron.yaml")
    folder = b.start_run()["folder"]
    wait_until(lambda: b.run_status()["status"] == "running")
    b.control_run("pause")
    wait_until(lambda: b.run_status()["status"] == "paused")
    try:
        with pytest.raises(BackendError, match="paused"):
            b.delete_run(folder)
        assert Path(folder).is_dir()
    finally:
        b.control_run("stop")
        wait_until(lambda: b.run_status()["status"] == "stopped")


def test_a_live_setup_runs_in_ten_batches_in_its_run_folder(tmp_path, fast):
    import pandas as pd

    b = boron_setup(HeadlessBackend(tmp_path), seed=7, ions=10)
    b.set_section("run", {"live": True})
    b.save_setup("boron.yaml")
    folder = Path(b.start_run()["folder"])
    wait_until(finished(b))
    scan = Scan.from_yaml(folder / "scan.yaml")
    (point,) = scan.points()
    assert scan.keep_seed is False and scan.batch_ions == 1  # 10 ions in ten batches of one
    units = pd.read_csv(folder / "points" / point.id[:16] / "units.csv")
    assert len(units) == 10 and int(units["n_ions"].sum()) == 10
    assert not (folder / "points" / point.id[:16] / "units.live.csv").exists()


def test_a_run_the_app_started_and_still_runs_is_not_deleted_from_here(tmp_path):
    # a run folder whose runner is the app's, and whose app process is alive, belongs to the app
    from srim.mcp.detached import PID_FILE, start_runner_file
    from srim.mcp.headless import delete_run_folder

    from test_results import spec

    from srim.scan import Scan
    from srim.scan.events import ScanFinished

    runs = tmp_path / "n15-runs"
    folder = runs / "20260101-000000Z"
    folder.mkdir(parents=True)
    Scan(spec(), seed=7).to_yaml(folder / "scan.yaml")
    start_runner_file(folder, workers=1, backend="app")  # this process: alive
    # the run has ended, and the app is still open on it: still the app's
    (folder / "events.jsonl").write_text(
        ScanFinished(n_done=1, n_failed=0, seconds=1.0, stopped=False).to_json() + "\n",
        encoding="utf-8",
    )
    with pytest.raises(BackendError, match="pysrim2 app"):
        delete_run_folder(folder, runs)
    assert folder.exists()
    # the app is gone: its folder can be deleted
    (folder / PID_FILE).write_text(
        json.dumps({"pid": 2**22 + 1, "started": 0, "workers": 1, "backend": "app"}),
        encoding="utf-8",
    )
    delete_run_folder(folder, runs)
    assert not folder.exists()
