"""pysrim2 scan: run, prepare, collect, clean, the views and the exit codes.

SRIM is replaced by a runner module written into the test folder: it returns the outputs of
test_files/1 (a real SRIM result), and fails for the energy in ``PYSRIM2_TEST_FAIL_ENERGY``.
``PYSRIM2_SCAN_RUNNER`` names it for the command line (the worker processes import it too).
Every wait has a timeout, and a SIGALRM guard (POSIX) fails a test that hangs.
"""

import io
import json
import os
import queue
import signal
import socket
import sys
import threading
import time
from pathlib import Path

import pytest

import srim.scan
import srim.ui.web as web
from srim import cli
from srim.scan import Scan
from srim.scan.backends import run_job
from srim.spec import RunSpec

FIXTURE = Path(__file__).resolve().parent.parent / "test_files" / "1"
GUARD_SECONDS = 120
RUNNER_MODULE = "fake_srim_for_scan_cli"
RUNNER_REF = f"{RUNNER_MODULE}:run"

RUNNER_SOURCE = f'''"""Stands in for SRIM in the scan command tests."""

import os
import subprocess
import sys
import time
from pathlib import Path

from srim.output import Results

FIXTURE = Path({str(FIXTURE)!r})


def run(spec):
    started = os.environ.get("PYSRIM2_TEST_STARTED")
    if started:
        Path(started).touch()  # tells the test that a unit is running
    if os.environ.get("PYSRIM2_TEST_SLEEP"):
        time.sleep(float(os.environ["PYSRIM2_TEST_SLEEP"]))
    if os.environ.get("PYSRIM2_TEST_NOISE"):
        print("noise from the runner")  # what SRIM or wine may print
        subprocess.run([sys.executable, "-c", "print('noise from a child')"], check=True)
    fail = os.environ.get("PYSRIM2_TEST_FAIL_ENERGY")
    if fail and spec.to_dict()["ion"]["energy_keV"] == float(fail):
        raise ValueError("SRIM said no")
    return Results(str(FIXTURE))
'''

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


@pytest.fixture(autouse=True)
def guard():
    """Fail (instead of hanging) if a test runs longer than GUARD_SECONDS (POSIX)."""
    if not hasattr(signal, "SIGALRM"):
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
def work(tmp_path, monkeypatch):
    """A folder with the fake SRIM importable, the runner selected and no environment
    cache or failure left over from the developer's shell."""
    (tmp_path / f"{RUNNER_MODULE}.py").write_text(RUNNER_SOURCE, encoding="utf-8")
    monkeypatch.syspath_prepend(str(tmp_path))
    monkeypatch.setenv(cli.SCAN_RUNNER_VAR, RUNNER_REF)
    monkeypatch.delenv("PYSRIM2_CACHE", raising=False)
    monkeypatch.delenv("PYSRIM2_TEST_FAIL_ENERGY", raising=False)
    return tmp_path


def write_scan(folder: Path, energies=(10.0, 13.0)) -> Path:
    path = folder / "scan.yaml"
    Scan(BASE, grid={"ion.energy_keV": list(energies)}, seed=7).to_yaml(path)
    return path


def json_rows(text: str) -> list[dict]:
    return [json.loads(line) for line in text.splitlines()]


def test_plain_run_prints_status_and_result_and_exits_1_on_failure(work, monkeypatch, capsys):
    monkeypatch.setenv("PYSRIM2_TEST_FAIL_ENERGY", "13.0")
    scan = write_scan(work)
    out = work / "out"
    code = cli.main(["scan", "run", str(scan), "--workers", "2", "--ui", "plain", "-o", str(out)])
    captured = capsys.readouterr()
    assert code == 1
    lines = captured.out.splitlines()
    assert lines[0].startswith("done 0/2")
    assert lines[-2].startswith("finished 1/2 done, 1 failed, 0 stopped")
    assert lines[-1] == f"result: {out / 'result' / 'scan.csv'}"
    assert (out / "result" / "scan.csv").is_file()
    assert "1 point(s) failed or are missing" in captured.err
    assert "\x1b" not in captured.out


def test_plain_run_with_every_point_done_exits_0(work, capsys):
    code = cli.main(["scan", "run", str(write_scan(work)), "--workers", "2", "--ui", "plain",
                     "-o", str(work / "o")])  # fmt: skip
    assert code == 0
    assert (
        capsys.readouterr().err
        == f"pysrim2: using test runner {RUNNER_REF} (PYSRIM2_SCAN_RUNNER)\n"
    )


