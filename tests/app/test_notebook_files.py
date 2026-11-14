"""The notebook files of the app: the starters, the reload file and the tool check (no Qt)."""

import importlib.util
import subprocess
import sys
from copy import deepcopy

import pytest
from run_folders import DONE, finished_scan

from srim.app import notebooks
from srim.app.notebooks import REINSTALL_TEXT, create_notebook, notebook_path, tool_problem
from srim.beam import beam_scan
from srim.notebook import NOTEBOOK_DIR, RELOAD_FILE, loaded_count
from srim.spec import RunSpec

BORON = {
    "version": 1,
    "ion": {"symbol": "B", "energy_keV": 20.0, "angle_deg": 7.0},
    "number_ions": 200,
    "calculation": 1,
    "seed": 12345,
    "settings": {},
    "target": [
        {
            "name": "gold",
            "thickness_nm": 100.0,
            "density_g_cm3": 19.3,
            "phase": 0,
            "elements": [
                {
                    "symbol": "Au",
                    "stoich": 1.0,
                    "displacement_eV": 25.0,
                    "lattice_eV": 3.0,
                    "surface_eV": 3.0,
                }
            ],
        }
    ],
}


def _run_folder(folder):
    """A finished run folder with one point (the fixture's real SRIM outputs)."""
    spec = RunSpec.from_dict(deepcopy(BORON))
    finished_scan(folder, beam_scan(spec), [DONE])
    return folder


@pytest.mark.parametrize("kind", ["marimo", "jupyter"])
def test_create_notebook_writes_the_starter_and_the_reload_file(tmp_path, kind):
    folder = _run_folder(tmp_path / "run")
    path = create_notebook(folder, kind)
    assert path == notebook_path(folder, kind)
    assert path.read_bytes() == notebooks.starter_path(kind).read_bytes()
    assert (folder / NOTEBOOK_DIR / RELOAD_FILE).is_file()


def test_an_existing_notebook_is_never_overwritten(tmp_path):
    folder = _run_folder(tmp_path / "run")
    notebook_path(folder, "marimo").write_text("# mine", encoding="utf-8")
    create_notebook(folder, "marimo")
    assert notebook_path(folder, "marimo").read_text(encoding="utf-8") == "# mine"


def test_the_marimo_starter_runs_against_a_run_folder(tmp_path):
    if importlib.util.find_spec("marimo") is None:
        pytest.skip("marimo is not installed (the app extra)")
    folder = _run_folder(tmp_path / "run")
    notebook = create_notebook(folder, "marimo")
    subprocess.run(
        [sys.executable, str(notebook)],
        cwd=folder,
        check=True,
        timeout=120,
        capture_output=True,
    )
    assert loaded_count(folder) == 1


def test_the_jupyter_starter_runs_against_a_run_folder(tmp_path):
    if importlib.util.find_spec("nbclient") is None or importlib.util.find_spec("nbformat") is None:
        pytest.skip("nbclient and nbformat are not installed (the dev group)")
    import nbformat
    from nbclient import NotebookClient

    folder = _run_folder(tmp_path / "run")
    notebook = create_notebook(folder, "jupyter")
    nb = nbformat.read(notebook, as_version=4)
    NotebookClient(
        nb, timeout=120, resources={"metadata": {"path": str(folder)}}
    ).execute()
    assert loaded_count(folder) == 1


def test_tool_problem_says_to_reinstall_the_app(monkeypatch):
    real_find_spec = importlib.util.find_spec

    def missing(name, *args, **kwargs):
        if name in ("marimo", "jupyterlab"):
            return None
        return real_find_spec(name, *args, **kwargs)

    monkeypatch.setattr(importlib.util, "find_spec", missing)
    assert tool_problem("marimo") == REINSTALL_TEXT == "Reinstall the app: pysrim2 install app"
    assert tool_problem("jupyter") == REINSTALL_TEXT


def test_tool_problem_is_none_when_the_tools_are_installed(monkeypatch):
    monkeypatch.setattr(importlib.util, "find_spec", lambda name, *a, **k: object())
    assert tool_problem("marimo") is None
    assert tool_problem("jupyter") is None
