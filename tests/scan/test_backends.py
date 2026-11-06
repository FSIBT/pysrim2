"""The job folders (run_job, pysrim2 run-job) and the container back end, with fake runtimes.

A fake ``podman`` / ``docker`` script is put first on PATH. It checks the flags the real
runtime would get, records every call in a log file, and runs ``run_job`` in-process on the
mounted job folder with a fake runner (the outputs of test_files/1), so the whole path from
run_scan to the outputs is exercised without a container. Every wait has a timeout and a
SIGALRM guard fails a test that hangs.
"""

import json
import os
import signal
import stat
import sys
import tempfile
import threading
import time
from pathlib import Path

import pytest

from srim.cli import main
from srim.installer import container as container_module
from srim.installer.container import ContainerError
from srim.output import Results
from srim.scan import Scan, ScanResult, backends, derive_seed, run_scan
from srim.scan.backends import ContainerRunner, read_job, run_job, write_job
from srim.spec import RunSpec, SpecError

FIXTURE = Path(__file__).resolve().parents[2] / "test_files" / "1"
GUARD_SECONDS = 180
WAIT_SECONDS = 60
LOG_VAR = "PYSRIM2_TEST_RUNTIME_LOG"
STATE_VAR = "PYSRIM2_TEST_STATE"
MODE_VAR = "PYSRIM2_TEST_RUNTIME_MODE"

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
    """Stands in for SRIM: the outputs of test_files/1 (copied by the caller)."""
    return Results(str(FIXTURE))


def failing_runner(spec):
    if energy_of(spec) == 20.0:
        raise ValueError("SRIM said no\nsecond line")
    return fixture_runner(spec)


def never_runs(spec):
    raise AssertionError("a cached run must not be computed again")


#: the fake runtime: a podman or docker stand-in (the name decides which flags are right)
FAKE_RUNTIME = """#!__PYTHON__
import json, os, stat, sys, time
from pathlib import Path

LOG = os.environ["__LOG_VAR__"]
STATE = os.environ["__STATE_VAR__"]
MODE = os.environ.get("__MODE_VAR__", "ok")
FIXTURE = "__FIXTURE__"


def record(**entry):
    with open(LOG, "a", encoding="utf-8") as f:
        f.write(json.dumps(entry) + "\\n")


def mode_of(path):
    return oct(stat.S_IMODE(os.stat(path).st_mode))


args = sys.argv[1:]
if args[:1] == ["info"]:
    sys.exit(0)
if args[:2] == ["rm", "-f"]:
    record(rm=args[2])
    sys.exit(0)
if args[:1] != ["run"]:
    sys.exit(2)

rest = args[1:]
name = rest[rest.index("--name") + 1]
mounts = [rest[i + 1] for i, a in enumerate(rest) if a == "-v"]
security = [rest[i + 1] for i, a in enumerate(rest) if a == "--security-opt"]
userns = [a for a in rest if a.startswith("--userns=")]
image = rest[-4]
tail = rest[-3:]
job = mounts[0].split(":/data/job")[0]
kind = "docker" if Path(sys.argv[0]).name == "docker" else "podman"
problems = []
if tail != ["pysrim2", "run-job", "/data/job"]:
    problems.append(f"command {tail}")
if len(mounts) != 1 or not mounts[0].endswith(":/data/job:Z" if kind == "podman" else ":/data/job"):
    problems.append(f"mount {mounts}")
if kind == "podman":
    if userns != ["--userns=keep-id:uid=1000,gid=1000"] or security:
        problems.append(f"podman flags {userns} {security}")
else:
    profile = security[0][8:] if security else ""
    seccomp_ok = len(security) == 1 and security[0].startswith("seccomp=")
    if userns or not seccomp_ok or not os.path.isfile(profile):
        problems.append(f"docker flags {security}")
record(
    kind="run",
    name=name,
    image=image,
    job=job,
    job_mode=mode_of(job),
    parent_mode=mode_of(os.path.dirname(job)),
    problems=problems,
)
if problems:
    sys.stderr.write("fake runtime: " + "; ".join(problems) + "\\n")
    sys.exit(125)

if MODE == "hang":
    Path(STATE, "started-" + name).touch()
    time.sleep(120)
    sys.exit(0)

from srim.output import Results
from srim.scan.backends import run_job


def runner(spec):
    energy = spec.to_dict()["ion"]["energy_keV"]
    if MODE == "fail_once" and energy == 20.0:
        marker = Path(STATE, "tried-20")
        if not marker.exists():
            marker.touch()
            raise RuntimeError("first try fails")
    if MODE == "fail_20" and energy == 20.0:
        raise ValueError("SRIM said no\\nsecond line")
    return Results(FIXTURE)


failed = run_job(job, runner=runner)
sys.exit(1 if failed else 0)
"""


