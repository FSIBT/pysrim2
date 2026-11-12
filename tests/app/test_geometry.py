"""The Geometry tab: the layer table, the elements dialog and the material menu.

Edits go through the document, so each one is one undo step. Dialogs are monkeypatched: the
element chooser returns a fixed choice, and the name prompt returns a fixed name.
"""

from copy import deepcopy

import pytest

pytest.importorskip("PySide6")
pytest.importorskip("pytestqt")

from PySide6.QtCore import QModelIndex, Qt  # noqa: E402
from PySide6.QtWidgets import QAbstractItemView, QDialog, QInputDialog  # noqa: E402

from srim.app import geometry as geometry_module  # noqa: E402
from srim.app.document import DocumentModel  # noqa: E402
from srim.app.elements import ElementChoice  # noqa: E402
from srim.app.geometry import ElementsDialog, GeometryTab, LayerTableModel  # noqa: E402
from srim.app.materials import load_user_materials, materials_path  # noqa: E402
from srim.app.model import new_element, new_layer  # noqa: E402
from srim.core.isotopes import stable_isotopes  # noqa: E402

COLUMN = {name: i for i, name in enumerate(LayerTableModel.COLUMNS)}


def layer(name, density=None, thickness=None, elements=(), phase=0, unit="nm"):
    """A layer dict as the document holds it, with the display unit."""
    return {
        "name": name,
        "thickness_nm": thickness,
        "density_g_cm3": density,
        "phase": phase,
        "elements": list(elements),
        "_unit": unit,
    }


def silica_elements():
    """SiO2 as the material menu makes it: Si 1, O 2."""
    return [
        {**new_element("Si"), "stoich": 1.0},
        {**new_element("O"), "stoich": 2.0},
    ]


@pytest.fixture
def document(qtbot):
    return DocumentModel()


@pytest.fixture
def tab(qtbot, document):
    widget = GeometryTab(document)
    qtbot.addWidget(widget)
    return widget


def set_layers(document, *layers):
    def fn(doc):
        doc["target"] = [dict(item) for item in layers]

    document.edit(fn)


def cell(tab, row, column):
    return tab.model.index(row, COLUMN[column])


def text(tab, row, column):
    return tab.model.data(cell(tab, row, column), Qt.ItemDataRole.DisplayRole)


# --- the table: rows, selection and the buttons -----------------------------------------


def test_the_tab_has_the_table_the_buttons_and_the_layer_columns(tab):
    assert tab.model.COLUMNS == ("name", "elements", "density", "thickness", "unit", "phase")
    assert [tab.model.headerData(i, Qt.Orientation.Horizontal) for i in range(6)] == [
        "Name",
        "Elements",
        "Density (g/cm³)",
        "Thickness",
        "Unit",
        "Gas",
    ]
    assert tab.add_button.text() == "Add layer"
    assert tab.remove_button.text() == "Remove layer"
    assert tab.up_button.text() == "Up"
    assert tab.down_button.text() == "Down"
    assert tab.material_button.text() == "Material…"
    assert tab.save_material_button.text() == "Save as material…"


def test_adding_a_layer_appends_a_new_one_and_selects_it(tab, document, qtbot):
    with qtbot.waitSignal(tab.selected_layer, timeout=1000) as signal:
        tab.add_button.click()
    assert document.doc["target"] == [new_layer()]
    assert signal.args == [0]
    tab.add_button.click()
    assert len(document.doc["target"]) == 2
    assert tab.table.currentIndex().row() == 1


def test_remove_up_and_down_change_the_order_and_undo_restores_it(tab, document):
    set_layers(document, layer("A"), layer("B"), layer("C"))
    names = lambda: [item["name"] for item in document.doc["target"]]  # noqa: E731

    tab.select_layer(1)
    tab.up_button.click()
    assert names() == ["B", "A", "C"]
    assert tab.table.currentIndex().row() == 0
    assert not tab.up_button.isEnabled()

    tab.down_button.click()
    assert names() == ["A", "B", "C"]
    assert tab.table.currentIndex().row() == 1
    document.undo()
    assert names() == ["B", "A", "C"]

    tab.select_layer(2)
    tab.remove_button.click()
    assert names() == ["B", "A"]
    document.undo()
    assert names() == ["B", "A", "C"]


