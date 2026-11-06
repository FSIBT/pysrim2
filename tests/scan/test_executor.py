"""run_scan: workers, events, retries, seeds and stopping, with fake runners (no SRIM).

The fake runners return the outputs of ``test_files/1`` (a real SRIM result) so the
extractors run on real files. Worker processes are started with ``spawn``, so the runners
are top-level functions. Every wait has a timeout, and a SIGALRM guard (POSIX) fails a test
that hangs instead of blocking the run.
"""

import json
import logging
import os
import signal
import subprocess
import sys
import threading
import time
from pathlib import Path

import pytest

from srim.output import Results
from srim.scan import Scan, ScanResult, derive_seed, run_scan
from srim.scan.events import (
    PointFailed,
    PointFinished,
    PointQueued,
    PointRetried,
    PointStarted,
    PointStopped,
    ScanFinished,
    ScanPaused,
    ScanStarted,
)
from srim.scan.extract import register, resolve
from srim.spec import RunSpec

FIXTURE = Path(__file__).resolve().parents[2] / "test_files" / "1"
#: the environment variable the fake runners use to share state with the parent
STATE_VAR = "PYSRIM2_TEST_STATE"
GUARD_SECONDS = 120


def echo_energy(results, point):
    """Extractor: the scanned energy, so each row says which point it is."""
    return {"energy_echo_keV": point.params["ion.energy_keV"]}


register("echo_energy", echo_energy, replace=True)

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
    """Stands in for SRIM: the outputs of test_files/1 (copied by the worker)."""
    return Results(str(FIXTURE))


def failing_runner(spec):
    if energy_of(spec) == 20.0:
        raise ValueError("SRIM said no\nsecond line")
    return fixture_runner(spec)


def flaky_runner(spec):
    """Fails the first time it sees 20 keV (the marker file is shared with the parent)."""
    marker = Path(os.environ[STATE_VAR]) / "tried-20"
    if energy_of(spec) == 20.0 and not marker.exists():
        marker.touch()
        raise RuntimeError("first try fails")
    return fixture_runner(spec)


def sleepy_runner(spec):
    """10 keV finishes; any other energy starts a child process and waits for it."""
    if energy_of(spec) == 10.0:
        return fixture_runner(spec)
    child = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(120)"])
    Path(os.environ[STATE_VAR], f"child-{child.pid}").touch()
    child.wait()
    return fixture_runner(spec)


@pytest.fixture(autouse=True)
def hang_guard():
    """Fail (instead of hanging) if a test runs longer than GUARD_SECONDS (POSIX)."""
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


@pytest.fixture
def state_dir(tmp_path, monkeypatch):
    directory = tmp_path / "state"
    directory.mkdir()
    monkeypatch.setenv(STATE_VAR, str(directory))
    return directory


class Recorder:
    """An ``on_event`` that keeps the events and checks that each one is JSON-safe."""

    def __init__(self, after=None):
        self.events = []
        self.after = after

    def __call__(self, event):
        json.loads(event.to_json())
        self.events.append(event)
        if self.after is not None:
            self.after(event)

    def kinds(self, index=None):
        out = []
        for event in self.events:
            if event.kind == "worker_stats":
                continue
            if index is None or getattr(event, "index", None) == index:
                out.append(event.kind)
        return out


def energy_scan(energies=(10.0, 20.0, 30.0, 40.0), **kwargs):
    kwargs.setdefault("seed", 7)
    kwargs.setdefault("extract", ["echo_energy"])
    return Scan(BASE, grid={"ion.energy_keV": list(energies)}, **kwargs)