def test_json_run_writes_only_events_to_stdout(work, monkeypatch, capsys):
    monkeypatch.setenv("PYSRIM2_TEST_FAIL_ENERGY", "13.0")
    out = work / "out"
    code = cli.main(["scan", "run", str(write_scan(work)), "--ui", "json", "-o", str(out)])
    captured = capsys.readouterr()
    assert code == 1
    rows = json_rows(captured.out)  # every line of stdout is an event
    assert rows[0]["kind"] == "scan_started"
    assert rows[-1]["kind"] == "scan_finished"
    assert (rows[-1]["n_done"], rows[-1]["n_failed"], rows[-1]["stopped"]) == (1, 1, False)
    assert captured.out.count("\n") == len(rows)
    assert f"result: {out / 'result' / 'scan.csv'}" in captured.err


def test_stopped_scan_exits_130_and_keeps_the_partial_result(work, monkeypatch, capsys):
    real = srim.scan.run_scan

    def stop_before_start(scan, **kwargs):
        kwargs["stop_event"].set()  # what Ctrl-C does, at once
        return real(scan, **kwargs)

    monkeypatch.setattr(srim.scan, "run_scan", stop_before_start)
    out = work / "out"
    code = cli.main(["scan", "run", str(write_scan(work)), "--ui", "json", "-o", str(out)])
    captured = capsys.readouterr()
    assert code == 130
    rows = json_rows(captured.out)
    assert rows[-1]["kind"] == "scan_finished" and rows[-1]["stopped"] is True
    assert (out / "result" / "scan.csv").is_file()
    assert "stopped: the partial result above is kept" in captured.err


class Tty(io.StringIO):
    def isatty(self):
        return True


def test_graphical_ui_falls_back_to_plain_with_the_reason(monkeypatch, capsys):
    monkeypatch.setattr(cli.importlib.util, "find_spec", lambda name: None)
    assert cli._view_for("plot") == "plain"
    assert "--ui plot: the optional textual package is not installed; using --ui plain" in (
        capsys.readouterr().err
    )
    monkeypatch.setattr(cli.importlib.util, "find_spec", lambda name: object())
    assert cli._view_for("plot") == "plain"  # not a terminal (capsys is not one)
    assert "stdin and stdout are not both terminals" in capsys.readouterr().err
    assert cli._view_for("web") == "web"  # a browser needs no terminal
    monkeypatch.setattr(
        cli.importlib.util, "find_spec", lambda name: None if name == "textual_serve" else object()
    )
    assert cli._view_for("web") == "plain"
    assert "--ui web: the optional textual-serve package is not installed; using --ui plain" in (
        capsys.readouterr().err
    )
    monkeypatch.setattr(cli.importlib.util, "find_spec", lambda name: object())
    assert cli._view_for("json") == "json"
    assert cli._view_for("plain") == "plain"
    assert capsys.readouterr().err == ""


def test_the_terminal_views_need_textual_for_the_dashboard_and_plotext_for_the_plot(
    monkeypatch, capsys
):
    monkeypatch.setattr(sys, "stdout", Tty())
    monkeypatch.setattr(sys, "stdin", Tty())
    available = {"textual", "textual_plotext"}
    monkeypatch.setattr(
        cli.importlib.util, "find_spec", lambda name: object() if name in available else None
    )
    assert cli._view_for("plot") == "plot"
    assert cli._view_for("dashboard") == "dashboard"
    available.discard("textual_plotext")
    assert cli._view_for("plot") == "plain"
    assert "--ui plot: the optional textual-plotext package is not installed" in (
        capsys.readouterr().err
    )
    assert cli._view_for("dashboard") == "dashboard"  # it needs no plotting package


def test_scan_run_has_a_plot_option_checked_against_the_scan(work, capsys):
    scan = write_scan(work)
    code = cli.main(["scan", "run", str(scan), "--ui", "plain", "--plot", "ion.nope,mean"])
    assert code == 2
    assert "--plot: x 'ion.nope' is not a scanned parameter" in capsys.readouterr().err
    assert not (work / "scan-").exists()


class FakeApp:
    """Stands in for the Textual app: it takes the event queue and drains it, as the real
    app does, until the scan thread's ``None``. It records what it was given."""

    last = None

    def __init__(self, scan, events, **kwargs):
        self.events = events
        self.kwargs = kwargs
        self.kinds = []
        FakeApp.last = self

    def run(self):
        while True:
            event = self.events.get(timeout=GUARD_SECONDS)
            if event is None:
                return
            self.kinds.append(event.kind)


