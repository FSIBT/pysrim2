"""pysrim2 scan: run, prepare, collect, clean, the views and the exit codes.

SRIM is replaced by a runner module written into the test folder: it returns the outputs of
test_files/1 (a real SRIM result), and fails for the energy in ``PYSRIM2_TEST_FAIL_ENERGY``.
``PYSRIM2_SCAN_RUNNER`` names it for the command line (the worker processes import it too).
Every wait has a timeout, and a SIGALRM guard (POSIX) fails a test that hangs.
"""

import io
import json
import signal
import sys
import threading
from pathlib import Path

import pytest

import srim.scan
from srim import cli
from srim.scan import Scan
from srim.scan.backends import run_job
from srim.spec import RunSpec

FIXTURE = Path(__file__).resolve().parent.parent / "test_files" / "1"
GUARD_SECONDS = 120
RUNNER_MODULE = "fake_srim_for_scan_cli"

RUNNER_SOURCE = f'''"""Stands in for SRIM in the scan command tests."""

import os
from pathlib import Path

from srim.output import Results

FIXTURE = Path({str(FIXTURE)!r})


def run(spec):
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
    monkeypatch.setenv(cli.SCAN_RUNNER_VAR, f"{RUNNER_MODULE}:run")
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
    assert capsys.readouterr().err == ""


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
        stop = threading.Event()
        stop.set()  # what Ctrl-C does, at once
        return real(scan, stop_event=stop, **kwargs)

    monkeypatch.setattr(srim.scan, "run_scan", stop_before_start)
    out = work / "out"
    code = cli.main(["scan", "run", str(write_scan(work)), "--ui", "json", "-o", str(out)])
    captured = capsys.readouterr()
    assert code == 130
    rows = json_rows(captured.out)
    assert rows[-1]["kind"] == "scan_finished" and rows[-1]["stopped"] is True
    assert (out / "result" / "scan.csv").is_file()
    assert "stopped by Ctrl-C" in captured.err


def test_graphical_ui_falls_back_to_plain_with_the_reason(monkeypatch, capsys):
    class Tty(io.StringIO):
        def isatty(self):
            return True

    monkeypatch.setattr(cli.importlib.util, "find_spec", lambda name: None)
    assert cli._view_for("plot") == "plain"
    assert "--ui plot: the optional textual package is not installed; using --ui plain" in (
        capsys.readouterr().err
    )
    monkeypatch.setattr(cli.importlib.util, "find_spec", lambda name: object())
    assert cli._view_for("web") == "plain"  # capsys is not a terminal
    assert "stdout is not a terminal" in capsys.readouterr().err
    monkeypatch.setattr(sys, "stdout", Tty())
    assert cli._view_for("dashboard") == "plain"
    assert "--ui dashboard: not available yet; using --ui plain" in capsys.readouterr().err
    assert cli._view_for("json") == "json"
    assert cli._view_for("plain") == "plain"
    assert capsys.readouterr().err == ""


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
