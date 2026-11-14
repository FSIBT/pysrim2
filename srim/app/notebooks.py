"""Notebooks in a run folder: the starter files and what the app needs to start them (Qt-free).

A notebook is ``notebook.py`` (marimo) or ``notebook.ipynb`` (Jupyter) in the run folder. The
starter is copied in only when the file is absent, so an edited notebook is never overwritten.
The app's own files for notebooks go in ``<run folder>/.pysrim2-notebook/`` (see
:mod:`srim.notebook`), where :func:`create_notebook` also creates the ``reload`` file.
"""

import importlib.util
import shutil
from pathlib import Path

import srim
from srim.notebook import NOTEBOOK_DIR, RELOAD_FILE

KINDS = ("marimo", "jupyter")
NOTEBOOK_NAMES = {"marimo": "notebook.py", "jupyter": "notebook.ipynb"}
STARTERS = {"marimo": "starter_marimo.py", "jupyter": "starter_jupyter.ipynb"}
#: the package that a notebook kind needs (its top-level name)
TOOLS = {"marimo": "marimo", "jupyter": "jupyterlab"}
REINSTALL_TEXT = "Reinstall the app: pysrim2 install app"


def _check_kind(kind: str) -> None:
    if kind not in KINDS:
        raise ValueError(f"unknown notebook kind {kind!r}; use one of: {', '.join(KINDS)}")


def notebook_path(folder, kind: str) -> Path:
    """The notebook file of ``kind`` in the run folder ``folder`` (it may not exist yet)."""
    _check_kind(kind)
    return Path(folder) / NOTEBOOK_NAMES[kind]


def starter_path(kind: str) -> Path:
    """The starter notebook of ``kind`` that ships with the library."""
    _check_kind(kind)
    return Path(srim.__file__).parent / "data" / "notebooks" / STARTERS[kind]


def create_notebook(folder, kind: str) -> Path:
    """Copy the starter of ``kind`` into the run folder and create the reload file.

    An existing notebook is left as it is. The reload file (``.pysrim2-notebook/reload``) is
    created when absent; Reload touches it. Returns the notebook's path.
    """
    folder = Path(folder)
    path = notebook_path(folder, kind)
    if not path.exists():
        shutil.copyfile(starter_path(kind), path)
    reload = folder / NOTEBOOK_DIR / RELOAD_FILE
    reload.parent.mkdir(parents=True, exist_ok=True)
    reload.touch(exist_ok=True)
    return path


def tool_problem(kind: str) -> str | None:
    """:data:`REINSTALL_TEXT` when the tool that ``kind`` needs is not installed, else None."""
    _check_kind(kind)
    if importlib.util.find_spec(TOOLS[kind]) is None:
        return REINSTALL_TEXT
    return None