def test_the_buttons_follow_the_selection(tab, document):
    set_layers(document, layer("A"), layer("B"))
    assert not tab.remove_button.isEnabled()
    tab.select_layer(1)
    assert tab.remove_button.isEnabled()
    assert tab.up_button.isEnabled() and not tab.down_button.isEnabled()


def test_select_layer_emits_the_row_and_minus_one_clears(tab, document, qtbot):
    set_layers(document, layer("A"), layer("B"))
    with qtbot.waitSignal(tab.selected_layer, timeout=1000) as signal:
        tab.select_layer(1)
    assert signal.args == [1]
    with qtbot.waitSignal(tab.selected_layer, timeout=1000) as signal:
        tab.select_layer(-1)
    assert signal.args == [-1]


def test_the_selection_survives_an_undo_that_keeps_the_layer_count(tab, document):
    set_layers(document, layer("A"), layer("B"))
    tab.select_layer(0)
    document.edit(lambda d: d.__setitem__("seed", 3))
    document.undo()
    assert tab.table.currentIndex().row() == 0


def test_drag_reordering_is_a_row_move_in_the_model(tab, document):
    set_layers(document, layer("A"), layer("B"), layer("C"))
    assert tab.table.dragDropMode() == QAbstractItemView.DragDropMode.InternalMove
    assert tab.model.supportedDropActions() == Qt.DropAction.MoveAction

    assert tab.model.moveRows(QModelIndex(), 0, 1, QModelIndex(), 2)
    assert [item["name"] for item in document.doc["target"]] == ["B", "A", "C"]


def test_a_drop_between_rows_moves_the_dragged_layer(tab, document):
    set_layers(document, layer("A"), layer("B"), layer("C"))
    data = tab.model.mimeData([cell(tab, 0, "name")])
    assert tab.model.dropMimeData(data, Qt.DropAction.MoveAction, 3, 0, QModelIndex())
    assert [item["name"] for item in document.doc["target"]] == ["B", "C", "A"]


def test_moving_a_row_onto_its_own_place_changes_nothing(tab, document):
    set_layers(document, layer("A"), layer("B"))
    assert not tab.model.moveRows(QModelIndex(), 0, 1, QModelIndex(), 1)
    assert [item["name"] for item in document.doc["target"]] == ["A", "B"]


# --- what the cells show ------------------------------------------------------------------


def test_the_elements_cell_shows_symbols_with_their_amounts(tab, document):
    set_layers(document, layer("Silica", elements=silica_elements()))
    assert text(tab, 0, "elements") == "Si 1, O 2"


def test_an_isotope_in_the_elements_cell_is_named_by_its_isotope(tab, document):
    si28 = next(i for i in stable_isotopes("Si") if i.mass_number == 28)
    element = {**new_element("Si", si28.mass_amu), "stoich": 1.0}
    set_layers(document, layer("Si28", elements=[element]))
    assert text(tab, 0, "elements") == "Si-28 1"


def test_the_density_and_thickness_cells_show_the_stored_numbers(tab, document):
    set_layers(document, layer("gold", density=19.3, thickness=100.0))
    assert text(tab, 0, "density") == "19.3"
    assert text(tab, 0, "thickness") == "100"
    assert text(tab, 0, "unit") == "nm"


def test_a_missing_density_shows_an_empty_cell(tab, document):
    set_layers(document, layer("gold", density=None))
    assert text(tab, 0, "density") == ""


def test_the_gas_column_is_a_check_box_mapped_to_phase(tab, document):
    set_layers(document, layer("air", phase=1))
    gas = cell(tab, 0, "phase")
    assert tab.model.data(gas, Qt.ItemDataRole.CheckStateRole) == Qt.CheckState.Checked
    assert tab.model.setData(gas, Qt.CheckState.Unchecked, Qt.ItemDataRole.CheckStateRole)
    assert document.doc["target"][0]["phase"] == 0
    assert tab.model.setData(gas, Qt.CheckState.Checked, Qt.ItemDataRole.CheckStateRole)
    assert document.doc["target"][0]["phase"] == 1


