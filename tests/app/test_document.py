"""The run setup document: edits, undo, open and save, and the histogram files a save copies.

These tests need PySide6 and pytest-qt (they skip without them); the model they use is Qt-free.
"""

import shutil
from copy import deepcopy
from pathlib import Path

import pytest

pytest.importorskip("PySide6")
pytest.importorskip("pytestqt")

from srim.app.document import DocumentModel  # noqa: E402
from srim.spec import RunSpec, SpecError  # noqa: E402

SPREAD = "19 1\n20 2\n21 1\n"
GAS = {
    "name": "nitrogen",
    "thickness_nm": 1000.0,
    "density_g_cm3": 0.00125,
    "phase": 1,
    "elements": [
        {
            "symbol": "N",
            "stoich": 2.0,
            "displacement_eV": 28.0,
            "lattice_eV": 3.0,
            "surface_eV": 2.0,
        }
    ],
}


def opened(path, boron_data, write_setup):
    write_setup(path, boron_data)
    doc = DocumentModel()
    doc.open(path)
    return doc


def test_a_new_document_is_unmodified_untitled_and_incomplete():
    doc = DocumentModel()
    assert doc.path is None and doc.base_dir is None
    assert not doc.is_modified and not doc.can_undo and not doc.can_redo
    assert doc.problems  # the ion, energy and layer are still missing


def test_edit_emits_changed_once_and_undo_and_redo_restore():
    doc = DocumentModel()
    seen = []
    doc.changed.connect(lambda: seen.append(doc.doc["seed"]))

    def set_seed(d):
        d["seed"] = 7

    doc.edit(set_seed)
    assert seen == [7]
    assert doc.doc["seed"] == 7 and doc.is_modified and doc.can_undo and not doc.can_redo
    doc.undo()
    assert doc.doc["seed"] == 0 and doc.can_redo and not doc.is_modified
    doc.redo()
    assert doc.doc["seed"] == 7
    assert seen == [7, 0, 7]


def test_an_edit_that_raises_changes_nothing(qtbot):
    doc = DocumentModel()
    before = deepcopy(doc.doc)
    seen = []
    doc.changed.connect(lambda: seen.append(True))

    def broken(d):
        d["seed"] = 99
        raise ValueError("no such value")

    with pytest.raises(ValueError, match="no such value"):
        doc.edit(broken)
    assert doc.doc == before and not doc.can_undo and seen == []


def test_problems_follow_each_edit(tmp_path, boron_data, write_setup):
    doc = opened(tmp_path / "run.yaml", boron_data, write_setup)
    assert doc.problems == []

    def drop_energy(d):
        d["ion"]["energy_keV"] = None

    doc.edit(drop_energy)
    assert [p.target for p in doc.problems] == ["beam/energy_keV"]
    doc.undo()
    assert doc.problems == []


def test_open_clears_the_history_so_undo_never_goes_back(tmp_path, boron_data, write_setup):
    path = write_setup(tmp_path / "run.yaml", boron_data)
    doc = DocumentModel()

    def set_seed(d):
        d["seed"] = 3

    doc.edit(set_seed)
    doc.open(path)
    assert not doc.can_undo and not doc.is_modified
    doc.undo()
    assert doc.doc["seed"] == 12345


def test_open_a_missing_or_broken_file_leaves_the_document_unchanged(tmp_path):
    doc = DocumentModel()
    before = deepcopy(doc.doc)
    with pytest.raises(SpecError):
        doc.open(tmp_path / "missing.yaml")
    broken = tmp_path / "broken.yaml"
    broken.write_text("version: 1\nspec: [not, a, mapping\n", encoding="utf-8")
    with pytest.raises(SpecError):
        doc.open(broken)
    assert doc.doc == before and doc.path is None


def test_a_histogram_file_that_is_broken_is_refused_on_open(tmp_path, boron_data, write_setup):
    source = tmp_path / "spread.txt"
    source.write_text(SPREAD, encoding="utf-8")
    boron_data["ion"]["energy_keV"] = {"histogram": {"file": str(source)}}
    path = write_setup(tmp_path / "run.yaml", boron_data)
    source.write_text("19 1\n", encoding="utf-8")  # one row is not a histogram
    doc = DocumentModel()
    with pytest.raises(SpecError):
        doc.open(path)
    assert doc.path is None and not doc.is_modified


