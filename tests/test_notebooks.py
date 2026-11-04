"""Static checks on the example notebooks (no SRIM needed)."""

import ast
import importlib
from pathlib import Path

import nbformat
import pytest

NOTEBOOKS = sorted((Path(__file__).resolve().parents[1] / "examples" / "notebooks").glob("*.ipynb"))


def test_notebooks_exist():
    assert {p.stem for p in NOTEBOOKS} >= {"Analysis", "Collision", "SiC", "Multilayer"}
    assert all(" " not in p.name for p in NOTEBOOKS)


@pytest.mark.parametrize("path", NOTEBOOKS, ids=lambda p: p.stem)
def test_notebook_is_valid_clean_and_imports_exist(path):
    notebook = nbformat.read(path, as_version=4)
    nbformat.validate(notebook)
    for cell in notebook.cells:
        if cell.cell_type != "code":
            continue
        assert not cell.outputs and cell.execution_count is None, "outputs are not committed"
        assert "/tmp/srim" not in cell.source, "use the default SRIM location"
        tree = ast.parse(cell.source)
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    importlib.import_module(alias.name)
            elif isinstance(node, ast.ImportFrom):
                module = importlib.import_module(node.module)
                for alias in node.names:
                    if not hasattr(module, alias.name):
                        importlib.import_module(f"{node.module}.{alias.name}")


@pytest.mark.parametrize("path", NOTEBOOKS, ids=lambda p: p.stem)
def test_notebook_uses_existing_srim_attributes(path):
    """``srim.X`` / ``import srim.y as z`` style references name things that exist."""
    import srim

    notebook = nbformat.read(path, as_version=4)
    for cell in notebook.cells:
        if cell.cell_type != "code":
            continue
        for node in ast.walk(ast.parse(cell.source)):
            if (
                isinstance(node, ast.Attribute)
                and isinstance(node.value, ast.Name)
                and node.value.id == "srim"
            ):
                assert hasattr(srim, node.attr), f"srim.{node.attr}"