# --- editing cells --------------------------------------------------------------------------


def test_thickness_in_micrometres_is_stored_in_nanometres(tab, document):
    set_layers(document, layer("film"))
    assert tab.model.setData(cell(tab, 0, "unit"), "µm", Qt.ItemDataRole.EditRole)
    assert tab.model.setData(cell(tab, 0, "thickness"), "10", Qt.ItemDataRole.EditRole)
    assert document.doc["target"][0]["thickness_nm"] == 10000.0


def test_changing_the_unit_only_changes_the_shown_number(tab, document):
    set_layers(document, layer("film", thickness=10000.0))
    assert text(tab, 0, "thickness") == "10000"
    assert tab.model.setData(cell(tab, 0, "unit"), "µm", Qt.ItemDataRole.EditRole)
    assert document.doc["target"][0]["thickness_nm"] == 10000.0
    assert text(tab, 0, "thickness") == "10"
    assert text(tab, 0, "unit") == "µm"


def test_an_unknown_unit_is_refused(tab, document):
    set_layers(document, layer("film", thickness=10.0))
    assert not tab.model.setData(cell(tab, 0, "unit"), "parsec", Qt.ItemDataRole.EditRole)
    assert document.doc["target"][0]["_unit"] == "nm"


def test_an_empty_density_stores_none_and_the_status_asks_for_it(tab, document):
    set_layers(document, layer("gold", density=19.3))
    assert tab.model.setData(cell(tab, 0, "density"), "", Qt.ItemDataRole.EditRole)
    assert document.doc["target"][0]["density_g_cm3"] is None
    assert any("enter the density" in problem.text for problem in document.problems)


@pytest.mark.parametrize("entry", ["abc", "1e400"])
def test_an_unparsable_density_is_refused_and_the_old_value_stays(tab, document, entry):
    set_layers(document, layer("gold", density=19.3))
    assert not tab.model.setData(cell(tab, 0, "density"), entry, Qt.ItemDataRole.EditRole)
    assert document.doc["target"][0]["density_g_cm3"] == 19.3


def test_a_comma_decimal_is_read_as_a_point(tab, document):
    set_layers(document, layer("glass", density=2.2))
    assert tab.model.setData(cell(tab, 0, "density"), "1,5", Qt.ItemDataRole.EditRole)
    assert document.doc["target"][0]["density_g_cm3"] == 1.5


def test_a_negative_density_is_stored_and_the_library_names_it(tab, document):
    document.edit(lambda d: d["ion"].update(symbol="B", energy_keV=20.0))
    set_layers(document, layer("gold", density=19.3, thickness=100.0, elements=silica_elements()))
    assert tab.model.setData(cell(tab, 0, "density"), "-1", Qt.ItemDataRole.EditRole)
    assert document.doc["target"][0]["density_g_cm3"] == -1.0
    blocking = [p for p in document.problems if p.blocking]
    assert blocking and "density_g_cm3" in blocking[0].text
    assert blocking[0].target == "geometry/0/density"


def test_an_empty_name_is_stored_as_typed(tab, document):
    set_layers(document, layer("gold"))
    assert tab.model.setData(cell(tab, 0, "name"), "Gold", Qt.ItemDataRole.EditRole)
    assert document.doc["target"][0]["name"] == "Gold"
    assert text(tab, 0, "name") == "Gold"


# --- problems shown in the cells -------------------------------------------------------------


def test_a_cell_named_by_a_blocking_problem_is_red_with_the_message(tab, document):
    set_layers(document, layer("gold", density=None, thickness=100.0, elements=silica_elements()))
    background = tab.model.data(cell(tab, 0, "density"), Qt.ItemDataRole.BackgroundRole)
    assert background is not None
    assert background.color().red() > background.color().green()
    tip = tab.model.data(cell(tab, 0, "density"), Qt.ItemDataRole.ToolTipRole)
    assert "enter the density" in tip
    assert tab.model.data(cell(tab, 0, "name"), Qt.ItemDataRole.BackgroundRole) is None


