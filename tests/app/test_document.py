"""The run setup document: edits, undo, open and save, and the histogram files a save copies.

These tests need PySide6 and pytest-qt (they skip without them); the model they use is Qt-free.
"""

import shutil
from copy import deepcopy
from pathlib import Path

import pytest
import yaml

pytest.importorskip("PySide6")
pytest.importorskip("pytestqt")

from srim.app.document import DocumentModel  # noqa: E402
from srim.app.scan_model import new_scan  # noqa: E402
from srim.scan import Scan  # noqa: E402
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


def test_a_binary_file_is_refused_on_open_and_changes_nothing(tmp_path):
    doc = DocumentModel()
    before = deepcopy(doc.doc)
    binary = tmp_path / "run.yaml"
    binary.write_bytes(b"\xff\xfe\x00\x81\x80not text at all")
    with pytest.raises(SpecError, match="not a text file"):
        doc.open(binary)
    assert doc.doc == before and doc.path is None and not doc.can_undo


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


HISTOGRAM = {"histogram": {"edges": [19, 20, 21], "counts": [1, 2]}}  # inline, not from a file

#: setups the editors do not offer (Review Focus 1): each is changed from the boron setup
NOT_OFFERED = {
    "calc4_binned_inline_histogram_plot_mode": {
        "calculation": 4,
        "beam": {"mode": "binned"},
        "settings": {"plot_mode": 0},
        "ion": {"symbol": "B", "energy_keV": HISTOGRAM, "angle_deg": 7.0},
        "gas": True,
    },
    "calc5_binned_inline_histogram": {
        "calculation": 5,
        "beam": {"mode": "binned"},
        "ion": {"symbol": "B", "energy_keV": HISTOGRAM, "angle_deg": 7.0},
        "gas": True,
    },
    "calc6_binned_inline_histogram": {
        "calculation": 6,
        "beam": {"mode": "binned"},
        "ion": {"symbol": "B", "energy_keV": HISTOGRAM, "angle_deg": 7.0},
    },
    "calc7_binned_inline_histogram": {
        "calculation": 7,
        "beam": {"mode": "binned"},
        "ion": {"symbol": "B", "energy_keV": HISTOGRAM, "angle_deg": 7.0},
    },
    "fixed_energy_and_angle_with_gas": {
        "ion": {
            "symbol": "B",
            "energy_keV": {"fixed": {"value": 20.0}},
            "angle_deg": {"fixed": {"value": 7.0}},
        },
        "gas": True,
    },
}


@pytest.mark.parametrize("case", sorted(NOT_OFFERED))
def test_a_saved_setup_keeps_what_the_editors_do_not_offer(case, tmp_path, boron_data, write_setup):
    changes = dict(NOT_OFFERED[case])
    if changes.pop("gas", False):
        boron_data["target"].append(deepcopy(GAS))  # gas layer with binding energies
    boron_data.update(changes)
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


# --- the scan section ---------------------------------------------------------------

SCAN_FILES = {
    "explicit_points": {
        "points": [
            {"ion.energy_keV": 10.0, "target[0].thickness_nm": 50.0},
            {"ion.energy_keV": 20.0, "target[0].thickness_nm": 100.0},
        ],
        "extract": ["implant_summary"],
        "ions": 100,
    },
    "python_extractor": {
        "grid": {"ion.energy_keV": [10, 20]},
        "extract": ["implant_summary", "python:tests.app.extractors:depth_sum"],
        "ions": 100,
    },
    "precision_target": {
        "grid": {"ion.angle_deg": [0, 7]},
        "precision": {"target": "sputter_yield", "rel": 0.05, "min_ions": 100, "max_ions": 500},
    },
    "keep_seed_one_point": {"ions": 100, "seed": 3, "keep_seed": True},
    "batches_and_seed": {
        "grid": {"ion.energy_keV": [10, 20]},
        "ions": 1000,
        "batch_ions": 100,
        "seed": 3,
    },
}


def fresh_folder(tmp_path):
    """An empty folder to save into (a save does not create its folder)."""
    folder = tmp_path / "new"
    folder.mkdir(exist_ok=True)
    return folder