def test_a_terminal_view_runs_the_scan_in_a_thread_and_feeds_the_app(work, monkeypatch, capsys):
    pytest.importorskip("textual")
    monkeypatch.setattr(cli, "_view_for", lambda ui: "dashboard")
    monkeypatch.setattr("srim.ui.tui.app.ScanApp", FakeApp)
    out = work / "out"
    code = cli.main(
        ["scan", "run", str(write_scan(work)), "-o", str(out), "--plot", "ion.energy_keV,mean"]
    )
    assert code == 0
    app = FakeApp.last
    assert app.kinds[0] == "scan_started" and app.kinds[-1] == "scan_finished"
    assert app.kwargs["view"] == "dashboard" and app.kwargs["plot"] == "ion.energy_keV,mean"
    assert app.kwargs["wait"] is True  # the app stays open after the scan until q
    assert f"result: {out / 'result' / 'scan.csv'}" in capsys.readouterr().out
    assert (out / "result" / "scan.csv").is_file()


def test_an_app_that_fails_stops_the_scan_and_the_error_is_reported(work, monkeypatch, capsys):
    pytest.importorskip("textual")

    class Broken(FakeApp):
        def run(self):
            raise RuntimeError("the screen broke")

    monkeypatch.setattr(cli, "_view_for", lambda ui: "plot")
    monkeypatch.setattr("srim.ui.tui.app.ScanApp", Broken)
    out = work / "out"
    code = cli.main(["scan", "run", str(write_scan(work)), "-o", str(out)])
    assert code == 1
    assert "scan failed: the screen broke" in capsys.readouterr().err
    assert (out / "result" / "scan.csv").is_file()  # stopped, with its partial result


def test_output_that_is_not_a_scan_folder_exits_2_before_running(work, capsys):
    out = work / "out"
    out.mkdir()
    (out / "notes.txt").write_text("mine", encoding="utf-8")
    code = cli.main(["scan", "run", str(write_scan(work)), "--ui", "plain", "-o", str(out)])
    captured = capsys.readouterr()
    assert code == 2
    assert "is not a scan folder" in captured.err
    assert captured.out == ""
    assert (out / "notes.txt").read_text(encoding="utf-8") == "mine"
    assert not (out / "result").exists()


def test_an_existing_scan_folder_is_reused_and_said_so(work, capsys):
    scan = write_scan(work)
    out = work / "out"
    assert cli.main(["scan", "run", str(scan), "--ui", "plain", "-o", str(out)]) == 0
    capsys.readouterr()
    assert cli.main(["scan", "run", str(scan), "--ui", "plain", "-o", str(out)]) == 0
    assert "reusing the scan folder" in capsys.readouterr().err


def test_bad_input_exits_2(work, capsys):
    assert cli.main(["scan", "run", str(work / "missing.yaml"), "-o", str(work / "o")]) == 2
    assert "missing.yaml" in capsys.readouterr().err
    assert cli.main(["scan", "run", "--set", "number_ions=5"]) == 2
    assert "no scan.yaml given" in capsys.readouterr().err
    code = cli.main(["scan", "run", str(write_scan(work)), "--set", "ion.energy_keV=abc"])
    assert code == 2
    assert "must be a number" in capsys.readouterr().err


def test_set_before_the_scan_path_in_scan_run_and_prepare(work):
    scan = write_scan(work)
    out = work / "out"
    code = cli.main(["scan", "run", "--set", "number_ions=12", "ion.angle_deg=5", str(scan),
                     "--ui", "plain", "-o", str(out)])  # fmt: skip
    assert code == 0
    recorded = Scan.from_yaml(out / "scan.yaml")
    assert recorded.base.number_ions == 12 and recorded.base.ion.angle_deg == 5.0
    assert recorded.ions == 12  # the ion count followed the base spec's number_ions

    folder = work / "slurm"
    assert (
        cli.main(["scan", "prepare", "--set", "number_ions=12", str(scan), "-o", str(folder)]) == 0
    )
    assert Scan.from_yaml(folder / "scan.yaml").ions == 12


def test_prepare_then_collect_round_trip(work, monkeypatch, capsys):
    scan = write_scan(work)
    folder = work / "slurm"
    assert cli.main(["scan", "prepare", str(scan), "-o", str(folder)]) == 0
    assert f"cd {folder} && sbatch submit.sbatch" in capsys.readouterr().out
    assert cli.main(["scan", "prepare", str(scan), "-o", str(folder)]) == 2  # already prepared
    capsys.readouterr()

    # the array tasks: each job folder run here with the fake SRIM (one point fails)
    monkeypatch.setenv("PYSRIM2_TEST_FAIL_ENERGY", "13.0")
    import fake_srim_for_scan_cli

    for job in sorted((folder / "jobs").iterdir()):
        run_job(job, runner=fake_srim_for_scan_cli.run)
    assert cli.main(["scan", "collect", str(folder)]) == 1
    out = capsys.readouterr().out
    assert "collected 2 point(s): done 1, failed 1" in out
    assert f"result: {folder / 'result' / 'scan.csv'}" in out
    assert (folder / "result" / "scan.csv").is_file()