class FakeRuntime:
    """The fake podman and docker on PATH, with their log and state folders."""

    def __init__(self, root: Path):
        self.root = root
        self.log = root / "runtime.log"
        self.state = root / "state"
        self.state.mkdir(parents=True)
        self.log.write_text("", encoding="utf-8")

    def records(self):
        lines = self.log.read_text(encoding="utf-8").splitlines()
        return [json.loads(line) for line in lines if line.strip()]

    def runs(self):
        return [e for e in self.records() if e.get("kind") == "run"]

    def removed(self):
        return [e["rm"] for e in self.records() if "rm" in e]


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
def fake_runtime(tmp_path, monkeypatch):
    """podman and docker stand-ins first on PATH; temp files and pysrim2 data in tmp_path."""
    bindir = tmp_path / "bin"
    bindir.mkdir()
    script = FAKE_RUNTIME.replace("__PYTHON__", sys.executable)
    script = script.replace("__LOG_VAR__", LOG_VAR).replace("__STATE_VAR__", STATE_VAR)
    script = script.replace("__MODE_VAR__", MODE_VAR).replace("__FIXTURE__", str(FIXTURE))
    for name in ("podman", "docker"):
        path = bindir / name
        path.write_text(script, encoding="utf-8")
        path.chmod(0o755)
    runtime = FakeRuntime(tmp_path)
    tmpdir = tmp_path / "tmp"
    tmpdir.mkdir()
    monkeypatch.setenv("PATH", f"{bindir}{os.pathsep}{os.environ['PATH']}")
    monkeypatch.setenv("TMPDIR", str(tmpdir))
    monkeypatch.setattr(tempfile, "tempdir", str(tmpdir))
    monkeypatch.setenv(LOG_VAR, str(runtime.log))
    monkeypatch.setenv(STATE_VAR, str(runtime.state))
    monkeypatch.setenv("PYSRIM2_HOME", str(tmp_path / "home"))
    monkeypatch.setenv(MODE_VAR, "ok")
    monkeypatch.delenv("PYSRIM2_IMAGE", raising=False)
    runtime.tmpdir = tmpdir
    return runtime


def unit_specs(*energies):
    return [
        (f"unit{i}", BASE.with_overrides([f"ion.energy_keV={e}", "seed=3"]))
        for i, e in enumerate(energies)
    ]


def energy_scan(energies=(10.0, 20.0), **kwargs):
    kwargs.setdefault("seed", 7)
    return Scan(BASE, grid={"ion.energy_keV": list(energies)}, **kwargs)


def job_dir(tmp_path, units=None):
    job = tmp_path / "job"
    write_job(job, units or unit_specs(10.0, 20.0))
    return job


# -- the job folder format -------------------------------------------------------------------


def test_a_job_folder_round_trips_its_specs(tmp_path):
    job = job_dir(tmp_path)
    units = read_job(job)
    assert [name for name, _ in units] == ["unit0", "unit1"]
    assert units[0][1].to_dict() == unit_specs(10.0)[0][1].to_dict()
    assert units[1][1].seed == 3


def test_a_folder_without_job_yaml_is_not_a_job(tmp_path):
    with pytest.raises(SpecError, match="job.yaml"):
        read_job(tmp_path)


@pytest.mark.parametrize("bad", ["../escape", "a/b", ".hidden", ""])
def test_unit_names_must_be_plain_folder_names(tmp_path, bad):
    job = tmp_path / "job"
    job.mkdir()
    (job / "job.yaml").write_text(
        f"version: 1\nunits:\n- name: '{bad}'\n  spec: {{}}\n", encoding="utf-8"
    )
    with pytest.raises(SpecError, match="name"):
        read_job(job)


def test_an_unknown_job_version_is_refused(tmp_path):
    job = job_dir(tmp_path)
    text = (job / "job.yaml").read_text(encoding="utf-8").replace("version: 1", "version: 9")
    (job / "job.yaml").write_text(text, encoding="utf-8")
    with pytest.raises(SpecError, match="version"):
        read_job(job)


