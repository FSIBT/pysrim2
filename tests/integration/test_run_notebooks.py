"""Execute every example notebook with real SRIM (``pytest -m srim``)."""

import os
from pathlib import Path

import pytest

nbformat = pytest.importorskip("nbformat")
nbclient = pytest.importorskip("nbclient")
pytest.importorskip("ipykernel")

NOTEBOOKS = sorted((Path(__file__).resolve().parents[2] / "examples" / "notebooks").glob("*.ipynb"))


@pytest.mark.parametrize("path", NOTEBOOKS, ids=lambda p: p.stem)
def test_notebook_executes(path, tmp_path, monkeypatch):
    monkeypatch.setenv("MPLBACKEND", "Agg")
    notebook = nbformat.read(path, as_version=4)
    # run in a scratch folder so the notebooks' output folders do not land in the repo
    client = nbclient.NotebookClient(
        notebook,
        timeout=int(os.environ.get("NOTEBOOK_TIMEOUT", "900")),
        startup_timeout=int(os.environ.get("NOTEBOOK_STARTUP_TIMEOUT", "120")),
        kernel_name="python3",
        resources={"metadata": {"path": str(tmp_path)}},
    )
    client.execute()