def test_collect_of_a_folder_without_scan_exits_2(work, capsys):
    (work / "empty").mkdir()
    assert cli.main(["scan", "collect", str(work / "empty")]) == 2
    assert "prepare the scan first" in capsys.readouterr().err


def test_clean_removes_the_cache_entries_only(work, capsys):
    out = work / "out"
    assert cli.main(["scan", "run", str(write_scan(work)), "--ui", "plain", "-o", str(out)]) == 0
    entries = list((out / "cache").glob("[0-9a-f][0-9a-f]/*"))
    assert entries
    capsys.readouterr()

    assert cli.main(["scan", "clean", str(out), "--dry-run"]) == 0
    assert f"would remove {len(entries)} cache entries" in capsys.readouterr().out
    assert all(entry.is_dir() for entry in entries)

    assert cli.main(["scan", "clean", str(out)]) == 0
    assert f"removed {len(entries)} cache entries" in capsys.readouterr().out
    assert not any(entry.exists() for entry in entries)
    assert (out / "cache" / "CACHEDIR.TAG").is_file()
    assert (out / "result" / "scan.csv").is_file() and (out / "scan.yaml").is_file()


def test_clean_all_refuses_a_folder_without_scan_yaml(work, capsys):
    folder = work / "plain"
    folder.mkdir()
    (folder / "x.txt").write_text("keep", encoding="utf-8")
    assert cli.main(["scan", "clean", str(folder), "--all"]) == 2
    assert "is not a scan folder" in capsys.readouterr().err
    assert (folder / "x.txt").exists()


def test_clean_all_removes_a_whole_scan_folder(work, capsys):
    out = work / "out"
    assert cli.main(["scan", "run", str(write_scan(work)), "--ui", "plain", "-o", str(out)]) == 0
    assert cli.main(["scan", "clean", str(out), "--all", "--dry-run"]) == 0
    assert out.exists()
    assert cli.main(["scan", "clean", str(out), "--all"]) == 0
    assert not out.exists()


def test_clean_of_a_shared_cache_and_its_argument_rules(work, capsys):
    shared = work / "shared"
    scan = write_scan(work)
    assert cli.main(["scan", "run", str(scan), "--ui", "plain", "-o", str(work / "a"),
                     "--cache", str(shared)]) == 0  # fmt: skip
    assert (shared / "CACHEDIR.TAG").is_file()
    assert cli.main(["scan", "clean", "--cache", str(shared)]) == 0
    assert "removed" in capsys.readouterr().out

    not_a_cache = work / "notcache"
    not_a_cache.mkdir()
    assert cli.main(["scan", "clean", "--cache", str(not_a_cache)]) == 2
    assert not_a_cache.exists()
    assert cli.main(["scan", "clean"]) == 2
    assert cli.main(["scan", "clean", str(work), "--cache", str(shared)]) == 2
    assert cli.main(["scan", "clean", "--cache", str(shared), "--all"]) == 2
    capsys.readouterr()


def scan_folder(work: Path, name: str = "out") -> Path:
    """A finished scan folder (it has the marker) under ``work``."""
    out = work / name
    assert cli.main(["scan", "run", str(write_scan(work)), "--ui", "plain", "-o", str(out)]) == 0
    return out


def test_clean_all_needs_the_marker_not_just_scan_yaml(work, capsys):
    folder = work / "project"
    folder.mkdir()
    (folder / "scan.yaml").write_text("not pysrim2's\n", encoding="utf-8")
    (folder / "data.csv").write_text("precious\n", encoding="utf-8")
    assert cli.main(["scan", "clean", str(folder), "--all"]) == 2
    assert "no .pysrim2-scan marker" in capsys.readouterr().err
    assert (folder / "data.csv").exists() and (folder / "scan.yaml").exists()


def test_clean_all_refuses_a_symbolic_link_and_keeps_the_target(work, capsys):
    out = scan_folder(work)
    link = work / "link"
    link.symlink_to(out, target_is_directory=True)
    assert cli.main(["scan", "clean", str(link), "--all"]) == 1
    assert "is a symbolic link" in capsys.readouterr().err
    assert (out / "scan.yaml").exists()


