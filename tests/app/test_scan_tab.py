"""The Scan tab: the scan of the setup, through the document (each edit is one undo step).

The range dialog and the "Other…" parameter prompt are monkeypatched where a test needs an answer.
Every value is typed into the widget and committed the way the user commits it (Enter or focus out).
"""

from copy import deepcopy

import pytest

pytest.importorskip("PySide6")
pytest.importorskip("pytestqt")

from PySide6.QtWidgets import QApplication, QDialog  # noqa: E402

from srim.app import scan_tab as scan_module  # noqa: E402
from srim.app.document import DocumentModel  # noqa: E402
from srim.app.scan_model import scan_from_section  # noqa: E402
from srim.app.scan_tab import RangeDialog, ScanTab  # noqa: E402

DEFAULT_SCAN = {"grid": {}, "extract": ["implant_summary"], "ions": None}


@pytest.fixture
def document(qtbot):
    return DocumentModel()


@pytest.fixture
def tab(qtbot, document):
    widget = ScanTab(document)
    qtbot.addWidget(widget)
    return widget


@pytest.fixture
def loaded(document, boron_data):
    """The document of the boron reference case: no scan yet."""
    document.edit(lambda doc: doc.update(deepcopy(boron_data)))
    return document


@pytest.fixture
def scanning(tab, loaded):
    """The boron case with a default scan (ticked on the tab)."""
    tab.enabled_box.setChecked(True)
    return loaded


def pick(combo, data):
    combo.setCurrentIndex(combo.findData(data))


def type_into(edit, text):
    edit.setText(text)
    edit.editingFinished.emit()


def scan_of(document):
    return document.doc["_scan"]


def test_ticking_run_as_a_scan_stores_the_default_and_unticking_removes_it(tab, loaded):
    tab.enabled_box.setChecked(True)
    assert loaded.doc["_scan"] == DEFAULT_SCAN
    loaded.undo()
    assert "_scan" not in loaded.doc
    assert not tab.enabled_box.isChecked()
    tab.enabled_box.setChecked(True)
    tab.enabled_box.setChecked(False)
    assert "_scan" not in loaded.doc
    loaded.undo()
    assert loaded.doc["_scan"] == DEFAULT_SCAN


def test_a_parameter_with_typed_values_is_the_grid(scanning, tab):
    tab.add_row()
    assert tab.rows[0].name == "ion.energy_keV"
    type_into(tab.rows[0].edit, "10, 20")
    assert scan_of(scanning)["grid"] == {"ion.energy_keV": [10.0, 20.0]}


def test_values_can_be_separated_by_semicolons_or_spaces(scanning, tab):
    tab.add_row()
    type_into(tab.rows[0].edit, "1; 2 4")
    assert scan_of(scanning)["grid"] == {"ion.energy_keV": [1.0, 2.0, 4.0]}


def test_a_value_that_is_not_a_number_is_refused_and_the_text_stays(scanning, tab):
    tab.add_row()
    type_into(tab.rows[0].edit, "10, x")
    assert scan_of(scanning)["grid"] == {"ion.energy_keV": None}
    assert tab.rows[0].edit.text() == "10, x"


def test_a_layer_thickness_is_chosen_from_the_layers(scanning, tab):
    tab.add_row()
    pick(tab.rows[0].combo, "target.0.thickness_nm")
    assert scan_of(scanning)["grid"] == {"target.0.thickness_nm": None}


def test_other_takes_a_typed_dotted_key(scanning, tab, monkeypatch):
    monkeypatch.setattr(
        scan_module.QInputDialog,
        "getText",
        staticmethod(lambda *args, **kwargs: ("target[0].density_g_cm3", True)),
    )
    tab.add_row()
    pick(tab.rows[0].combo, scan_module.OTHER)
    assert scan_of(scanning)["grid"] == {"target.0.density_g_cm3": None}


def test_the_range_dialog_fills_the_values(scanning, tab, monkeypatch):
    def fill(self):
        self.start.setValue(1)
        self.stop.setValue(100)
        self.steps.setValue(3)
        self.log.setChecked(True)
        return QDialog.DialogCode.Accepted

    monkeypatch.setattr(RangeDialog, "exec", fill)
    tab.add_row()
    tab.rows[0].range_button.click()
    assert scan_of(scanning)["grid"] == {"ion.energy_keV": [1.0, 10.0, 100.0]}


def test_a_range_that_is_refused_keeps_the_dialog_open(qtbot):
    dialog = RangeDialog()
    qtbot.addWidget(dialog)
    dialog.start.setValue(0)
    dialog.stop.setValue(10)
    dialog.log.setChecked(True)
    dialog._accept()
    assert dialog.result() != QDialog.DialogCode.Accepted
    assert dialog.message.text() != ""


def test_switching_to_a_precision_target_removes_the_ions(scanning, tab):
    tab.ions.setValue(500)
    pick(tab.mode, "precision")
    assert "ions" not in scan_of(scanning)
    assert scan_of(scanning)["precision"] == {}
    pick(tab.mode, "fixed")
    assert "precision" not in scan_of(scanning)
    assert scan_of(scanning)["ions"] is None


