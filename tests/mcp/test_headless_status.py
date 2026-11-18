"""Headless status, install planning and the export bookkeeping: no real checks, installs or SRIM.

The checks, the installer steps and the default components are injected, so nothing probes the
machine, runs wine or installs anything.
"""

import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest
from test_results import run_folder

from srim.installer.steps import Step
from srim.mcp.backend import BackendError
from srim.mcp.headless import HeadlessBackend
from srim.output.tables import Results


def check(name, ok, detail="", fix="run: pysrim2 install srim"):
    return {"name": name, "ok": ok, "detail": detail, "fix": "" if ok else fix}


def fake_checks(*checks):
    return lambda: list(checks)


def step(name, *, done=False, admin=False, commands=("do it",), calls=None, error=None):
    """A fake installer step: ``calls`` collects the names of the steps that ran."""

    def run():
        if calls is not None:
            calls.append(name)
        if error is not None:
            raise error

    return Step(
        name=name,
        summary=f"{name} summary",
        check=lambda: done,
        commands=lambda: list(commands),
        run=run,
        needs_admin=admin,
    )


def fake_steps(table, calls=None):
    """A ``plan_steps`` that maps component names to the fake steps in ``table``."""

    def plan_steps(components):
        steps = []
        for component in components:
            if component not in table:
                raise BackendError(f"component {component} is not available yet")
            steps.extend(table[component])
        return steps

    return plan_steps


def n15_backend(tmp_path, **kwargs):
    backend = HeadlessBackend(tmp_path, **kwargs)
    backend.set_section("ion", {"symbol": "N", "mass_amu": 15.0001, "energy_keV": 40})
    backend.set_section(
        "target",
        [
            {
                "thickness_nm": 1000,
                "density_g_cm3": 2.33,
                "elements": [{"symbol": "Si", "stoich": 1}],
            }
        ],
    )
    return backend


# --- status -------------------------------------------------------------------------------


def test_status_gives_the_checks_and_the_first_problem(tmp_path):
    backend = HeadlessBackend(
        tmp_path,
        checks=fake_checks(check("pysrim2", True), check("trim-exe", False, "C:/srim/TRIM.exe")),
    )
    status = backend.status()
    assert status["mode"] == "headless"
    assert status["srim_ready"] is False
    assert status["srim_problem"] == "trim-exe: C:/srim/TRIM.exe. Fix: run: pysrim2 install srim"
    assert [c["name"] for c in status["checks"]] == ["pysrim2", "trim-exe"]
    assert status["setup"] is None
    assert status["running"] is None


def test_a_stale_skill_or_a_big_run_folder_does_not_block_a_run(tmp_path):
    backend = HeadlessBackend(
        tmp_path,
        checks=fake_checks(
            check("pysrim2", True),
            check("srim-dir", True),
            check("trim-exe", True),
            check("agent-skill", False, "out of date", "run: pysrim2 install skill"),
            check("run-directories", False, "10 GB", "run: pysrim2 clean runs"),
        ),
    )
    status = backend.status()
    assert status["srim_ready"] is True
    assert status["srim_problem"] is None


def test_status_names_the_setup_file_once_it_has_one(tmp_path):
    backend = n15_backend(tmp_path, checks=fake_checks(check("trim-exe", True)))
    backend.save_setup("n15.yaml")
    assert backend.status()["setup"] == str(tmp_path / "n15.yaml")


# --- install planning -----------------------------------------------------------------------


def test_install_plan_lists_each_step_with_its_commands(tmp_path):
    table = {
        "srim": [step("fetch", commands=("download the zip",))],
        "wine": [step("wine-packages", admin=True, commands=("sudo apt install wine",))],
    }
    backend = HeadlessBackend(tmp_path, plan_steps=fake_steps(table))
    rows = backend.install_plan(["srim", "wine"])
    assert rows == [
        {
            "component": "srim",
            "step": "fetch",
            "description": "fetch summary",
            "admin": False,
            "commands": ["download the zip"],
            "done": False,
        },
        {
            "component": "wine",
            "step": "wine-packages",
            "description": "wine-packages summary",
            "admin": True,
            "commands": ["sudo apt install wine"],
            "done": False,
        },
    ]