def test_the_missing_elements_mark_the_elements_cell(tab, document):
    set_layers(document, layer("gold", density=19.3, thickness=100.0))
    assert tab.model.data(cell(tab, 0, "elements"), Qt.ItemDataRole.BackgroundRole) is not None


# --- focus from the status bar ----------------------------------------------------------------


def test_focus_target_on_a_density_selects_the_cell_and_opens_its_editor(tab, document):
    set_layers(document, layer("gold", density=19.3, thickness=100.0))
    tab.focus_target("0/density")
    assert tab.table.currentIndex() == cell(tab, 0, "density")
    assert tab.table.state() == QAbstractItemView.State.EditingState


def test_focus_target_on_elements_selects_that_cell(tab, document):
    set_layers(document, layer("a"), layer("b"))
    tab.focus_target("1/elements")
    assert tab.table.currentIndex() == cell(tab, 1, "elements")


def test_focus_target_with_nothing_after_the_tab_only_shows_it(tab, document):
    set_layers(document, layer("a"))
    tab.focus_target("")
    assert not tab.table.selectionModel().hasSelection()


# --- the material menu ----------------------------------------------------------------------


def menu_action(tab, text_):
    return next(a for a in tab.material_button.menu().actions() if a.text() == text_)


def test_choosing_a_predefined_material_fills_the_layer_in_one_undo_step(tab, document):
    set_layers(document, layer("", thickness=50.0))
    tab.select_layer(0)
    before = document.doc["target"][0]
    menu_action(tab, "Silicon dioxide (fused silica)").trigger()
    after = document.doc["target"][0]
    assert after["name"] == "Silicon dioxide (fused silica)"
    assert after["density_g_cm3"] == 2.2
    assert after["thickness_nm"] == 50.0
    assert [(e["symbol"], e["stoich"]) for e in after["elements"]] == [("Si", 1.0), ("O", 2.0)]
    document.undo()
    assert document.doc["target"][0] == before


def test_the_material_menu_ends_with_my_materials_after_a_separator(tab):
    actions = tab.material_button.menu().actions()
    assert actions[-1].menu() is not None and actions[-1].text() == "My materials"
    assert any(a.isSeparator() for a in actions)


def test_a_saved_material_appears_under_my_materials_and_can_be_chosen(tab, document, monkeypatch):
    monkeypatch.setattr(QInputDialog, "getText", lambda *args, **kwargs: ("My glass", True))
    set_layers(document, layer("glass", density=2.5, elements=silica_elements()))
    tab.select_layer(0)
    tab.save_material_button.click()

    saved = load_user_materials(materials_path())
    assert [m.name for m in saved] == ["My glass"]
    assert saved[0].density_g_cm3 == 2.5

    mine = menu_action(tab, "My materials").menu()
    names = [action.text() for action in mine.actions()]
    assert names == ["My glass"]

    document.edit(lambda d: d["target"][0].update(elements=[], density_g_cm3=None))
    mine.actions()[0].trigger()
    layer_now = document.doc["target"][0]
    assert layer_now["density_g_cm3"] == 2.5
    assert [e["symbol"] for e in layer_now["elements"]] == ["Si", "O"]


def test_saving_a_layer_without_a_density_is_refused_with_a_warning(tab, document, monkeypatch):
    monkeypatch.setattr(QInputDialog, "getText", lambda *args, **kwargs: ("Broken", True))
    warnings = []
    monkeypatch.setattr(
        geometry_module.QMessageBox, "warning", lambda *args, **kwargs: warnings.append(args)
    )
    set_layers(document, layer("glass", density=None, elements=silica_elements()))
    tab.select_layer(0)
    tab.save_material_button.click()
    assert warnings
    assert load_user_materials(materials_path()) == []


# --- the elements dialog -----------------------------------------------------------------------