# -- run_job -----------------------------------------------------------------------------------


def counting(calls, fail=None):
    def runner(spec):
        calls.append(energy_of(spec))
        if fail is not None and energy_of(spec) == fail:
            raise ValueError("boom\nmore")
        return fixture_runner(spec)

    return runner


def test_run_job_runs_every_unit_and_writes_done_last(tmp_path):
    job = job_dir(tmp_path)
    calls = []
    assert run_job(job, runner=counting(calls)) == 0
    assert calls == [10.0, 20.0]
    for name in ("unit0", "unit1"):
        out = job / "out" / name
        assert (out / "RANGE.txt").is_file()
        assert (out / ".done").is_file()
        assert not (out / "error.log").exists()


def test_a_failed_unit_is_logged_and_the_others_still_run(tmp_path):
    job = job_dir(tmp_path)
    assert run_job(job, runner=counting([], fail=10.0)) == 1
    failed = job / "out" / "unit0"
    assert not (failed / ".done").exists()
    first_line = (failed / "error.log").read_text(encoding="utf-8").splitlines()[0]
    assert first_line == "ValueError: boom more"
    assert (job / "out" / "unit1" / ".done").is_file()


def test_run_job_skips_done_units_and_retries_failed_ones(tmp_path):
    job = job_dir(tmp_path)
    run_job(job, runner=counting([], fail=10.0))
    calls = []
    assert run_job(job, runner=counting(calls)) == 0
    assert calls == [10.0]  # unit1 was done: not run again
    assert not (job / "out" / "unit0" / "error.log").exists()
    calls.clear()
    assert run_job(job, runner=counting(calls)) == 0
    assert calls == []


def test_a_bad_job_folder_is_refused_before_any_unit_runs(tmp_path):
    job = job_dir(tmp_path)
    text = (job / "job.yaml").read_text(encoding="utf-8")
    (job / "job.yaml").write_text(
        text.replace("number_ions: 10", "number_ions: -1"), encoding="utf-8"
    )
    calls = []
    with pytest.raises(SpecError):
        run_job(job, runner=counting(calls))
    assert calls == []


@pytest.mark.skipif(os.name != "posix", reason="folder modes are POSIX")
def test_outputs_of_a_world_writable_job_folder_are_writable_by_all(tmp_path):
    """Docker runs the container as uid 1000 (any host uid): out/ and each unit folder are
    created 0o777, so the host can remove what the container made. Modes are set explicitly
    (not left to the umask), and a failing unit's folder is opened too."""
    job = job_dir(tmp_path)
    os.chmod(job, 0o777)
    run_job(job, runner=counting([], fail=10.0))
    mode = lambda p: stat.S_IMODE(os.stat(p).st_mode) & 0o777  # noqa: E731
    assert mode(job / "out") == 0o777
    assert mode(job / "out" / "unit0") == 0o777  # failed unit
    assert mode(job / "out" / "unit1") == 0o777  # done unit


@pytest.mark.skipif(os.name != "posix", reason="folder modes are POSIX")
def test_a_job_folder_that_is_not_world_writable_is_left_alone(tmp_path):
    job = job_dir(tmp_path)
    os.chmod(job, 0o755)
    run_job(job, runner=counting([]))
    assert stat.S_IMODE(os.stat(job / "out").st_mode) & 0o022 == 0  # not opened to others


# -- the CLI ----------------------------------------------------------------------------------


@pytest.fixture
def fake_unit_run(monkeypatch):
    """_run_unit without SRIM: writes RANGE.txt, or fails for 20 keV when asked."""

    def run_unit(spec, out, runner):
        if os.environ.get("PYSRIM2_TEST_CLI_FAIL") and energy_of(spec) == 20.0:
            raise RuntimeError("no SRIM here")
        out.mkdir(parents=True, exist_ok=True)
        (out / "RANGE.txt").write_text("x", encoding="utf-8")

    monkeypatch.setattr(backends, "_run_unit", run_unit)