def test_clean_all_refuses_the_working_directory_and_its_parents(work, monkeypatch, capsys):
    out = scan_folder(work)
    inner = out / "inner"
    inner.mkdir()
    monkeypatch.chdir(out)
    assert cli.main(["scan", "clean", str(out), "--all"]) == 1
    assert "holds the working directory" in capsys.readouterr().err
    monkeypatch.chdir(inner)  # the folder is above the working directory
    assert cli.main(["scan", "clean", str(out), "--all"]) == 1
    assert (out / "scan.yaml").exists()


def test_clean_all_refuses_the_home_folder(work, monkeypatch, capsys):
    home = work / "home"
    home.mkdir()
    (home / ".pysrim2-scan").write_text("pysrim2 scan folder\n", encoding="utf-8")
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("USERPROFILE", str(home))  # what Path.home() reads on Windows
    monkeypatch.chdir(work)
    assert cli.main(["scan", "clean", str(home), "--all"]) == 1
    assert "is the home folder" in capsys.readouterr().err
    assert home.exists()


def test_clean_all_reports_a_removal_that_fails(work, monkeypatch, capsys):
    out = scan_folder(work)

    def refuse(path, **kwargs):
        raise PermissionError("denied")

    monkeypatch.setattr(cli.shutil, "rmtree", refuse)
    assert cli.main(["scan", "clean", str(out), "--all"]) == 1
    assert "could not remove it: denied" in capsys.readouterr().err


def test_clean_is_refused_while_a_scan_holds_the_folder(work, capsys):
    from srim.scan.executor import scan_lock

    out = scan_folder(work)
    held = scan_lock(out).acquire()
    try:
        assert cli.main(["scan", "clean", str(out), "--all"]) == 1
        assert "a scan is running" in capsys.readouterr().err
        assert cli.main(["scan", "clean", str(out)]) == 1
        assert cli.main(["scan", "clean", str(out), "--all", "--dry-run"]) == 0
    finally:
        held.release()
    assert (out / "scan.yaml").exists()
    assert cli.main(["scan", "clean", str(out), "--all"]) == 0
    assert not out.exists()


def test_scan_run_in_a_folder_that_a_scan_holds_exits_2(work, capsys):
    from srim.scan.executor import scan_lock

    out = work / "busy"
    out.mkdir()
    held = scan_lock(out).acquire()
    try:
        code = cli.main(["scan", "run", str(write_scan(work)), "--ui", "plain", "-o", str(out)])
    finally:
        held.release()
    assert code == 2
    assert "a scan is already running in" in capsys.readouterr().err
    assert not (out / "scan.yaml").exists()


def test_json_stdout_holds_only_events_even_when_the_runner_prints(work, monkeypatch, capfd):
    # prints from the runner, and from a child it starts, go to stderr
    monkeypatch.setenv("PYSRIM2_TEST_NOISE", "1")
    out = work / "out"
    code = cli.main(["scan", "run", str(write_scan(work)), "--ui", "json", "-o", str(out)])
    captured = capfd.readouterr()
    assert code == 0
    rows = json_rows(captured.out)
    assert rows[0]["kind"] == "scan_started" and rows[-1]["kind"] == "scan_finished"
    assert "noise from the runner" in captured.err
    assert "noise from a child" in captured.err


def test_ctrl_c_during_a_scan_exits_130_with_the_partial_result(work, monkeypatch, capsys):
    if sys.platform == "win32":
        pytest.skip("the SIGINT test uses POSIX signals")
    started = work / "started"
    monkeypatch.setenv("PYSRIM2_TEST_SLEEP", "60")  # the units run until they are stopped
    monkeypatch.setenv("PYSRIM2_TEST_STARTED", str(started))

    def press_ctrl_c_once_a_unit_runs():
        deadline = time.monotonic() + 60
        while not started.exists() and time.monotonic() < deadline:
            time.sleep(0.05)
        os.kill(os.getpid(), signal.SIGINT)

    presser = threading.Thread(target=press_ctrl_c_once_a_unit_runs, daemon=True)
    presser.start()
    out = work / "out"
    code = cli.main(["scan", "run", str(write_scan(work)), "--workers", "2", "--ui", "plain",
                     "-o", str(out)])  # fmt: skip
    presser.join(timeout=5)
    assert code == 130
    assert (out / "result" / "scan.csv").is_file()
    assert "stopped: the partial result above is kept" in capsys.readouterr().err


