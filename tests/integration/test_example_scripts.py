"""Run the scripts in ``examples/scripts`` with tiny inputs so they cannot rot (``pytest -m srim``)."""

import importlib.util
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
    assert {"depth_A", "total", "vacancies_Au"} <= set(profile.columns)
    assert profile["total"].sum() > 0
    assert (tmp_path / "output" / "vacancy_profile.csv").is_file()