def write_scan_file(path, boron_data, scan):
    """A hand-written run file: the spec of ``boron_data`` and the ``scan:`` section ``scan``."""
    spec = RunSpec.from_dict(boron_data).to_dict()
    version = spec.pop("version")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        yaml.safe_dump({"version": version, "spec": spec, "scan": scan}, sort_keys=False),
        encoding="utf-8",
    )
    return path


@pytest.mark.parametrize("name", sorted(SCAN_FILES))
def test_a_scan_file_opens_and_saves_to_the_same_setup_and_scan(tmp_path, boron_data, name):
    old = write_scan_file(tmp_path / "old" / "run.yaml", boron_data, SCAN_FILES[name])
    doc = DocumentModel()
    doc.open(old)
    new = fresh_folder(tmp_path) / "run.yaml"
    doc.save(new)
    assert RunSpec.from_yaml(new).to_dict() == RunSpec.from_yaml(old).to_dict()
    assert Scan.from_yaml(new).to_dict() == Scan.from_yaml(old).to_dict()


def test_a_keep_seed_scan_file_has_no_blocking_problem_and_round_trips(tmp_path, boron_data):
    old = write_scan_file(
        tmp_path / "old" / "run.yaml", boron_data, SCAN_FILES["keep_seed_one_point"]
    )
    doc = DocumentModel()
    doc.open(old)
    assert not [problem for problem in doc.problems if problem.blocking]
    new = fresh_folder(tmp_path) / "run.yaml"
    doc.save(new)
    assert RunSpec.from_yaml(new).to_dict() == RunSpec.from_yaml(old).to_dict()
    assert Scan.from_yaml(new).to_dict() == Scan.from_yaml(old).to_dict()


def test_a_scan_file_with_a_python_extractor_is_written_back_as_a_reference(tmp_path, boron_data):
    old = write_scan_file(tmp_path / "old" / "run.yaml", boron_data, SCAN_FILES["python_extractor"])
    doc = DocumentModel()
    doc.open(old)
    assert doc.problems == []
    doc.save(fresh_folder(tmp_path) / "run.yaml")
    assert (
        "python:tests.app.extractors:depth_sum" in (fresh_folder(tmp_path) / "run.yaml").read_text()
    )


def test_a_plain_run_saved_has_no_scan_section(tmp_path, boron_data, write_setup):
    old = write_setup(tmp_path / "old" / "run.yaml", boron_data)
    doc = DocumentModel()
    doc.open(old)
    assert "_scan" not in doc.doc
    doc.save(fresh_folder(tmp_path) / "run.yaml")
    assert "scan:" not in (fresh_folder(tmp_path) / "run.yaml").read_text()


def test_a_scan_is_part_of_the_undo_history(tmp_path, boron_data):
    path = write_scan_file(tmp_path / "run.yaml", boron_data, SCAN_FILES["explicit_points"])
    doc = DocumentModel()
    doc.open(path)
    assert not doc.is_modified

    def more_ions(d):
        d["_scan"]["ions"] = 500

    doc.edit(more_ions)
    assert doc.is_modified
    doc.undo()
    assert doc.doc["_scan"]["ions"] == 100 and not doc.is_modified


def test_a_scan_with_nothing_to_vary_cannot_be_saved(tmp_path, boron_data, write_setup):
    doc = opened(tmp_path / "base" / "run.yaml", boron_data, write_setup)
    doc.edit(lambda d: d.update({"_scan": new_scan()}))
    target = fresh_folder(tmp_path) / "run.yaml"
    with pytest.raises(SpecError, match="Add a value to vary"):
        doc.save(target)
    assert not target.exists()


def test_a_setup_in_a_run_folder_is_in_the_run_folder_until_it_is_saved_elsewhere(
    tmp_path, write_setup, boron_data
):
    from srim.scan.executor import MARKER_NAME

    run = tmp_path / "boron-runs" / "20261112T000000Z"
    run.mkdir(parents=True)
    (run / MARKER_NAME).write_text("scan\n", encoding="utf-8")
    doc = DocumentModel()
    assert not doc.in_run_folder
    doc.open(write_setup(run / "scan.yaml", boron_data))
    assert doc.in_run_folder
    doc.open(write_setup(tmp_path / "other" / "run.yaml", boron_data))
    assert not doc.in_run_folder
    doc.new()
    assert not doc.in_run_folder
