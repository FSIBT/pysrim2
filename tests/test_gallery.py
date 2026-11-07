"""Tests for the documentation gallery runner (docs/gallery/make.py)."""

import importlib.util
import types
from pathlib import Path

import pytest

pytest.importorskip("matplotlib")

GALLERY = Path(__file__).resolve().parents[1] / "docs" / "gallery"
IMAGES = GALLERY / "images"

_spec = importlib.util.spec_from_file_location("gallery_make_for_tests", GALLERY / "make.py")
make = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(make)

NAMES = make.entries()
SRIM_NAMES = [n for n in NAMES if make.load(n).NEEDS_SRIM]
NO_SRIM_NAMES = [n for n in NAMES if not make.load(n).NEEDS_SRIM]


def test_every_entry_has_the_fields():
    assert NAMES, "the gallery has no entries"
    for name in NAMES:
        module = make.load(name)
        assert isinstance(module.TITLE, str) and module.TITLE
        assert isinstance(module.NEEDS_SRIM, bool)
        assert callable(module.make)
        assert module.OUTPUT.endswith((".png", ".svg"))


def test_every_entry_has_its_committed_image():
    for name in NAMES:
        path = IMAGES / make.load(name).OUTPUT
        assert path.is_file(), f"{path} is missing; rebuild the gallery images"
        assert path.stat().st_size < 300 * 1024, f"{path} is 300 KB or larger"


def test_list_marks_the_srim_entries(capsys):
    assert make.main(["--list"]) == 0
    lines = capsys.readouterr().out.splitlines()
    assert len(lines) == len(NAMES)
    for name, line in zip(NAMES, lines, strict=True):
        module = make.load(name)
        assert line.startswith(name)
        assert ("[SRIM]" in line) == module.NEEDS_SRIM
        assert line.endswith(module.TITLE)


def test_list_shows_the_title_of_a_srim_entry(tmp_path, monkeypatch, capsys):
    fake = types.SimpleNamespace(
        TITLE="Fake SRIM title", NEEDS_SRIM=True, OUTPUT="fake.png", make=None
    )
    real_load = make.load
    monkeypatch.setattr(make, "entries", lambda: ["fake"])
    monkeypatch.setattr(make, "load", lambda name: fake if name == "fake" else real_load(name))
    assert make.main(["--list"]) == 0
    assert capsys.readouterr().out.splitlines() == ["fake  [SRIM]  Fake SRIM title"]


def test_list_reports_a_broken_entry_and_exits_1(monkeypatch, capsys):
    real_load = make.load

    def load(name):
        if name == "heatmap":
            raise SyntaxError("bad entry")
        return real_load(name)

    monkeypatch.setattr(make, "load", load)
    assert make.main(["--list"]) == 1
    out = capsys.readouterr().out
    assert "FAILED heatmap: bad entry" in out
    assert "depth_profiles  " in out


@pytest.mark.parametrize("name", NO_SRIM_NAMES)
def test_entries_without_srim_run(name, tmp_path, capsys):
    assert make.main(["--out", str(tmp_path), name]) == 0
    output = tmp_path / make.load(name).OUTPUT
    assert output.is_file() and output.stat().st_size > 0
    assert sorted(p.name for p in tmp_path.iterdir()) == [output.name]
    assert f"{name}: {output}" in capsys.readouterr().out


@pytest.mark.skipif(not SRIM_NAMES, reason="no gallery entry needs SRIM yet")
def test_srim_entries_are_skipped_without_srim(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(make, "srim_available", lambda: False)
    assert make.main(["--out", str(tmp_path)] + SRIM_NAMES) == 0
    out = capsys.readouterr().out
    for name in SRIM_NAMES:
        assert f"skip {name}: needs SRIM" in out
    assert list(tmp_path.iterdir()) == []


def test_a_failing_entry_makes_the_exit_code_1(tmp_path, monkeypatch, capsys):
    class Broken:
        TITLE = "broken"
        NEEDS_SRIM = False
        OUTPUT = "heatmap.png"

        @staticmethod
        def make(outdir):
            raise RuntimeError("boom")

    monkeypatch.setattr(make, "load", lambda name: Broken)
    assert make.main(["--out", str(tmp_path), "heatmap"]) == 1
    assert "FAILED heatmap: boom" in capsys.readouterr().out


def test_unknown_entry_is_an_error(capsys):
    assert make.main(["nope"]) == 2
    assert "nope" in capsys.readouterr().err