def pid_gone(pid, timeout=15.0):
    """True once process ``pid`` is gone or a zombie (a zombie has no more work to do)."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            os.kill(pid, 0)
        except ProcessLookupError:
            return True
        status = Path(f"/proc/{pid}/stat")
        if status.exists() and status.read_text().split(")")[-1].split()[0] == "Z":
            return True
        time.sleep(0.1)
    return False


# -- the normal path -----------------------------------------------------------------------


def test_every_point_runs_and_the_table_has_one_row_per_point(tmp_path):
    scan = Scan(BASE, grid={"ion.energy_keV": [10.0, 20.0, 30.0, 40.0]})  # default extract
    result = run_scan(scan, workers=2, runner=fixture_runner, scan_dir=tmp_path / "s")
    assert isinstance(result, ScanResult)
    assert result.table["status"].tolist() == ["done"] * 4
    assert result.table["ion.energy_keV"].tolist() == [10.0, 20.0, 30.0, 40.0]
    assert "mean_depth_A" in result.table.columns  # the default extractor ran in the parent
    assert result.table["n_ions"].tolist() == [10] * 4
    assert result.table["path"].tolist() == [f"points/{p.id[:16]}" for p in scan.points()]


def test_the_scan_folder_holds_scan_yaml_points_and_result(tmp_path):
    folder = tmp_path / "scan"
    scan = energy_scan()
    run_scan(scan, workers=2, runner=fixture_runner, scan_dir=folder)
    recorded = Scan.from_yaml(folder / "scan.yaml")
    assert recorded.seed == 7
    assert [p.id for p in recorded.points()] == [p.id for p in scan.points()]
    for point in scan.points():
        assert (folder / "points" / point.id[:16] / "RANGE.txt").is_file()
    assert (folder / "result" / "scan.csv").is_file()
    assert (folder / "result" / "metadata.yaml").is_file()


def test_point_seeds_are_derived_from_the_base_seed_and_the_point_id(tmp_path):
    scan = energy_scan()
    result = run_scan(scan, workers=2, runner=fixture_runner, scan_dir=tmp_path / "s")
    for point, text in zip(scan.points(), result.table["seeds"], strict=True):
        assert text == str(derive_seed(7, int(point.id[:15], 16)))


def test_seeds_do_not_change_when_points_are_reordered(tmp_path):
    grid = run_scan(
        energy_scan(), workers=2, runner=fixture_runner, scan_dir=tmp_path / "grid"
    ).table
    reordered = Scan(
        BASE,
        points=[{"ion.energy_keV": e} for e in (30.0, 10.0, 40.0, 20.0)],
        extract=["echo_energy"],
        seed=7,
    )
    other = run_scan(reordered, workers=2, runner=fixture_runner, scan_dir=tmp_path / "rows").table
    by_energy = dict(zip(grid["ion.energy_keV"], grid["seeds"], strict=True))
    for energy, seeds in zip(other["ion.energy_keV"], other["seeds"], strict=True):
        assert seeds == by_energy[energy]


def test_base_seed_is_drawn_and_recorded_when_the_scan_has_none(tmp_path):
    folder = tmp_path / "s"
    recorder = Recorder()
    result = run_scan(
        energy_scan(seed=None),
        workers=1,
        runner=fixture_runner,
        scan_dir=folder,
        on_event=recorder,
    )
    started = recorder.events[0]
    assert isinstance(started, ScanStarted)
    assert 1 <= started.base_seed <= 2**31 - 1
    assert Scan.from_yaml(folder / "scan.yaml").seed == started.base_seed
    assert result.scan.seed == started.base_seed


def test_default_worker_count_is_cpu_count_minus_one(tmp_path, monkeypatch):
    monkeypatch.setattr(os, "cpu_count", lambda: 3)
    recorder = Recorder()
    run_scan(energy_scan(), runner=fixture_runner, scan_dir=tmp_path / "s", on_event=recorder)
    assert recorder.events[0].workers == 2  # 4 points, 2 workers


def test_workers_never_exceed_the_number_of_points(tmp_path):
    recorder = Recorder()
    run_scan(
        energy_scan((10.0, 20.0)),
        workers=8,
        runner=fixture_runner,
        scan_dir=tmp_path / "s",
        on_event=recorder,
    )
    assert recorder.events[0].workers == 2
    assert {e.worker for e in recorder.events if isinstance(e, PointStarted)} <= {0, 1}


def test_events_come_in_order_and_each_point_has_its_own_sequence(tmp_path):
    recorder = Recorder()
    run_scan(
        energy_scan(), workers=2, runner=fixture_runner, scan_dir=tmp_path / "s", on_event=recorder
    )
    events = recorder.events
    assert isinstance(events[0], ScanStarted)
    assert (events[0].n_points, events[0].base_seed) == (4, 7)
    assert isinstance(events[-1], ScanFinished)
    assert (events[-1].n_done, events[-1].n_failed, events[-1].stopped) == (4, 0, False)
    queued = [i for i, e in enumerate(events) if isinstance(e, PointQueued)]
    started = [i for i, e in enumerate(events) if isinstance(e, PointStarted)]
    assert len(queued) == 4 and max(queued) < min(started)
    for index in range(4):
        assert recorder.kinds(index) == [
            "point_queued",
            "point_started",
            "point_progress",
            "point_progress",
            "point_finished",
        ]
    finished = [e for e in events if isinstance(e, PointFinished)]
    assert {e.values["energy_echo_keV"] for e in finished} == {10.0, 20.0, 30.0, 40.0}


# -- retries and failures ------------------------------------------------------------------


def test_a_failed_attempt_is_retried_with_the_same_seed(tmp_path, state_dir):
    recorder = Recorder()
    scan = energy_scan((10.0, 20.0))
    result = run_scan(
        scan,
        workers=2,
        runner=flaky_runner,
        scan_dir=tmp_path / "s",
        on_event=recorder,
    )
    assert result.table["status"].tolist() == ["done", "done"]
    retry_index = 1
    assert recorder.kinds(retry_index) == [
        "point_queued",
        "point_started",
        "point_progress",
        "point_failed",
        "point_retried",
        "point_started",
        "point_progress",
        "point_progress",
        "point_finished",
    ]
    failed = [e for e in recorder.events if isinstance(e, PointFailed)]
    assert len(failed) == 1 and failed[0].final is False and failed[0].attempt == 1
    retried = [e for e in recorder.events if isinstance(e, PointRetried)]
    assert retried[0].attempt == 2
    assert result.table["seeds"].tolist() == [
        str(derive_seed(7, int(p.id[:15], 16))) for p in scan.points()
    ]
    log = (tmp_path / "s" / "points" / scan.points()[1].id[:16] / "error.log").read_text()
    assert "attempt 1: RuntimeError: first try fails" in log


def test_a_point_that_fails_every_attempt_is_reported_and_the_scan_goes_on(tmp_path):
    recorder = Recorder()
    scan = energy_scan()
    result = run_scan(
        scan,
        workers=2,
        runner=failing_runner,
        scan_dir=tmp_path / "s",
        on_event=recorder,
    )
    assert result.table["status"].tolist() == ["done", "failed", "done", "done"]
    row = result.table.iloc[1]
    assert row["error"] == "ValueError: SRIM said no second line"  # one line
    assert row["path"] == f"points/{scan.points()[1].id[:16]}"
    assert recorder.kinds(1) == [
        "point_queued",
        "point_started",
        "point_progress",
        "point_failed",
        "point_retried",
        "point_started",
        "point_progress",
        "point_failed",
    ]
    final = [e for e in recorder.events if isinstance(e, PointFailed) and e.final]
    assert len(final) == 1 and final[0].attempt == 2
    log = (tmp_path / "s" / "points" / scan.points()[1].id[:16] / "error.log").read_text()
    assert "attempt 2" in log and "Traceback" in log
    assert recorder.events[-1].n_failed == 1 and recorder.events[-1].n_done == 3


def test_with_no_retries_the_first_failure_is_final(tmp_path):
    recorder = Recorder()
    result = run_scan(
        energy_scan((20.0,)),
        workers=1,
        runner=failing_runner,
        scan_dir=tmp_path / "s",
        retries=0,
        on_event=recorder,
    )
    assert result.table["status"].tolist() == ["failed"]
    assert recorder.kinds() == [
        "scan_started",
        "point_queued",
        "point_started",
        "point_progress",
        "point_failed",
        "scan_finished",
    ]


def test_an_error_in_on_event_is_logged_and_the_scan_goes_on(tmp_path, caplog):
    def broken(event):
        raise RuntimeError("the UI broke")

    with caplog.at_level(logging.WARNING, logger="srim.scan.executor"):
        result = run_scan(
            energy_scan((10.0, 20.0)),
            workers=2,
            runner=fixture_runner,
            scan_dir=tmp_path / "s",
            on_event=broken,
        )
    assert result.table["status"].tolist() == ["done", "done"]
    assert any("on_event raised" in r.getMessage() for r in caplog.records)


# -- stopping ------------------------------------------------------------------------------


def wait_for_child(state, timeout=30.0):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        found = sorted(state.glob("child-*"))
        if found:
            return int(found[0].name.split("-")[1])
        time.sleep(0.05)
    raise AssertionError("no child process was started")


def stop_mid_scan(tmp_path, state, stop_how):
    """Run four points on two workers; 10 keV finishes at once, the others wait for a child.

    When the first point finishes, the scan is stopped (``stop_how``) once a child process
    exists. Returns the result, the events and the pid of the child.
    """
    stop = threading.Event()

    recorder = Recorder()
    scan = energy_scan()
    holder = {}

    def watch(event):
        recorder(event)
        if isinstance(event, PointFinished):
            holder["child"] = wait_for_child(state)  # the running point is in progress
            if stop_how == "event":
                stop.set()
            else:
                os.kill(os.getpid(), signal.SIGINT)  # what Ctrl-C does

    result = run_scan(
        scan,
        workers=2,
        runner=sleepy_runner,
        scan_dir=tmp_path / "s",
        on_event=watch,
        stop_event=stop,
    )
    return result, recorder.events, holder["child"]


@pytest.mark.skipif(sys.platform == "win32", reason="process groups are POSIX")
def test_stop_event_ends_running_points_without_orphans(tmp_path, state_dir):
    result, events, child = stop_mid_scan(tmp_path, state_dir, "event")
    assert result.table["status"].tolist() == ["done", "stopped", "stopped", "stopped"]
    assert result.table["path"].iloc[1:].isna().all()
    assert not (tmp_path / "s" / "points" / result.table["id"].iloc[1][:16]).exists()
    assert isinstance(events[-1], ScanFinished) and events[-1].stopped is True
    assert events[-1].n_done == 1
    stopped = [e for e in events if isinstance(e, PointStopped)]
    assert [e.index for e in stopped] == [1, 2, 3]
    assert all(e.error is None for e in stopped)
    assert all(i < len(events) - 1 for i, e in enumerate(events) if isinstance(e, PointStopped))
    assert pid_gone(child), "the SRIM stand-in child is still running after the stop"


#: a grandchild that ignores SIGTERM: only SIGKILL to its process group ends it
STUBBORN_CHILD = (
    "import signal, time; signal.signal(signal.SIGTERM, signal.SIG_IGN); time.sleep(120)"
)


def stubborn_runner(spec):
    """Starts a child that ignores SIGTERM, then waits for it (for the stop tests)."""
    child = subprocess.Popen([sys.executable, "-c", STUBBORN_CHILD])
    Path(os.environ[STATE_VAR], f"child-{child.pid}").touch()
    Path(os.environ[STATE_VAR], f"worker-{os.getpid()}").touch()
    child.wait()
    return fixture_runner(spec)


@pytest.mark.skipif(sys.platform == "win32", reason="process groups are POSIX")
def test_a_child_that_ignores_sigterm_is_killed_with_its_worker(tmp_path, state_dir):
    stop = threading.Event()

    def stop_when_running(event):
        if isinstance(event, PointStarted):
            holder["child"] = wait_for_child(state_dir)
            stop.set()

    holder = {}
    result = run_scan(
        energy_scan((20.0,)),
        workers=1,
        runner=stubborn_runner,
        scan_dir=tmp_path / "s",
        stop_event=stop,
        on_event=stop_when_running,
    )
    assert result.table["status"].tolist() == ["stopped"]
    assert pid_gone(holder["child"]), "the child that ignores SIGTERM survived the stop"


def wait_for_pids(state, pattern, count, timeout=60.0):
    """The pids written by the fake runners as ``<pattern>-<pid>`` files."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        found = sorted(state.glob(f"{pattern}-*"))
        if len(found) >= count:
            return [int(p.name.rsplit("-", 1)[1]) for p in found]
        time.sleep(0.05)
    raise AssertionError(f"fewer than {count} {pattern} files appeared")