def test_an_error_before_the_scan_starts_exits_2(work, monkeypatch, capsys):
    import srim.scan

    def refuse(scan, **kwargs):
        raise ValueError("workers must be a whole number >= 1, got 0")

    monkeypatch.setattr(srim.scan, "run_scan", refuse)
    assert cli.main(["scan", "run", str(write_scan(work)), "-o", str(work / "o")]) == 2
    assert "scan did not start: workers must be" in capsys.readouterr().err


def test_an_error_after_the_scan_starts_exits_1(work, monkeypatch, capsys):
    import srim.scan
    from srim.scan.events import ScanStarted

    def fail_midway(scan, *, on_event, **kwargs):
        on_event(ScanStarted(2, 1, 7, "x"))
        raise ValueError("the result table is broken")

    monkeypatch.setattr(srim.scan, "run_scan", fail_midway)
    assert cli.main(["scan", "run", str(write_scan(work)), "-o", str(work / "o")]) == 1
    assert "scan failed: the result table is broken" in capsys.readouterr().err


def test_reusing_a_folder_warns_only_when_its_scan_differs(work, capsys):
    scan = write_scan(work)
    out = work / "out"
    assert cli.main(["scan", "run", str(scan), "--ui", "plain", "-o", str(out)]) == 0
    capsys.readouterr()
    assert cli.main(["scan", "run", str(scan), "--ui", "plain", "-o", str(out)]) == 0
    assert "describes another scan" not in capsys.readouterr().err
    other = work / "other.yaml"
    Scan(BASE, grid={"ion.energy_keV": [10.0]}, seed=7).to_yaml(other)
    assert cli.main(["scan", "run", str(other), "--ui", "plain", "-o", str(out)]) == 0
    assert "describes another scan" in capsys.readouterr().err


def test_sigterm_in_a_terminal_view_stops_the_scan_and_restores_the_handler(monkeypatch):
    if sys.platform == "win32":
        pytest.skip("os.kill(SIGTERM) ends the whole process on Windows")
    pytest.importorskip("textual")
    before = signal.getsignal(signal.SIGTERM)
    stop, pause, events = threading.Event(), threading.Event(), queue.Queue()

    class TerminatedApp(FakeApp):
        def run(self):
            os.kill(os.getpid(), signal.SIGTERM)  # what a service manager sends
            assert stop.wait(GUARD_SECONDS), "SIGTERM did not set the stop event"
            super().run()

    monkeypatch.setattr("srim.ui.tui.app.ScanApp", TerminatedApp)

    def call():
        assert stop.wait(GUARD_SECONDS)  # the scan sees the stop and returns its result
        return "partial"

    result = cli._run_in_app(call, events, None, view="plot", plot=None, stop=stop, pause=pause)
    assert result == "partial" and stop.is_set()
    assert signal.getsignal(signal.SIGTERM) == before  # restored after the app


def test_the_terminal_view_does_not_wait_forever_for_a_scan_that_will_not_stop(monkeypatch):
    pytest.importorskip("textual")
    monkeypatch.setattr(cli, "TERMINAL_JOIN_SECONDS", 0.3)

    class QuitNow(FakeApp):
        def run(self):  # the user quit at once (ctrl+c twice); the scan has not stopped
            return

    monkeypatch.setattr("srim.ui.tui.app.ScanApp", QuitNow)
    release = threading.Event()
    events = queue.Queue()

    def call():
        release.wait(GUARD_SECONDS)  # ignores the stop
        return "late"

    stop = threading.Event()
    try:
        result = cli._run_in_app(call, events, None, view="plot", plot=None, stop=stop,
                                 pause=threading.Event())  # fmt: skip
    finally:
        release.set()
    assert result is None and stop.is_set()


def test_quitting_before_the_scan_stopped_exits_130_with_the_partial_result(
    work, monkeypatch, capsys
):
    monkeypatch.setattr(cli, "_view_for", lambda ui: "plot")
    monkeypatch.setattr(cli, "_run_in_app", lambda *args, **kwargs: None)
    out = work / "out"
    code = cli.main(["scan", "run", str(write_scan(work)), "-o", str(out)])
    assert code == 130
    assert "stopped: the partial result above is kept" in capsys.readouterr().err