def test_save_refuses_an_incomplete_setup_and_writes_nothing(tmp_path):
    doc = DocumentModel()
    target = tmp_path / "run.yaml"
    with pytest.raises(SpecError, match="Choose the ion: pick an element on the Beam tab"):
        doc.save(target)
    assert not target.exists() and doc.path is None


def test_a_saved_setup_keeps_what_the_editors_do_not_offer(tmp_path, boron_data, write_setup):
    boron_data.update(
        {
            "calculation": 4,
            "beam": {"mode": "binned"},  # calculation 4 with a distributed beam is binned
            "settings": {"plot_mode": 0},
            "ion": {
                "symbol": "B",
                "energy_keV": {"histogram": {"edges": [19, 20, 21], "counts": [1, 2]}},
                "angle_deg": 7.0,
            },
        }
    )
    boron_data["target"].append(GAS)
    old = write_setup(tmp_path / "old" / "run.yaml", boron_data)
    doc = DocumentModel()
    doc.open(old)
    new = tmp_path / "new" / "run.yaml"
    new.parent.mkdir()
    doc.save(new)
    assert RunSpec.from_yaml(new).to_dict() == RunSpec.from_yaml(old).to_dict()


def test_saving_writes_a_normal_run_file_with_provenance(tmp_path, boron_data, write_setup):
    doc = opened(tmp_path / "run.yaml", boron_data, write_setup)
    target = tmp_path / "copy" / "run.yaml"
    target.parent.mkdir()
    doc.save(target)
    assert doc.path == target and doc.base_dir == target.parent
    text = target.read_text(encoding="utf-8")
    assert text.startswith("version: 1\nspec:\n") and "pysrim2 " in text
    assert not doc.is_modified


def test_is_modified_follows_the_save(tmp_path, boron_data, write_setup):
    doc = opened(tmp_path / "run.yaml", boron_data, write_setup)

    def set_seed(d):
        d["seed"] = 5

    doc.edit(set_seed)
    assert doc.is_modified
    doc.save(tmp_path / "run.yaml")
    assert not doc.is_modified
    doc.undo()
    assert doc.is_modified
    doc.redo()
    assert not doc.is_modified


def test_a_saved_histogram_setup_still_opens_after_the_folder_moves(
    tmp_path, boron_data, write_setup
):
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    source = elsewhere / "spread.txt"
    source.write_text(SPREAD, encoding="utf-8")
    boron_data["ion"]["energy_keV"] = {"histogram": {"file": str(source)}}
    work = tmp_path / "work"
    path = write_setup(work / "run.yaml", boron_data)

    doc = DocumentModel()
    doc.open(path)
    doc.save(path)
    text = path.read_text(encoding="utf-8")
    assert "beam/spread.txt" in text and str(source) not in text
    assert (work / "beam" / "spread.txt").read_text(encoding="utf-8") == SPREAD

    moved_folder = tmp_path / "moved"
    shutil.move(work, moved_folder)
    again = DocumentModel()
    again.open(moved_folder / "run.yaml")
    assert again.problems == []
    histogram = again.doc["ion"]["energy_keV"]["histogram"]["file"]
    assert Path(histogram).resolve() == (moved_folder / "beam" / "spread.txt").resolve()


def test_a_relative_histogram_is_read_from_the_setup_folder_on_save(
    tmp_path, boron_data, write_setup
):
    first = tmp_path / "first"
    (first / "beam").mkdir(parents=True)
    (first / "beam" / "spread.txt").write_text(SPREAD, encoding="utf-8")
    doc = opened(first / "run.yaml", boron_data, write_setup)

    def relative(d):
        d["ion"]["energy_keV"] = {"histogram": {"file": "beam/spread.txt"}}

    doc.edit(relative)
    second = tmp_path / "second" / "run.yaml"
    second.parent.mkdir()
    doc.save(second)
    assert (tmp_path / "second" / "beam" / "spread.txt").read_text(encoding="utf-8") == SPREAD


def test_a_histogram_file_that_is_gone_refuses_the_save(tmp_path, boron_data, write_setup):
    source = tmp_path / "spread.txt"
    source.write_text(SPREAD, encoding="utf-8")
    boron_data["ion"]["energy_keV"] = {"histogram": {"file": str(source)}}
    path = write_setup(tmp_path / "run.yaml", boron_data)
    doc = DocumentModel()
    doc.open(path)
    source.unlink()
    target = tmp_path / "copy" / "run.yaml"
    target.parent.mkdir()
    with pytest.raises(SpecError):
        doc.save(target)
    assert not target.exists()