@pytest.fixture
def elements_dialog(qtbot):
    dialog = ElementsDialog([dict(e) for e in silica_elements()])
    qtbot.addWidget(dialog)
    return dialog


def test_the_elements_dialog_lists_each_element_with_its_fields(elements_dialog):
    assert elements_dialog.row_count() == 2
    assert elements_dialog.label(0) == "Si"
    assert elements_dialog.field(1, "stoich").text() == "2"
    assert elements_dialog.field(0, "displacement_eV").text() == "25"


def test_add_element_chooses_one_and_gives_it_stoich_one(elements_dialog, monkeypatch):
    monkeypatch.setattr(
        geometry_module, "choose_element", lambda parent, title: ElementChoice("Fe", None)
    )
    elements_dialog.add_button.click()
    assert elements_dialog.row_count() == 3
    added = elements_dialog.elements[2]
    assert added["symbol"] == "Fe" and added["stoich"] == 1.0
    assert "mass_amu" not in added


def test_add_element_cancelled_adds_nothing(elements_dialog, monkeypatch):
    monkeypatch.setattr(geometry_module, "choose_element", lambda parent, title: None)
    elements_dialog.add_button.click()
    assert elements_dialog.row_count() == 2


def test_choose_replaces_the_element_and_keeps_the_amounts(elements_dialog, monkeypatch):
    si28 = next(i for i in stable_isotopes("Si") if i.mass_number == 28)
    monkeypatch.setattr(
        geometry_module,
        "choose_element",
        lambda parent, title: ElementChoice("Si", si28.mass_amu),
    )
    elements_dialog.choose_button(0).click()
    first = elements_dialog.elements[0]
    assert first["mass_amu"] == si28.mass_amu
    assert first["stoich"] == 1.0
    assert elements_dialog.label(0) == "Si-28"


def test_remove_deletes_the_selected_element(elements_dialog):
    elements_dialog.table.selectRow(0)
    elements_dialog.remove_button.click()
    assert [e["symbol"] for e in elements_dialog.elements] == ["O"]


def test_an_amount_with_a_comma_is_read_and_an_unparsable_one_is_refused(elements_dialog):
    field = elements_dialog.field(0, "stoich")
    field.setText("2,5")
    field.editingFinished.emit()
    assert elements_dialog.elements[0]["stoich"] == 2.5

    field.setText("abc")
    field.editingFinished.emit()
    assert elements_dialog.elements[0]["stoich"] == 2.5
    assert field.text() == "2.5"


def test_an_empty_amount_becomes_missing(elements_dialog):
    field = elements_dialog.field(0, "stoich")
    field.setText("")
    field.editingFinished.emit()
    assert elements_dialog.elements[0]["stoich"] is None


def test_a_zero_or_negative_amount_is_stored_as_the_library_sees_it(elements_dialog):
    field = elements_dialog.field(1, "stoich")
    field.setText("-1")
    field.editingFinished.emit()
    assert elements_dialog.elements[1]["stoich"] == -1.0


def test_the_elements_button_applies_the_dialog_in_one_undo_step(tab, document, monkeypatch):
    set_layers(document, layer("glass", density=2.2))
    tab.select_layer(0)
    monkeypatch.setattr(
        geometry_module, "choose_element", lambda parent, title: ElementChoice("Fe", None)
    )

    def add_and_accept(self):
        self.add_button.click()
        return QDialog.DialogCode.Accepted

    monkeypatch.setattr(ElementsDialog, "exec", add_and_accept)
    tab.elements_button.click()
    assert [e["symbol"] for e in document.doc["target"][0]["elements"]] == ["Fe"]
    document.undo()
    assert document.doc["target"][0]["elements"] == []


def test_cancelling_the_elements_dialog_changes_nothing(tab, document, monkeypatch):
    set_layers(document, layer("glass", density=2.2, elements=silica_elements()))
    tab.select_layer(0)
    before = deepcopy(document.doc)
    monkeypatch.setattr(ElementsDialog, "exec", lambda self: QDialog.DialogCode.Rejected)
    tab.elements_button.click()
    assert document.doc == before