class FakeServerProcess:
    """The server process of --ui web, without a server: a group signal to its pid is recorded
    (``terminated``) and makes it exit; the real group is never signalled. The stop is the POSIX
    one on every platform (the fake has no process tree for taskkill, and the pid is not real)."""

    def __init__(self, monkeypatch=None):
        self.pid = 4242
        self.terminated = False
        self.exited = False
        if monkeypatch is not None:
            monkeypatch.setattr(web, "_signal_group", lambda server, sig: self.signal_group(sig))
            monkeypatch.setattr(web, "IS_WINDOWS", False)

    def signal_group(self, sig):
        self.terminated = self.terminated or sig == signal.SIGTERM
        self.exited = True

    def poll(self):
        return 0 if self.exited else None

    def wait(self, timeout=None):
        return 0


def test_web_run_prints_the_url_writes_the_events_and_stops_the_server(work, monkeypatch, capsys):
    import srim.ui.web as web

    started, server = [], FakeServerProcess(monkeypatch)
    monkeypatch.setattr(web, "start_server", lambda argv: started.append(argv) or server)
    monkeypatch.setattr(web, "GRACE_SECONDS", 0.0)
    port = free_port()
    out = work / "out"
    code = cli.main(["scan", "run", str(write_scan(work)), "--workers", "2", "--ui", "web",
                     "--port", str(port), "-o", str(out), "--no-wait"])  # fmt: skip
    captured = capsys.readouterr()
    assert code == 0
    assert f"web view: http://127.0.0.1:{port}" in captured.out
    lines = captured.out.splitlines()
    assert lines[0].startswith("web view: ")  # the URL comes before the status lines
    assert any(line.startswith("done 0/2") for line in lines)
    kinds = [json.loads(line)["kind"] for line in (out / "events.jsonl").read_text().splitlines()]
    assert kinds[0] == "run" and kinds[1] == "scan_started" and kinds[-1] == "scan_finished"
    argv = started[0]
    assert argv[argv.index("--port") + 1] == str(port)
    assert argv[argv.index("--host") + 1] == "127.0.0.1"
    assert server.terminated


def test_web_run_on_a_busy_port_goes_on_as_plain(work, monkeypatch, capsys):
    import srim.ui.web as web

    started = []
    monkeypatch.setattr(web, "start_server", lambda argv: started.append(argv))
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as busy:
        busy.bind(("127.0.0.1", 0))
        busy.listen(1)
        port = busy.getsockname()[1]
        out = work / "out"
        code = cli.main(["scan", "run", str(write_scan(work)), "--ui", "web", "--port", str(port),
                         "-o", str(out)])  # fmt: skip
    captured = capsys.readouterr()
    assert code == 0
    assert f"--ui web: cannot listen on 127.0.0.1:{port}" in captured.err
    assert "; using --ui plain" in captured.err
    assert started == [] and not (out / "events.jsonl").exists()


def test_a_stop_request_in_the_control_file_stops_a_web_run(work, monkeypatch, capsys):
    import srim.ui.web as web

    started = work / "started"
    monkeypatch.setenv("PYSRIM2_TEST_SLEEP", "60")  # the units run until they are stopped
    monkeypatch.setenv("PYSRIM2_TEST_STARTED", str(started))
    monkeypatch.setattr(web, "GRACE_SECONDS", 0.0)
    out = work / "out"

    def viewer_sends_stop_once_a_unit_runs(argv):
        def send():
            deadline = time.monotonic() + 60
            while not started.exists() and time.monotonic() < deadline:
                time.sleep(0.05)
            with (out / web.CONTROL_FILE).open("a", encoding="utf-8") as file:
                file.write("stop\n")

        threading.Thread(target=send, daemon=True).start()
        return FakeServerProcess(monkeypatch)

    monkeypatch.setattr(web, "start_server", viewer_sends_stop_once_a_unit_runs)
    code = cli.main(["scan", "run", str(write_scan(work)), "--workers", "2", "--ui", "web",
                     "--port", str(free_port()), "-o", str(out)])  # fmt: skip
    assert code == 130
    assert "stopped: the partial result above is kept" in capsys.readouterr().err
    events = [json.loads(line) for line in (out / "events.jsonl").read_text().splitlines()]
    assert events[-1]["kind"] == "scan_finished" and events[-1]["stopped"] is True


def free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
        probe.bind(("127.0.0.1", 0))
        return probe.getsockname()[1]


def test_a_web_run_turns_sigterm_into_a_stop_and_restores_the_handler(work, monkeypatch, capsys):
    import srim.ui.web as web

    calls = []
    real = cli._stop_on_sigterm

    def recording(stop):
        restore = real(stop)
        calls.append(stop)
        return restore

    monkeypatch.setattr(cli, "_stop_on_sigterm", recording)
    monkeypatch.setattr(web, "start_server", lambda argv: FakeServerProcess(monkeypatch))
    monkeypatch.setattr(web, "GRACE_SECONDS", 0.0)
    before = signal.getsignal(signal.SIGTERM)
    code = cli.main(["scan", "run", str(write_scan(work)), "--ui", "web", "--port",
                     str(free_port()), "-o", str(work / "out"), "--no-wait"])  # fmt: skip
    assert code == 0 and len(calls) == 1
    assert signal.getsignal(signal.SIGTERM) is before  # the previous handler is back