DRIVER = """
import sys
sys.path.insert(0, {testdir!r})
from srim.scan import Scan, run_scan
from test_executor import BASE, stubborn_runner

if __name__ == "__main__":
    scan = Scan(BASE, grid={{"ion.energy_keV": [10.0, 20.0]}}, extract=["echo_energy"], seed=7)
    run_scan(scan, workers=2, runner=stubborn_runner, scan_dir={scan_dir!r})
"""


@pytest.mark.skipif(sys.platform == "win32", reason="process groups are POSIX")
def test_workers_and_their_children_die_when_the_parent_is_killed(tmp_path, state_dir):
    driver = tmp_path / "driver.py"
    driver.write_text(
        DRIVER.format(testdir=str(Path(__file__).parent), scan_dir=str(tmp_path / "s"))
    )
    env = {**os.environ, STATE_VAR: str(state_dir)}
    proc = subprocess.Popen(
        [sys.executable, str(driver)],
        env=env,
        cwd=tmp_path,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    try:
        workers = wait_for_pids(state_dir, "worker", 2)
        children = wait_for_pids(state_dir, "child", 2)
    finally:
        proc.kill()  # no cleanup in the parent at all
        proc.wait(timeout=10)
    for pid in workers + children:
        assert pid_gone(pid, timeout=10.0), f"process {pid} outlived its parent"


def exiting_runner(spec):
    """The worker process dies at once, without a message."""
    os._exit(3)


def test_workers_that_keep_dying_stop_the_scan_with_an_error(tmp_path):
    with pytest.raises(RuntimeError, match="exit code 3"):
        run_scan(
            energy_scan((10.0,)),
            workers=1,
            runner=exiting_runner,
            scan_dir=tmp_path / "s",
            retries=5,
        )


def test_a_worker_releases_its_run_directory_once(monkeypatch):
    from srim.scan import executor

    class FakeRunDir:
        cleaned = 0

        def cleanup(self):
            FakeRunDir.cleaned += 1

    monkeypatch.setitem(executor._WORKER, "run_dir", FakeRunDir())
    executor._release_run_dir()
    executor._release_run_dir()
    assert FakeRunDir.cleaned == 1
    assert executor._WORKER["run_dir"] is None


@pytest.mark.skipif(sys.platform == "win32", reason="SIGINT is simulated with os.kill")
def test_ctrl_c_stops_the_scan_and_restores_the_previous_handler(tmp_path, state_dir):
    before = signal.getsignal(signal.SIGINT)
    result, events, child = stop_mid_scan(tmp_path, state_dir, "sigint")
    assert result.table["status"].tolist() == ["done", "stopped", "stopped", "stopped"]
    assert events[-1].stopped is True
    assert pid_gone(child), "the SRIM stand-in child is still running after Ctrl-C"
    assert signal.getsignal(signal.SIGINT) is before


def test_a_stop_before_the_start_runs_nothing(tmp_path):
    stop = threading.Event()
    stop.set()
    recorder = Recorder()
    result = run_scan(
        energy_scan(),
        workers=2,
        runner=fixture_runner,
        scan_dir=tmp_path / "s",
        stop_event=stop,
        on_event=recorder,
    )
    assert result.table["status"].tolist() == ["stopped"] * 4
    assert not any(isinstance(e, PointStarted) for e in recorder.events)
    assert recorder.events[-1].stopped is True
    assert sum(isinstance(e, PointStopped) for e in recorder.events) == 4


# -- arguments ----------------------------------------------------------------------------


def fail_once_then_fixture(spec):
    """20 keV fails its first attempt; the marker file tells later attempts apart."""
    marker = Path(os.environ[STATE_VAR]) / "tried-20"
    if energy_of(spec) == 20.0 and not marker.exists():
        marker.touch()
        raise RuntimeError("first try fails")
    return fixture_runner(spec)


def test_a_stop_during_a_retry_keeps_the_error_log_and_last_error(tmp_path, state_dir):
    """Attempt 1 of 20 keV fails and the scan stops before its retry runs."""
    stop = threading.Event()

    def stop_on_failure(event):
        if isinstance(event, PointFailed) and not event.final:
            stop.set()

    recorder = Recorder(after=stop_on_failure)
    result = run_scan(
        energy_scan(),
        workers=2,
        runner=fail_once_then_fixture,
        scan_dir=tmp_path / "s",
        stop_event=stop,
        on_event=recorder,
    )
    row = result.table.iloc[1]
    assert row["status"] == "stopped"
    assert row["error"] == "RuntimeError: first try fails"
    folder = tmp_path / "s" / "points" / energy_scan().points()[1].id[:16]
    assert "attempt 1" in (folder / "error.log").read_text()
    assert not (folder / "RANGE.txt").exists()  # nothing of the cut-short attempt is kept
    stopped = [e for e in recorder.events if isinstance(e, PointStopped) and e.index == 1]
    assert stopped and stopped[0].error == "RuntimeError: first try fails"
    assert (state_dir / "tried-20").exists()  # attempt 1 did run


def picky_extractor(results, point):
    """Fails for 20 keV: the outputs exist, so the point is failed without a SRIM retry."""
    if point.params["ion.energy_keV"] == 20.0:
        raise ValueError("no good")
    return {"ok": 1.0}


def counting_runner(spec):
    Path(os.environ[STATE_VAR], f"run-{energy_of(spec)}-{time.monotonic_ns()}").touch()
    return fixture_runner(spec)


def test_an_extractor_failure_fails_the_point_without_a_srim_retry(tmp_path, state_dir):
    scan = Scan(BASE, grid={"ion.energy_keV": [10.0, 20.0]}, extract=[picky_extractor], seed=5)
    result = run_scan(scan, workers=2, runner=counting_runner, scan_dir=tmp_path / "s", retries=2)
    assert result.table["status"].tolist() == ["done", "failed"]
    failed = result.table.iloc[1]
    assert failed["error"] == "ValueError: no good"
    assert failed["n_ions"] == 10  # the outputs exist: the requested count is kept
    assert len(list(state_dir.glob("run-20.0-*"))) == 1  # SRIM ran once, not retried
    log = (tmp_path / "s" / "points" / scan.points()[1].id[:16] / "error.log").read_text()
    assert "Traceback" in log and "no good" in log


def test_unknown_backends_are_refused(tmp_path):
    with pytest.raises(ValueError, match="backend must be 'local' or 'container'"):
        run_scan(energy_scan(), backend="slurm", runner=fixture_runner, scan_dir=tmp_path / "s")
    assert not (tmp_path / "s").exists()  # refused before anything is written


def plain_extractor(results, point):
    """A module-level Python extractor: recorded in scan.yaml and read back by name."""
    return {"plain_keV": point.params["ion.energy_keV"] * 2.0}


def test_a_python_extractor_runs_and_is_recorded_as_a_reference(tmp_path):
    folder = tmp_path / "s"
    scan = Scan(BASE, grid={"ion.energy_keV": [10.0, 20.0]}, extract=[plain_extractor], seed=3)
    result = run_scan(scan, workers=2, runner=fixture_runner, scan_dir=folder)
    assert result.table["plain_keV"].tolist() == [20.0, 40.0]
    assert f"python:{__name__}:plain_extractor" in (folder / "scan.yaml").read_text()
    assert resolve(Scan.from_yaml(folder / "scan.yaml").extract[0]) is plain_extractor


@pytest.mark.parametrize("workers", [0, -1, 1.5, True])
def test_workers_must_be_a_positive_whole_number(tmp_path, workers):
    with pytest.raises(ValueError, match="workers"):
        run_scan(energy_scan((10.0,)), workers=workers, runner=fixture_runner)


def test_a_new_scan_folder_is_made_when_none_is_given(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    result = run_scan(energy_scan((10.0,)), workers=1, runner=fixture_runner)
    folders = [p for p in tmp_path.iterdir() if p.name.startswith("scan-")]
    assert len(folders) == 1
    assert (folders[0] / "scan.yaml").is_file()
    assert result.table["status"].tolist() == ["done"]


def test_the_run_directory_prune_never_fails_a_scan(monkeypatch):
    from srim import rundir
    from srim.scan import executor

    def broken(*args, **kwargs):
        raise OSError("data directory unreadable")

    monkeypatch.setattr(rundir, "prune_runs", broken)
    executor._prune_old_runs()  # must not raise


def test_a_folder_with_other_files_never_becomes_a_scan_folder(tmp_path):
    from srim.scan.executor import MARKER_NAME, mark_scan_folder

    project = tmp_path / "project"
    project.mkdir()
    (project / "notes.txt").write_text("mine")
    with pytest.raises(ValueError, match="not a scan folder"):
        mark_scan_folder(project)
    assert not (project / MARKER_NAME).exists()
    empty = tmp_path / "empty"
    empty.mkdir()
    mark_scan_folder(empty)
    (empty / "scan.yaml").write_text("x")
    mark_scan_folder(empty)  # a scan folder stays one when it is reused
    assert (empty / MARKER_NAME).is_file()


# -- pause ---------------------------------------------------------------------------------


def resume_after(pause, resumed, delay=0.3):
    """On ScanPaused(True) of a test: lift the pause after ``delay`` seconds (a timer thread;
    run_scan itself stays on the calling thread)."""

    def lift():
        pause.clear()
        resumed.set()

    return threading.Timer(delay, lift)


@pytest.fixture
def lift_pause_if_stuck():
    """A pause that nobody lifts holds the scan for ever. ``guard(pause)`` starts a timer
    that lifts it after 20 s, so a test that misses its event fails instead of hanging."""
    timers = []

    def guard(pause):
        timer = threading.Timer(20.0, pause.clear)
        timer.daemon = True
        timer.start()
        timers.append(timer)

    yield guard
    for timer in timers:
        timer.cancel()


def test_a_pause_before_the_start_starts_nothing_until_resumed(tmp_path, lift_pause_if_stuck):
    pause = threading.Event()
    pause.set()
    lift_pause_if_stuck(pause)
    recorder = Recorder()
    resumed = threading.Event()
    timers = []

    def on_event(event):
        recorder(event)
        if isinstance(event, ScanPaused) and event.paused:
            timers.append(resume_after(pause, resumed).start())

    result = run_scan(
        energy_scan((20.0, 30.0)),
        workers=1,
        runner=fixture_runner,
        scan_dir=tmp_path / "s",
        pause_event=pause,
        on_event=on_event,
    )
    assert resumed.is_set() and timers
    flags = [(i, e.paused) for i, e in enumerate(recorder.events) if isinstance(e, ScanPaused)]
    assert [paused for _, paused in flags] == [True, False]
    resumed_at = flags[1][0]
    assert not any(isinstance(e, PointStarted) for e in recorder.events[:resumed_at]), (
        "a point started while the scan was paused"
    )
    assert result.table["status"].tolist() == ["done", "done"]


def test_a_pause_during_the_scan_lets_the_running_point_finish(tmp_path, lift_pause_if_stuck):
    pause = threading.Event()
    lift_pause_if_stuck(pause)
    recorder = Recorder()
    resumed = threading.Event()

    def on_event(event):
        recorder(event)
        if isinstance(event, PointStarted) and event.index == 0:
            pause.set()  # on the calling thread: the next dispatch sees the flag
        if isinstance(event, ScanPaused) and event.paused:
            resume_after(pause, resumed).start()

    result = run_scan(
        energy_scan((10.0, 20.0, 30.0)),
        workers=1,
        runner=fixture_runner,
        scan_dir=tmp_path / "s",
        pause_event=pause,
        on_event=on_event,
    )
    events = recorder.events
    paused_at = next(i for i, e in enumerate(events) if isinstance(e, ScanPaused) and e.paused)
    resumed_at = next(i for i, e in enumerate(events) if isinstance(e, ScanPaused) and not e.paused)
    # point 0 was running when the pause came: it finishes while the others wait
    assert any(isinstance(e, PointFinished) and e.index == 0 for e in events[paused_at:])
    assert not any(isinstance(e, PointStarted) for e in events[paused_at:resumed_at])
    assert result.table["status"].tolist() == ["done", "done", "done"]


def test_scan_paused_is_sent_only_when_the_flag_changes(tmp_path, lift_pause_if_stuck):
    pause = threading.Event()
    lift_pause_if_stuck(pause)
    recorder = Recorder()
    resumed = threading.Event()

    def on_event(event):
        recorder(event)
        if isinstance(event, PointFinished) and event.index == 0:
            pause.set()
        if isinstance(event, ScanPaused) and event.paused:
            resume_after(pause, resumed).start()

    run_scan(
        energy_scan((10.0, 20.0)),
        workers=1,
        runner=fixture_runner,
        scan_dir=tmp_path / "s",
        pause_event=pause,
        on_event=on_event,
    )
    assert [e.paused for e in recorder.events if isinstance(e, ScanPaused)] == [True, False]


def test_a_stop_while_paused_ends_the_scan_without_starting_points(tmp_path, lift_pause_if_stuck):
    pause = threading.Event()
    pause.set()
    lift_pause_if_stuck(pause)
    stop = threading.Event()
    recorder = Recorder()

    def on_event(event):
        recorder(event)
        if isinstance(event, ScanPaused):
            stop.set()

    result = run_scan(
        energy_scan((10.0, 20.0)),
        workers=1,
        runner=fixture_runner,
        scan_dir=tmp_path / "s",
        pause_event=pause,
        stop_event=stop,
        on_event=on_event,
    )
    assert result.table["status"].tolist() == ["stopped", "stopped"]
    assert not any(isinstance(e, PointStarted) for e in recorder.events)
    assert recorder.events[-1].stopped is True


# -- predefined extractors that need the run spec ------------------------------------------


def test_ions_in_layer_works_in_a_scan_with_fixed_ions(tmp_path):
    """The extractor reads the layer edges from the run spec, which the results must carry."""
    scan = energy_scan(
        energies=(10.0, 20.0),
        extract=[{"ions_in_layer": {"layer_index": 0}}],
    )
    result = run_scan(scan, workers=2, runner=fixture_runner, scan_dir=tmp_path / "s", cache=False)
    assert result.table["status"].tolist() == ["done", "done"]
    values = result.table["fraction_of_stopped_ions_in_layer_0"].tolist()
    assert all(0.0 <= float(v) <= 1.0 for v in values)


def test_ions_in_layer_works_for_cached_points(tmp_path):
    scan = energy_scan(
        energies=(10.0,),
        extract=[{"ions_in_layer": {"layer_index": 0}}],
    )
    first = run_scan(scan, workers=1, runner=fixture_runner, scan_dir=tmp_path / "a")
    again = run_scan(
        scan,
        workers=1,
        runner=never_runs,
        scan_dir=tmp_path / "b",
        cache=tmp_path / "a" / "cache",
    )
    assert again.table["status"].tolist() == ["cached"]
    assert (
        again.table["fraction_of_stopped_ions_in_layer_0"].tolist()
        == first.table["fraction_of_stopped_ions_in_layer_0"].tolist()
    )


def never_runs(spec):
    raise AssertionError("a cached run must not be computed again")


def test_a_refused_foreign_folder_keeps_no_lock_file(tmp_path):
    from srim.scan.executor import LOCK_NAME

    project = tmp_path / "project"
    project.mkdir()
    (project / "notes.txt").write_text("mine")
    with pytest.raises(ValueError, match="not a scan folder"):
        run_scan(energy_scan(), workers=1, runner=fixture_runner, scan_dir=project, cache=False)
    assert sorted(p.name for p in project.iterdir()) == ["notes.txt"]
    assert not (project / LOCK_NAME).exists()
