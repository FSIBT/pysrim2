"""The gallery entries that run SRIM build their pictures (``pytest -m srim``).

Each entry writes its picture into the folder given to ``make.py --out`` and nothing else.
An entry's ``IONS`` is lowered to ``QUICK_IONS`` here, so that the CI jobs stay within their
time limit; the committed pictures are built by ``make.py`` with the full counts.
"""

import importlib.util
from pathlib import Path

import pytest

pytest.importorskip("matplotlib")

GALLERY = Path(__file__).resolve().parents[2] / "docs" / "gallery"

_spec = importlib.util.spec_from_file_location("gallery_make_for_srim_tests", GALLERY / "make.py")
make = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(make)

SRIM_NAMES = [n for n in make.entries() if make.load(n).NEEDS_SRIM]
QUICK_IONS = 50


@pytest.mark.parametrize("name", SRIM_NAMES)
def test_srim_entry_builds_its_picture(name, tmp_path, capsys, monkeypatch):
    load = make.load

    def quick_load(entry):
        module = load(entry)
        if hasattr(module, "IONS"):
            module.IONS = min(module.IONS, QUICK_IONS)
        return module

    monkeypatch.setattr(make, "load", quick_load)
    assert make.main(["--out", str(tmp_path), name]) == 0
    output = tmp_path / make.load(name).OUTPUT
    assert output.is_file() and output.stat().st_size > 0
    assert sorted(p.name for p in tmp_path.iterdir()) == [output.name]
    assert f"{name}: {output}" in capsys.readouterr().out
