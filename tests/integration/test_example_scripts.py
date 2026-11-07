"""Run the scripts in `examples/scripts` with tiny inputs so they cannot rot (`pytest -m srim`)."""

import importlib.util
import subprocess
import sys
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parents[2] / "examples" / "scripts"


def load(name):
    spec = importlib.util.spec_from_file_location(name, SCRIPTS / f"{name}.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_stopping_table(tmp_path):
    table = load("stopping_table").run(tmp_path / "output")
    assert list(table.columns)[:2] == ["energy_keV", "electronic_stopping"]
    assert len(table) > 5
    assert (table["electronic_stopping"] > 0).all()
    assert (tmp_path / "output" / "stopping_table.csv").is_file()


def test_multilayer_damage(tmp_path):
    profile = load("multilayer_damage").run(2, tmp_path / "output")
    assert {"depth_A", "total", "vacancies_Au_per_A_per_ion"} <= set(profile.columns)
    assert profile["total"].sum() > 0
    assert (tmp_path / "output" / "vacancy_profile.csv").is_file()


def test_angle_energy_scan(tmp_path):
    result = load("angle_energy_scan").run(tmp_path / "scan", workers=2)
    assert len(result.table) == 4
    assert set(result.table["status"]) == {"done"}
    assert (result.table["n_ions_stopped"] > 0).all()
    assert (result.table["mean_depth_A"] > 0).all()
    assert (tmp_path / "scan" / "result" / "scan.csv").is_file()


def test_beam_energy_spread(tmp_path):
    table = load("beam_energy_spread").run(tmp_path / "output")
    assert list(table["mode"]) == ["exact", "binned"]
    assert (table["n_ions"] > 0.9 * 200).all()
    assert (table["mean_depth_A"] > 0).all()
    assert (table["straggle_depth_A"] > 0).all()
    # the same beam both ways. With 200 ions the mean depth changes by several per cent from seed
    # to seed, so the check is loose: it catches a units or energy mistake, not the statistics.
    exact, binned = table["mean_depth_A"]
    assert abs(exact - binned) < 0.25 * exact
    assert (tmp_path / "output" / "beam_energy_spread.csv").is_file()


def test_scan_yaml_from_the_command_line(tmp_path):
    folder = tmp_path / "scan"
    completed = subprocess.run(
        [
            sys.executable,
            "-m",
            "srim",
            "scan",
            "run",
            str(SCRIPTS / "scan.yaml"),
            "--ui",
            "plain",
            "--workers",
            "2",
            "--output",
            str(folder),
        ],
        capture_output=True,
        text=True,
        timeout=900,
        check=False,
    )
    assert completed.returncode == 0, completed.stderr
    assert (folder / "result" / "scan.csv").is_file()