def test_cli_run_job_exit_codes(tmp_path, fake_unit_run, monkeypatch, capsys):
    job = job_dir(tmp_path)
    assert main(["run-job", str(job)]) == 0
    assert "unit0: done" in capsys.readouterr().out
    monkeypatch.setenv("PYSRIM2_TEST_CLI_FAIL", "1")
    other = job_dir(tmp_path / "again")
    assert main(["run-job", str(other)]) == 1
    assert main(["run-job", str(tmp_path / "nothing")]) == 2


# -- the container back end ---------------------------------------------------------------------


def run_container(tmp_path, runtime="podman", **kwargs):
    kwargs.setdefault("workers", 2)
    kwargs.setdefault("image", "pysrim2:test")
    kwargs.setdefault("cache", False)  # every call really runs its containers
    return run_scan(
        energy_scan(),
        backend="container",
        runtime=runtime,
        scan_dir=tmp_path / "scan",
        **kwargs,
    )


def test_each_point_runs_in_one_container_with_the_podman_flags(tmp_path, fake_runtime):
    result = run_container(tmp_path)
    assert result.table["status"].tolist() == ["done", "done"]
    runs = fake_runtime.runs()
    assert len(runs) == 2
    assert {r["image"] for r in runs} == {"pysrim2:test"}
    assert all(r["problems"] == [] for r in runs)
    assert all(r["parent_mode"] == "0o700" for r in runs)
    assert all(r["job_mode"] != "0o777" for r in runs)  # only docker needs it
    assert len({r["name"] for r in runs}) == 2  # one container per run, named uniquely
    assert fake_runtime.removed() == []  # nothing was stopped
    assert (tmp_path / "scan" / "points" / result.scan.points()[0].id[:16] / "RANGE.txt").is_file()


def test_the_temporary_job_folders_are_removed_after_each_point(tmp_path, fake_runtime):
    run_container(tmp_path, workers=1)
    assert list(fake_runtime.tmpdir.glob("pysrim2-job-*")) == []


def test_the_done_marker_does_not_reach_the_points_or_the_cache(tmp_path, fake_runtime):
    scan_dir = tmp_path / "scan"
    result = run_scan(
        energy_scan(),
        workers=1,
        backend="container",
        runtime="podman",
        image="pysrim2:test",
        scan_dir=scan_dir,
        cache=scan_dir / "cache",
    )
    assert result.table["status"].tolist() == ["done", "done"]
    for point in result.scan.points():
        assert not (scan_dir / "points" / point.id[:16] / ".done").exists()
    entries = [p.parent for p in (scan_dir / "cache").rglob(".complete")]
    assert len(entries) == 2
    for entry in entries:
        assert not (entry / ".done").exists()
        assert not (entry / "error.log").exists()
        assert (entry / "RANGE.txt").is_file()


def test_docker_gets_the_seccomp_profile_and_a_world_writable_job_folder(tmp_path, fake_runtime):
    result = run_container(tmp_path, runtime="docker", workers=1)
    assert result.table["status"].tolist() == ["done", "done"]
    runs = fake_runtime.runs()
    assert all(r["problems"] == [] for r in runs)
    assert all(r["job_mode"] == "0o777" for r in runs)
    assert all(r["parent_mode"] == "0o700" for r in runs)


def test_the_container_backend_gives_the_same_events_and_table_as_local(tmp_path, fake_runtime):
    def kinds_of(events):
        return [e.kind for e in events if e.kind != "worker_stats"]

    local_events, container_events = [], []
    scan = energy_scan()
    local = run_scan(
        scan,
        workers=1,
        runner=fixture_runner,
        scan_dir=tmp_path / "local",
        cache=False,
        on_event=local_events.append,
    )
    boxed = run_scan(
        scan,
        workers=1,
        backend="container",
        runtime="podman",
        image="pysrim2:test",
        scan_dir=tmp_path / "boxed",
        cache=False,
        on_event=container_events.append,
    )
    assert kinds_of(container_events) == kinds_of(local_events)
    assert boxed.table["status"].tolist() == local.table["status"].tolist()
    assert boxed.table["seeds"].tolist() == local.table["seeds"].tolist()
    assert boxed.table["mean_depth_A"].tolist() == local.table["mean_depth_A"].tolist()


def test_a_failing_unit_is_retried_and_then_succeeds(tmp_path, fake_runtime, monkeypatch):
    monkeypatch.setenv(MODE_VAR, "fail_once")
    events = []
    result = run_container(tmp_path, workers=1, retries=1, on_event=events.append)
    assert result.table["status"].tolist() == ["done", "done"]
    assert any(e.kind == "point_retried" for e in events)


