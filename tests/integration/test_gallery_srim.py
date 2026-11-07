"""The gallery entries that run SRIM build their pictures (``pytest -m srim``).

Each entry writes its picture into the folder given to ``make.py --out`` and nothing else.
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


@pytest.mark.parametrize("name", SRIM_NAMES)
def test_srim_entry_builds_its_picture(name, tmp_path, capsys):
    assert make.main(["--out", str(tmp_path), name]) == 0
    output = tmp_path / make.load(name).OUTPUT
    assert output.is_file() and output.stat().st_size > 0
    assert sorted(p.name for p in tmp_path.iterdir()) == [output.name]
    assert f"{name}: {output}" in capsys.readouterr().out