def test_no_wait_closes_a_terminal_view_when_the_scan_ends(work, monkeypatch, capsys):
    pytest.importorskip("textual")
    monkeypatch.setattr(cli, "_view_for", lambda ui: "plot")
    monkeypatch.setattr("srim.ui.tui.app.ScanApp", FakeApp)
    out = work / "out"
    code = cli.main(["scan", "run", str(write_scan(work)), "-o", str(out), "--no-wait"])
    assert code == 0
    assert FakeApp.last.kwargs["wait"] is False


@pytest.mark.parametrize("ui", ["plain", "json"])
def test_no_wait_is_accepted_and_ignored_by_plain_and_json(work, ui, capsys):
    out = work / "out"
    code = cli.main(["scan", "run", str(write_scan(work)), "--ui", ui, "--no-wait", "-o", str(out)])
    assert code == 0 and (out / "result" / "scan.csv").is_file()


def hold_until_ctrl_c(holds):
    """Replaces the wait of the web view after a finished scan: the Ctrl-C comes at once."""

    def hold(self):
        holds.append(self.url)
        raise KeyboardInterrupt

    return hold


def test_web_run_waits_for_ctrl_c_after_the_scan_and_keeps_the_scans_exit_code(
    work, monkeypatch, capsys
):
    import srim.ui.web as web

    server, holds = FakeServerProcess(monkeypatch), []
    monkeypatch.setattr(web, "start_server", lambda argv: server)
    monkeypatch.setattr(web.WebView, "_hold", hold_until_ctrl_c(holds))
    port = free_port()
    code = cli.main(["scan", "run", str(write_scan(work)), "--ui", "web", "--port", str(port),
                     "-o", str(work / "out")])  # fmt: skip
    captured = capsys.readouterr()
    assert code == 0  # Ctrl-C after a finished scan is not a stop: no 130
    assert holds == [f"http://127.0.0.1:{port}"]
    assert (
        f"scan finished — the view stays at http://127.0.0.1:{port}; press Ctrl-C to stop it"
        in (captured.err)
    )
    assert server.terminated


def test_web_run_with_no_wait_closes_the_view_after_the_grace_period(work, monkeypatch, capsys):
    import srim.ui.web as web

    def no_waiting(self):
        raise AssertionError("--no-wait must not wait for Ctrl-C")

    monkeypatch.setattr(web, "start_server", lambda argv: FakeServerProcess(monkeypatch))
    monkeypatch.setattr(web.WebView, "_hold", no_waiting)
    monkeypatch.setattr(web, "GRACE_SECONDS", 0.0)
    code = cli.main(["scan", "run", str(write_scan(work)), "--ui", "web", "--port",
                     str(free_port()), "-o", str(work / "out"), "--no-wait"])  # fmt: skip
    assert code == 0


def test_a_rerun_without_a_seed_reuses_the_seed_quietly(work, capsys):
    scan = work / "scan.yaml"
    Scan(BASE, grid={"ion.energy_keV": [10.0, 13.0]}).to_yaml(scan)
    out = work / "out"
    assert cli.main(["scan", "run", str(scan), "--ui", "plain", "-o", str(out)]) == 0
    capsys.readouterr()
    assert cli.main(["scan", "run", str(scan), "--ui", "plain", "-o", str(out)]) == 0
    err = capsys.readouterr().err
    assert "describes another scan" not in err


def test_set_keeps_the_seed_of_a_keep_seed_scan(work, capsys):
    """--set rebuilds the scan; a keep_seed scan must still run its point with its seed."""
    scan = work / "scan.yaml"
    Scan(BASE, seed=4711, keep_seed=True).to_yaml(scan)
    out = work / "out"
    code = cli.main(
        ["scan", "run", "--set", "ion.energy_keV=15", str(scan), "--ui", "plain", "-o", str(out)]
    )
    assert code == 0
    recorded = Scan.from_yaml(out / "scan.yaml")
    assert recorded.keep_seed is True
    assert recorded.base.ion.energy_keV == 15.0
    assert recorded.seed == 4711
    assert (out / "result" / "scan.csv").read_text(encoding="utf-8").count(",4711,") == 1