def test_a_precision_target_and_a_relative_error_are_stored_without_the_defaults(scanning, tab):
    pick(tab.mode, "precision")
    type_into(tab.precision_target.lineEdit(), "mean_depth_nm")
    tab.rel.setValue(0.05)
    assert scan_of(scanning)["precision"] == {"target": "mean_depth_nm", "rel": 0.05}


def test_a_precision_count_that_differs_from_its_default_is_stored(scanning, tab):
    pick(tab.mode, "precision")
    tab.min_ions.setValue(1000)
    assert "min_ions" not in scan_of(scanning)["precision"]
    tab.min_ions.setValue(500)
    assert scan_of(scanning)["precision"]["min_ions"] == 500


def test_ticking_vacancy_at_with_a_depth_stores_its_entry(scanning, tab):
    tab.extract_boxes["vacancy_at"].setChecked(True)
    type_into(tab.vacancy_depth, "50")
    assert scan_of(scanning)["extract"] == [
        "implant_summary",
        {"vacancy_at": {"depth_nm": 50.0}},
    ]
    tab.extract_boxes["vacancy_at"].setChecked(False)
    assert scan_of(scanning)["extract"] == ["implant_summary"]


def test_explicit_points_and_python_extractors_show_read_only_and_are_kept(loaded, tab):
    section = {
        "points": [{"ion.energy_keV": 10.0}, {"ion.energy_keV": 20.0}],
        "extract": ["implant_summary", "python:my_mod:deep"],
    }
    loaded.edit(lambda doc: doc.__setitem__("_scan", scan_from_section(section)))
    assert [line.text() for line in tab.kept_lines] == [
        "2 explicit points from the file",
        "python:my_mod:deep",
    ]
    tab.ions.setValue(100)
    assert scan_of(loaded)["points"] == section["points"]
    assert scan_of(loaded)["extract"] == section["extract"]
    assert scan_of(loaded)["ions"] == 100


def test_new_open_and_undo_show_the_documents_scan(
    tab, document, boron_data, write_setup, tmp_path
):
    path = write_setup(tmp_path / "run.yaml", boron_data)
    path.write_text(
        path.read_text(encoding="utf-8")
        + "scan:\n  grid:\n    ion.energy_keV: [10, 20]\n  ions: 100\n",
        encoding="utf-8",
    )
    document.open(path)
    assert tab.enabled_box.isChecked()
    assert [row.name for row in tab.rows] == ["ion.energy_keV"]
    assert tab.rows[0].edit.text() == "10, 20"
    assert tab.ions.value() == 100
    tab.ions.setValue(200)
    document.undo()
    assert tab.ions.value() == 100
    document.new()
    assert not tab.enabled_box.isChecked()
    assert tab.rows == []
    assert tab.ions.value() == 0


def test_seed_and_batch_are_stored_and_blank_or_zero_is_none(scanning, tab):
    tab.seed.setValue(7)
    assert scan_of(scanning)["seed"] == 7
    tab.seed.setValue(tab.seed.minimum())
    assert scan_of(scanning)["seed"] is None
    tab.batch_ions.setValue(5)
    assert scan_of(scanning)["batch_ions"] == 5
    tab.batch_ions.setValue(0)
    assert scan_of(scanning)["batch_ions"] is None


def test_a_keep_seed_scan_shows_its_line_and_keeps_the_flag(loaded, tab):
    section = {"keep_seed": True, "ions": 200}
    loaded.edit(lambda doc: doc.__setitem__("_scan", scan_from_section(section)))
    assert tab.enabled_box.isChecked()
    assert not tab.keep_label.isHidden()
    assert tab.keep_label.text() == "Runs the setup's own seed (one point)"
    tab.seed.setValue(9)
    assert scan_of(loaded)["keep_seed"] is True
    assert scan_of(loaded)["seed"] == 9


def test_the_note_says_the_ions_per_point_are_separate(tab):
    assert "Beam tab" in tab.note.text()


def test_focus_target_vary_focuses_the_table(qtbot, scanning, tab):
    tab.show()
    qtbot.waitExposed(tab)
    tab.focus_target("vary")
    qtbot.waitUntil(lambda: QApplication.focusWidget() is tab.table)


def test_focus_target_seed_focuses_the_seed(qtbot, scanning, tab):
    tab.show()
    qtbot.waitExposed(tab)
    tab.focus_target("seed")
    qtbot.waitUntil(lambda: QApplication.focusWidget() is tab.seed)


def test_an_unchanged_blank_precision_target_is_not_an_edit(scanning, tab, monkeypatch):
    pick(tab.mode, "precision")
    edits = []
    real_edit = scanning.edit
    monkeypatch.setattr(scanning, "edit", lambda fn: (edits.append(fn), real_edit(fn)))
    type_into(tab.precision_target.lineEdit(), "")
    type_into(tab.precision_target.lineEdit(), "   ")
    assert edits == []
    assert scan_of(scanning)["precision"] == {}
    type_into(tab.precision_target.lineEdit(), "mean_depth_nm")
    assert len(edits) == 1
    type_into(tab.precision_target.lineEdit(), "")
    assert len(edits) == 2  # clearing a set target is an edit: it is stored as blank
