"""Detached runs: the runner outlives its starter, its events are read back, and it can be
stopped and is reported as crashed when it is killed. Fake runners only (no SRIM).

Every test that starts a runner stops or kills it in a ``finally``, so no test leaves a process
behind. Waits have a 60 s timeout.
"""

import json
import os
import signal
import subprocess
import sys
import time
from pathlib import Path

import pytest

from srim.mcp.backend import BackendError
from srim.mcp.detached import (
    EVENTS_FILE,
    LOG_FILE,
    PID_FILE,
    run_state,
    send_control,
    start_detached,
)
from srim.scan import Scan
from srim.scan.executor import mark_scan_folder
from srim.spec import RunSpec

TIMEOUT = 60.0

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


def scan_folder_with_points(tmp_path, n=1) -> Path:
    """A folder with a scan.yaml of ``n`` points (energies 10, 20, ... keV)."""
    folder = tmp_path / "scan"
    folder.mkdir()
    mark_scan_folder(folder)  # run_scan refuses a folder with files and no marker
    grid = {"ion.energy_keV": [10.0 * (i + 1) for i in range(n)]} if n > 1 else None
    Scan(BASE, grid=grid, seed=7).to_yaml(folder / "scan.yaml")
    return folder


def scan_folder_with_one_point(tmp_path) -> Path:
    return scan_folder_with_points(tmp_path, n=1)


def fake_runner_env(name="fixture_runner") -> dict:
    """The environment that makes the runner use a fake runner of tests/app/runners.py."""
    return {"PYSRIM2_SCAN_RUNNER": f"tests.app.runners:{name}"}


def slow_runner_env() -> dict:
    return fake_runner_env("one_second_runner")


def wait_until(predicate, timeout=TIMEOUT):
    """Poll ``predicate`` until it is true; fail when ``timeout`` seconds pass first."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return
        time.sleep(0.1)
    raise AssertionError("condition not met in time")


def runner_pid(folder) -> int:
    return json.loads((folder / PID_FILE).read_text(encoding="utf-8"))["pid"]


def kill_runner(folder) -> None:
    """Kill the runner and the workers it started (POSIX: their process group), if they run."""
    pid = runner_pid(folder)
    try:
        if os.name == "nt":
            os.kill(pid, signal.SIGTERM)  # on Windows this terminates the process
        else:
            os.killpg(pid, signal.SIGKILL)
    except (ProcessLookupError, PermissionError, OSError):
        pass


@pytest.fixture
def started(tmp_path):
    """Folders whose runners are killed at the end of the test, however it ends."""
    folders = []

    def make(folder):
        folders.append(folder)
        return folder

    yield make
    for folder in folders:
        if (folder / PID_FILE).is_file():
            kill_runner(folder)


def test_a_detached_run_finishes_and_writes_its_events(tmp_path, started):
    folder = started(scan_folder_with_one_point(tmp_path))
    start_detached(folder, workers=1, env=fake_runner_env())
    wait_until(lambda: run_state(folder)["status"] == "finished")
    assert (folder / "result" / "scan.csv").is_file()
    state = run_state(folder)
    assert state["points_done"] == 1
    assert state["points_total"] == 1
    assert state["errors"] == []
    lines = (folder / EVENTS_FILE).read_text(encoding="utf-8").splitlines()
    kinds = [json.loads(line)["kind"] for line in lines]
    assert kinds[0] == "scan_started"
    assert kinds[-1] == "scan_finished"


def test_the_run_survives_its_starter(tmp_path, started):
    # the starter is a child Python that starts the run and exits at once
    folder = started(scan_folder_with_one_point(tmp_path))
    code = (
        "import sys\n"
        "from pathlib import Path\n"
        "from srim.mcp.detached import start_detached\n"
        "start_detached(Path(sys.argv[1]), workers=1)\n"
    )
    env = {**os.environ, **fake_runner_env()}
    subprocess.run([sys.executable, "-c", code, str(folder)], check=True, env=env, timeout=TIMEOUT)
    wait_until(lambda: run_state(folder)["status"] == "finished")


def test_stop_through_the_control_file(tmp_path, started):
    folder = started(scan_folder_with_points(tmp_path, n=20))
    start_detached(folder, workers=1, env=slow_runner_env())
    wait_until(lambda: run_state(folder)["points_done"] >= 1)
    send_control(folder, "stop")
    wait_until(lambda: run_state(folder)["status"] == "stopped")
    state = run_state(folder)
    assert state["points_done"] < state["points_total"]


def test_pause_and_resume_are_reported(tmp_path, started):
    folder = started(scan_folder_with_points(tmp_path, n=20))
    start_detached(folder, workers=1, env=slow_runner_env())
    wait_until(lambda: run_state(folder)["status"] == "running")
    send_control(folder, "pause")
    wait_until(lambda: run_state(folder)["status"] == "paused")
    send_control(folder, "resume")
    wait_until(lambda: run_state(folder)["status"] == "running")
    send_control(folder, "stop")
    wait_until(lambda: run_state(folder)["status"] == "stopped")


def test_a_killed_runner_is_reported_as_crashed(tmp_path, started):
    folder = started(scan_folder_with_points(tmp_path, n=20))
    start_detached(folder, workers=1, env=slow_runner_env())
    wait_until(lambda: run_state(folder)["status"] == "running")
    os.kill(runner_pid(folder), signal.SIGKILL if os.name != "nt" else signal.SIGTERM)
    wait_until(lambda: run_state(folder)["status"] == "crashed")
    kill_runner(folder)  # the workers of the killed runner, if any still run


def test_a_run_that_cannot_start_is_failed(tmp_path, started):
    folder = started(scan_folder_with_one_point(tmp_path))
    start_detached(folder, workers=1, env=fake_runner_env("no_such_runner"))
    wait_until(lambda: run_state(folder)["status"] == "failed")
    assert run_state(folder)["errors"]


def test_control_on_a_finished_run_is_refused(tmp_path, started):
    folder = started(scan_folder_with_one_point(tmp_path))
    start_detached(folder, workers=1, env=fake_runner_env())
    wait_until(lambda: run_state(folder)["status"] == "finished")
    with pytest.raises(BackendError, match="finished"):
        send_control(folder, "stop")


def test_unknown_control_is_refused(tmp_path):
    folder = scan_folder_with_one_point(tmp_path)
    with pytest.raises(BackendError):
        send_control(folder, "explode")


def test_a_folder_without_a_run_has_no_state(tmp_path):
    folder = scan_folder_with_one_point(tmp_path)
    with pytest.raises(BackendError, match="no run"):
        run_state(folder)


def test_a_folder_without_a_scan_cannot_start(tmp_path):
    with pytest.raises(BackendError, match="scan.yaml"):
        start_detached(tmp_path, workers=1, env=fake_runner_env())


def test_a_second_run_in_a_live_folder_is_refused(tmp_path, started):
    folder = started(scan_folder_with_points(tmp_path, n=20))
    start_detached(folder, workers=1, env=slow_runner_env())
    wait_until(lambda: run_state(folder)["status"] == "running")
    try:
        with pytest.raises(BackendError, match="already going"):
            start_detached(folder, workers=1, env=slow_runner_env())
    finally:
        send_control(folder, "stop")
        wait_until(lambda: run_state(folder)["status"] == "stopped")


def test_the_runner_log_is_kept(tmp_path, started):
    folder = started(scan_folder_with_one_point(tmp_path))
    start_detached(folder, workers=1, env=fake_runner_env())
    wait_until(lambda: run_state(folder)["status"] == "finished")
    assert (folder / LOG_FILE).is_file()
    assert Path(folder / PID_FILE).is_file()