def test_install_plan_defaults_to_the_components_of_this_system(tmp_path):
    backend = HeadlessBackend(
        tmp_path,
        plan_steps=fake_steps({"skill": [step("skill")]}),
        default_components=lambda: ["skill"],
    )
    assert [row["component"] for row in backend.install_plan()] == ["skill"]


def test_an_unknown_component_is_refused_with_its_reason(tmp_path):
    with pytest.raises(BackendError, match="nonexistent"):
        HeadlessBackend(tmp_path).install_plan(["nonexistent"])


def test_components_must_be_a_list_of_names(tmp_path):
    with pytest.raises(BackendError, match="list of component names"):
        HeadlessBackend(tmp_path).install_plan("srim")


# --- install steps --------------------------------------------------------------------------


def test_install_step_runs_the_non_admin_steps_and_returns_the_admin_commands(tmp_path):
    calls = []
    table = {
        "wine": [
            step("fetch", calls=calls),
            step("wine-packages", admin=True, commands=("sudo apt install wine",), calls=calls),
        ]
    }
    backend = HeadlessBackend(tmp_path, plan_steps=fake_steps(table))
    out = backend.install_step("wine")
    assert out["component"] == "wine"
    assert out["ran"] == ["fetch"]
    assert out["commands"] == ["sudo apt install wine"]
    assert out["failed"] is None
    assert calls == ["fetch"]  # the admin step's run was never called


def test_an_admin_only_component_runs_nothing(tmp_path):
    calls = []
    table = {"vb5": [step("vb5", admin=True, commands=("regsvr32 /s X.OCX",), calls=calls)]}
    out = HeadlessBackend(tmp_path, plan_steps=fake_steps(table)).install_step("vb5")
    assert out["ran"] == []
    assert out["commands"] == ["regsvr32 /s X.OCX"]
    assert calls == []


def test_a_component_already_installed_does_nothing(tmp_path):
    calls = []
    table = {"srim": [step("srim", done=True, calls=calls)]}
    out = HeadlessBackend(tmp_path, plan_steps=fake_steps(table)).install_step("srim")
    assert out["ran"] == [] and out["commands"] == [] and calls == []
    assert any("[ok] srim" in line for line in out["log"])


def test_a_failed_step_is_reported_not_raised(tmp_path):
    table = {"srim": [step("fetch", error=OSError("disk full"))]}
    out = HeadlessBackend(tmp_path, plan_steps=fake_steps(table)).install_step("srim")
    assert out["failed"] == "fetch"
    assert any("[fail] fetch: disk full" in line for line in out["log"])


def test_install_step_writes_nothing_to_stdout(tmp_path, capsys):
    # the MCP stdio transport owns stdout: the steps' lines are returned, never printed
    table = {"srim": [step("fetch")]}
    HeadlessBackend(tmp_path, plan_steps=fake_steps(table)).install_step("srim")
    assert capsys.readouterr().out == ""


def test_install_step_refuses_an_unknown_component(tmp_path):
    with pytest.raises(BackendError, match="nonexistent"):
        HeadlessBackend(tmp_path).install_step("nonexistent")


# --- export bookkeeping ------------------------------------------------------------------------


def test_export_files_lists_what_an_export_would_write_without_writing(tmp_path):
    folder = run_folder(tmp_path)
    backend = HeadlessBackend(tmp_path)
    files = backend.export_files(str(folder), "csv", "out")
    assert str(tmp_path / "out" / "ioniz.csv") in files
    assert str(tmp_path / "out" / "metadata.yaml") in files
    assert not (tmp_path / "out").exists()


def test_the_listed_files_are_the_ones_an_export_writes(tmp_path):
    folder = run_folder(tmp_path)
    backend = HeadlessBackend(tmp_path)
    listed = backend.export_files(str(folder), "csv", "out")
    written = backend.export(str(folder), "csv", "out")["written"]
    assert sorted(listed) == sorted(written)


def test_hdf5_is_one_file_and_is_listed_as_one(tmp_path):
    pytest.importorskip("tables")
    folder = run_folder(tmp_path)
    backend = HeadlessBackend(tmp_path)
    assert backend.export_files(str(folder), "hdf5", "out.h5") == [str(tmp_path / "out.h5")]


def test_files_this_session_exported_are_not_replacements(tmp_path):
    folder = run_folder(tmp_path)
    backend = HeadlessBackend(tmp_path)
    backend.export(str(folder), "csv", "out")
    assert backend.would_replace(str(tmp_path / "out" / "ioniz.csv")) is False