def test_a_unit_that_keeps_failing_is_reported_with_its_error(tmp_path, fake_runtime, monkeypatch):
    monkeypatch.setenv(MODE_VAR, "fail_20")
    result = run_container(tmp_path, workers=1, retries=0)
    assert result.table["status"].tolist() == ["done", "failed"]  # 10 keV, then 20 keV
    assert result.table["error"].iloc[1] == "ValueError: SRIM said no second line"


def test_stopping_the_scan_removes_the_running_container(tmp_path, fake_runtime, monkeypatch):
    """A watcher thread stops the scan once the fake container has started (its marker
    file appears). If that takes longer than WAIT_SECONDS it stops the scan anyway and the
    test fails with a message, rather than hanging."""
    monkeypatch.setenv(MODE_VAR, "hang")
    stop = threading.Event()
    timed_out = []

    def watch():
        deadline = time.monotonic() + WAIT_SECONDS
        while time.monotonic() < deadline and not list(fake_runtime.state.iterdir()):
            time.sleep(0.05)
        if not list(fake_runtime.state.iterdir()):
            timed_out.append(True)
        stop.set()

    watcher = threading.Thread(target=watch, daemon=True)
    watcher.start()
    result = run_container(tmp_path, workers=1, stop_event=stop)
    watcher.join(timeout=WAIT_SECONDS)
    assert not timed_out, "the fake container never started (no marker file)"
    assert result.table["status"].tolist() == ["stopped", "stopped"]
    names = [r["name"] for r in fake_runtime.runs()]
    assert names and fake_runtime.removed() == names[:1]
    assert list(fake_runtime.tmpdir.glob("pysrim2-job-*")) == []


def test_no_runtime_is_a_clear_error_before_any_worker_starts(tmp_path, monkeypatch):
    monkeypatch.setattr(container_module, "find_runtime", lambda: None)
    with pytest.raises(ContainerError, match="no container runtime"):
        run_scan(energy_scan(), backend="container", scan_dir=tmp_path / "scan")
    assert not (tmp_path / "scan").exists()


def test_the_image_is_the_argument_then_the_environment_then_the_release(
    tmp_path, fake_runtime, monkeypatch
):
    monkeypatch.setenv("PYSRIM2_IMAGE", "pysrim2:env")
    run_container(tmp_path / "a", workers=1, image=None)
    assert {r["image"] for r in fake_runtime.runs()} == {"pysrim2:env"}
    run_container(tmp_path / "b", workers=1, image="pysrim2:arg")
    assert {r["image"] for r in fake_runtime.runs()[2:]} == {"pysrim2:arg"}
    monkeypatch.delenv("PYSRIM2_IMAGE")
    run_container(tmp_path / "c", workers=1, image=None)
    release = container_module.image_ref(container_module.image_tag())
    assert {r["image"] for r in fake_runtime.runs()[4:]} == {release}


def test_a_runner_cannot_be_combined_with_the_container_backend(tmp_path):
    with pytest.raises(ValueError, match="runner"):
        run_scan(energy_scan(), backend="container", runner=fixture_runner, runtime="podman")


def test_the_docker_runner_resolves_its_seccomp_profile_once(tmp_path, monkeypatch):
    monkeypatch.setenv("PYSRIM2_HOME", str(tmp_path / "home"))
    docker = ContainerRunner("docker", "pysrim2:test")
    assert docker.seccomp is not None and Path(docker.seccomp).is_file()
    assert ContainerRunner("podman", "pysrim2:test").seccomp is None


def test_the_runner_is_picklable_for_the_worker_processes():
    import pickle

    runner = ContainerRunner("podman", "pysrim2:test")
    clone = pickle.loads(pickle.dumps(runner))
    assert (clone.runtime, clone.image) == ("podman", "pysrim2:test")


def test_scanresult_type_is_returned(tmp_path, fake_runtime):
    assert isinstance(run_container(tmp_path, workers=1), ScanResult)


def test_seeds_in_the_container_match_the_derivation(tmp_path, fake_runtime):
    scan = energy_scan()
    result = run_container(tmp_path, workers=1)
    expected = [str(derive_seed(7, int(p.id[:15], 16))) for p in scan.points()]
    assert result.table["seeds"].tolist() == expected