def test_a_file_the_session_did_not_write_is_a_replacement(tmp_path):
    (tmp_path / "out").mkdir()
    (tmp_path / "out" / "ioniz.csv").write_text("theirs", encoding="utf-8")
    backend = HeadlessBackend(tmp_path)
    assert backend.would_replace(str(tmp_path / "out" / "ioniz.csv")) is True
    assert backend.would_replace(str(tmp_path / "out" / "other.csv")) is False


def test_a_step_that_prints_or_spawns_a_process_writes_nothing_to_fd_1(tmp_path, capfd):
    # The MCP stdio transport owns file descriptor 1: a step's own print, and the output of a
    # child process it starts (which inherits fd 1), are captured into the log instead.
    def noisy():
        print("python says hi")
        subprocess.run([sys.executable, "-c", "print('child says hi')"], check=True)

    noisy_step = Step(
        name="noisy",
        summary="noisy summary",
        check=lambda: False,
        commands=lambda: ["do it"],
        run=noisy,
    )
    backend = HeadlessBackend(tmp_path, plan_steps=fake_steps({"srim": [noisy_step]}))
    out = backend.install_step("srim")
    captured = capfd.readouterr()
    assert "says hi" not in captured.out and captured.out == ""
    assert out["ran"] == ["noisy"]
    text = "\n".join(out["log"])
    assert "python says hi" in text
    assert "child says hi" in text


# --- export writes exactly the files it lists -------------------------------------------------

ROOT = Path(__file__).resolve().parents[2]


def collision_folder(tmp_path):
    """A run folder with collision events (SiC-C's COLLISON.txt) in its point."""
    folder = run_folder(tmp_path)
    point = next((folder / "points").iterdir())
    shutil.copy(ROOT / "test_files" / "SiC-C" / "COLLISON.txt", point / "COLLISON.txt")
    return folder


def realpaths(paths):
    return {os.path.realpath(path) for path in paths}


@pytest.mark.parametrize("fmt", ["csv", "parquet", "hdf5"])
@pytest.mark.parametrize("collision", [False, True])
def test_the_listed_files_are_exactly_the_written_files(tmp_path, fmt, collision):
    if fmt == "parquet":
        pytest.importorskip("pyarrow")
    if fmt == "hdf5":
        pytest.importorskip("tables")
    folder = collision_folder(tmp_path) if collision else run_folder(tmp_path)
    backend = HeadlessBackend(tmp_path)
    dest = "out.h5" if fmt == "hdf5" else "out"
    listed = backend.export_files(str(folder), fmt, dest, collision)
    written = backend.export(str(folder), fmt, dest, collision)["written"]
    assert realpaths(listed) == realpaths(written)
    if fmt != "hdf5":  # one file per table: the collision table is listed by its name
        assert any(Path(path).stem == "collision" for path in listed) == collision


def test_export_asks_the_writer_for_exactly_the_listed_tables(tmp_path, monkeypatch):
    folder = run_folder(tmp_path)
    seen = {}

    def fake_to_csv(self, directory, spec=None, include=None, chunk_rows=100_000):
        seen["include"] = include
        return {}

    monkeypatch.setattr(Results, "to_csv", fake_to_csv)
    backend = HeadlessBackend(tmp_path)
    listed = backend.export_files(str(folder), "csv", "out")
    backend.export(str(folder), "csv", "out")
    listed_tables = sorted(Path(p).stem for p in listed if not p.endswith("metadata.yaml"))
    assert sorted(seen["include"]) == listed_tables


def test_an_export_that_writes_an_unlisted_file_is_refused(tmp_path, monkeypatch):
    folder = run_folder(tmp_path)

    def rogue_to_csv(self, directory, spec=None, include=None, chunk_rows=100_000):
        os.makedirs(directory, exist_ok=True)
        rogue = os.path.join(directory, "rogue.txt")
        with open(rogue, "w", encoding="utf-8") as handle:
            handle.write("not listed")
        return {"rogue": rogue}

    monkeypatch.setattr(Results, "to_csv", rogue_to_csv)
    with pytest.raises(BackendError, match="did not list"):
        HeadlessBackend(tmp_path).export(str(folder), "csv", "out")
