"""The Beam tab: the ion, the number of ions and each beam quantity, through the document.

Dialogs are monkeypatched: the periodic table returns a fixed choice, the file dialog returns a
fixed path, and the histogram preview is accepted. Every edit is one call of ``document.edit``.
"""

from copy import deepcopy

import pytest

pytest.importorskip("PySide6")
pytest.importorskip("pytestqt")

from PySide6.QtCore import Qt  # noqa: E402
from PySide6.QtWidgets import QApplication, QDialog  # noqa: E402

from srim.app import beam_tab as beam_module  # noqa: E402
from srim.app.beam_tab import BeamTab, HistogramPreview  # noqa: E402
from srim.app.document import DocumentModel  # noqa: E402
from srim.app.elements import ElementChoice  # noqa: E402
from srim.app.model import BINNED_AUTO, BINNED_TEXT, EXACT_TEXT, to_spec  # noqa: E402
from srim.app.settings import AppSettings  # noqa: E402
from srim.app.window import MainWindow  # noqa: E402
from srim.beam.binned import make_bins  # noqa: E402
from srim.beam.shapes import Histogram  # noqa: E402
from srim.spec import SpecError  # noqa: E402


@pytest.fixture
def document(qtbot):
    return DocumentModel()


@pytest.fixture
def tab(qtbot, document):
    widget = BeamTab(document)
    qtbot.addWidget(widget)
    return widget


@pytest.fixture
def with_setup(document, boron_data):
    """The document of the boron reference case: ion B at 20 keV, a gold layer."""
    document.edit(lambda doc: doc.update(deepcopy(boron_data)))
    return document


def pick(editor, kind):
    editor.kind_box.setCurrentIndex(editor.kind_box.findData(kind))


def type_into(edit, text):
    edit.setText(text)
    edit.editingFinished.emit()


def record(editor):
    received = []
    editor.value_changed.connect(received.append)
    return received


def count_edits(document, monkeypatch):
    calls = []
    original = document.edit

    def counting(fn):
        calls.append(fn)
        original(fn)

    monkeypatch.setattr(document, "edit", counting)
    return calls


def ion(document):
    return document.doc["ion"]


# --- the ion, the number of ions and the layout ----------------------------------------


def test_the_tab_has_the_ion_the_count_and_the_six_quantities(tab):
    assert tab.ion_button.text() == "Ion: not chosen"
    assert (tab.number_ions.minimum(), tab.number_ions.maximum()) == (1, 2147483647)
    assert tab.number_ions.value() == 1000
    assert list(tab.editors) == [
        "energy_keV",
        "angle_deg",
        "azimuth_deg",
        "y_nm",
        "z_nm",
        "depth_nm",
    ]
    assert tab.editors["energy_keV"].title() == "Energy (keV)"
    assert tab.editors["angle_deg"].title() == "Angle (degrees)"
    assert tab.editors["depth_nm"].title() == "Depth (nm)"


def test_required_quantities_have_no_not_set_choice_and_optional_ones_do(tab):
    def kinds(key):
        box = tab.editors[key].kind_box
        return [box.itemData(i) for i in range(box.count())]

    assert kinds("energy_keV") == [
        "value",
        "gaussian",
        "uniform",
        "triangular",
        "lognormal",
        "function",
        "histogram",
    ]
    assert kinds("azimuth_deg")[0] == "unset"


def test_the_tab_scrolls_its_editors_and_plots(tab):
    from PySide6.QtWidgets import QScrollArea

    (scroll,) = tab.findChildren(QScrollArea)
    assert scroll.widgetResizable()
    for editor in tab.editors.values():
        assert scroll.isAncestorOf(editor)
    for plot in tab.plots.values():
        assert scroll.isAncestorOf(plot)


def test_the_window_puts_the_beam_tab_in_the_beam_place(qtbot):
    win = MainWindow(AppSettings(route="wine"), checks_fn=lambda route: [])
    qtbot.addWidget(win)
    assert isinstance(win.editors["beam"], BeamTab)
    assert win.tabs.widget(1) is win.editors["beam"]


def test_choosing_b10_sets_the_ion_and_its_button(tab, document, monkeypatch):
    monkeypatch.setattr(
        beam_module, "choose_element", lambda parent, title: ElementChoice("B", 10.0129370)
    )
    tab.ion_button.click()
    assert ion(document)["symbol"] == "B"
    assert ion(document)["mass_amu"] == pytest.approx(10.0129370, abs=1e-7)
    assert tab.ion_button.text() == "Ion: B-10"


def test_choosing_natural_removes_the_mass(tab, document, monkeypatch):
    monkeypatch.setattr(
        beam_module, "choose_element", lambda parent, title: ElementChoice("B", 10.0129370)
    )
    tab.ion_button.click()
    monkeypatch.setattr(beam_module, "choose_element", lambda parent, title: ElementChoice("B"))
    tab.ion_button.click()
    assert "mass_amu" not in ion(document)
    assert tab.ion_button.text() == "Ion: B (natural)"


def test_cancelling_the_periodic_table_changes_nothing(tab, document, monkeypatch):
    monkeypatch.setattr(beam_module, "choose_element", lambda parent, title: None)
    calls = count_edits(document, monkeypatch)
    tab.ion_button.click()
    assert calls == []


def test_the_number_of_ions_is_one_edit(tab, document, monkeypatch):
    calls = count_edits(document, monkeypatch)
    tab.number_ions.setValue(500)
    assert document.doc["number_ions"] == 500
    assert len(calls) == 1


def test_typing_the_number_of_ions_waits_for_enter(qtbot, tab, document):
    tab.number_ions.selectAll()
    qtbot.keyClicks(tab.number_ions, "5")
    assert document.doc["number_ions"] == 1000
    qtbot.keyClick(tab.number_ions, Qt.Key.Key_Return)
    assert document.doc["number_ions"] == 5


def test_an_edit_elsewhere_refreshes_the_fields_and_undo_restores_them(tab, document, with_setup):
    document.edit(lambda doc: doc["ion"].update(energy_keV=25.0))
    assert tab.editors["energy_keV"].fields["value"].text() == "25.0"
    document.undo()
    assert tab.editors["energy_keV"].fields["value"].text() == "20.0"


# --- one quantity: fixed values, distributions and the limit -------------------------------


def test_a_gaussian_energy_emits_its_mapping(tab, document):
    editor = tab.editors["energy_keV"]
    received = record(editor)
    pick(editor, "gaussian")
    type_into(editor.fields["mean"], "20")
    type_into(editor.fields["sigma"], "1")
    assert received[-1] == {"gaussian": {"mean": 20.0, "sigma": 1.0}}
    assert ion(document)["energy_keV"] == {"gaussian": {"mean": 20.0, "sigma": 1.0}}


def test_a_limit_to_adds_the_range_inside_the_mapping(tab, document):
    editor = tab.editors["energy_keV"]
    pick(editor, "gaussian")
    type_into(editor.fields["mean"], "20")
    type_into(editor.fields["sigma"], "1")
    type_into(editor.limit_low, "18")
    type_into(editor.limit_high, "22")
    assert ion(document)["energy_keV"] == {
        "gaussian": {"mean": 20.0, "sigma": 1.0, "range": [18.0, 22.0]}
    }


@pytest.mark.parametrize(
    "kind, fields, expected",
    [
        ("uniform", {"low": "19", "high": "21"}, {"uniform": {"low": 19.0, "high": 21.0}}),
        (
            "triangular",
            {"low": "1", "mode": "2", "high": "4"},
            {"triangular": {"low": 1.0, "mode": 2.0, "high": 4.0}},
        ),
        ("lognormal", {"mu": "0.5", "sigma": "0.1"}, {"lognormal": {"mu": 0.5, "sigma": 0.1}}),
        (
            "function",
            {"ref": "python:mod:fn"},
            {"function": {"ref": "python:mod:fn"}},
        ),
    ],
)
def test_each_distribution_kind_emits_its_own_fields(tab, document, kind, fields, expected):
    editor = tab.editors["energy_keV"]
    pick(editor, kind)
    for name, text in fields.items():
        type_into(editor.fields[name], text)
    assert ion(document)["energy_keV"] == expected


def test_an_empty_sigma_emits_none_and_the_status_bar_names_the_library_message(
    qtbot, monkeypatch, boron_data
):
    win = MainWindow(AppSettings(route="wine"), checks_fn=lambda route: [])
    qtbot.addWidget(win)
    win.document.edit(lambda doc: doc.update(deepcopy(boron_data)))
    editor = win.editors["beam"].editors["energy_keV"]
    received = record(editor)
    pick(editor, "gaussian")
    type_into(editor.fields["mean"], "20")
    type_into(editor.fields["sigma"], "")
    assert received[-1] == {"gaussian": {"mean": 20.0, "sigma": None}}
    assert win.document.problems[0].target == "beam/energy_keV"
    assert "sigma" in win.status.shown_text()
    win.close()


def test_an_empty_energy_is_missing_and_the_setup_says_so(tab, document, with_setup):
    editor = tab.editors["energy_keV"]
    type_into(editor.fields["value"], "")
    assert ion(document)["energy_keV"] is None
    assert document.problems[0].text == "Enter the ion energy on the Beam tab"


def test_a_comma_decimal_is_read_as_a_point(tab, document, with_setup):
    type_into(tab.editors["energy_keV"].fields["value"], "1,5")
    assert ion(document)["energy_keV"] == 1.5


def test_a_number_the_library_refuses_is_emitted_for_the_library_to_judge(
    tab, document, with_setup
):
    type_into(tab.editors["energy_keV"].fields["value"], "-1")
    assert ion(document)["energy_keV"] == -1.0
    assert "must be > 0" in document.problems[0].text


@pytest.mark.parametrize("text", ["abc", "1e400", "nan"])
def test_a_refused_number_keeps_the_old_value_and_emits_nothing(
    tab, document, with_setup, monkeypatch, text
):
    editor = tab.editors["energy_keV"]
    received = record(editor)
    calls = count_edits(document, monkeypatch)
    type_into(editor.fields["value"], text)
    assert received == []
    assert calls == []
    assert editor.fields["value"].text() == "20.0"


def test_the_validator_refuses_letters_while_typing(qtbot, tab):
    edit = tab.editors["energy_keV"].fields["value"]
    qtbot.keyClicks(edit, "abc")
    assert edit.text() == ""
    qtbot.keyClicks(edit, "1,5e-2")
    assert edit.text() == "1,5e-2"


def test_an_unchanged_field_does_not_rewrite_the_document(tab, document, with_setup, monkeypatch):
    document.edit(lambda doc: doc["ion"].update(energy_keV=20.123456789))
    calls = count_edits(document, monkeypatch)
    tab.editors["energy_keV"].fields["value"].editingFinished.emit()
    assert calls == []
    assert ion(document)["energy_keV"] == 20.123456789


def test_a_loaded_distribution_shows_its_fields_and_emits_nothing(tab, document):
    document.edit(
        lambda doc: doc["ion"].update(
            energy_keV={"uniform": {"low": 19.0, "high": 21.0, "range": [18.0, 22.0]}}
        )
    )
    editor = tab.editors["energy_keV"]
    assert editor.kind_box.currentData() == "uniform"
    assert editor.fields["low"].text() == "19.0"
    assert (editor.limit_low.text(), editor.limit_high.text()) == ("18.0", "22.0")


def test_azimuth_not_set_removes_the_azimuth(tab, document, with_setup):
    editor = tab.editors["azimuth_deg"]
    pick(editor, "value")
    type_into(editor.fields["value"], "10")
    assert ion(document)["azimuth_deg"] == 10.0
    pick(editor, "unset")
    assert "azimuth_deg" not in ion(document)


def test_setting_only_the_depth_fills_the_others_with_zero(tab, document, with_setup):
    editor = tab.editors["depth_nm"]
    pick(editor, "value")
    type_into(editor.fields["value"], "50")
    assert ion(document)["position"] == {"y_nm": 0.0, "z_nm": 0.0, "depth_nm": 50.0}


def test_all_three_positions_not_set_removes_the_position(tab, document, with_setup):
    editor = tab.editors["depth_nm"]
    pick(editor, "value")
    type_into(editor.fields["value"], "50")
    for key in ("y_nm", "z_nm", "depth_nm"):
        pick(tab.editors[key], "unset")
    assert "position" not in ion(document)


def test_a_position_distribution_keeps_the_others_at_zero(tab, document, with_setup):
    editor = tab.editors["y_nm"]
    pick(editor, "gaussian")
    type_into(editor.fields["mean"], "1")
    type_into(editor.fields["sigma"], "0.5")
    assert ion(document)["position"] == {
        "y_nm": {"gaussian": {"mean": 1.0, "sigma": 0.5}},
        "z_nm": 0.0,
        "depth_nm": 0.0,
    }


def test_the_function_kind_takes_a_reference_and_a_range(tab, document, with_setup):
    editor = tab.editors["energy_keV"]
    pick(editor, "function")
    type_into(editor.fields["ref"], "python:mod:fn")
    type_into(editor.limit_low, "0")
    type_into(editor.limit_high, "1")
    assert ion(document)["energy_keV"] == {
        "function": {"ref": "python:mod:fn", "range": [0.0, 1.0]}
    }


def test_focus_target_focuses_the_energy_field(qtbot, tab):
    tab.show()
    qtbot.waitExposed(tab)
    tab.focus_target("energy_keV")
    qtbot.waitUntil(lambda: QApplication.focusWidget() is tab.editors["energy_keV"].fields["value"])


# --- histogram files ------------------------------------------------------------------------


def write_histogram(path, text):
    path.write_text(text)
    return path


def test_loading_a_histogram_stores_its_absolute_path(
    tab, document, with_setup, tmp_path, monkeypatch
):
    path = write_histogram(tmp_path / "beam.txt", "10 5\n20 9\n30 3\n")
    monkeypatch.setattr(
        beam_module.QFileDialog, "getOpenFileName", staticmethod(lambda *a, **k: (str(path), ""))
    )
    monkeypatch.setattr(HistogramPreview, "exec", lambda self: QDialog.DialogCode.Accepted)
    editor = tab.editors["energy_keV"]
    pick(editor, "histogram")
    editor.load_button.click()
    assert ion(document)["energy_keV"] == {"histogram": {"file": str(path.resolve())}}


def test_a_broken_histogram_shows_the_library_message_and_changes_nothing(
    tab, document, with_setup, tmp_path, monkeypatch
):
    path = write_histogram(tmp_path / "bad.txt", "10 5\n")  # one row: a histogram needs two
    expected = str(pytest.raises(SpecError, Histogram.from_file, path).value)
    shown = []
    monkeypatch.setattr(
        beam_module.QFileDialog, "getOpenFileName", staticmethod(lambda *a, **k: (str(path), ""))
    )
    monkeypatch.setattr(
        beam_module.QMessageBox, "warning", staticmethod(lambda *a, **k: shown.append(a[-1]))
    )
    editor = tab.editors["energy_keV"]
    pick(editor, "histogram")
    before = deepcopy(document.doc)
    editor.load_button.click()
    assert shown == [expected]
    assert document.doc == before


def test_a_histogram_with_decreasing_edges_is_refused_too(
    tab, document, with_setup, tmp_path, monkeypatch
):
    path = write_histogram(tmp_path / "down.txt", "30 20 5\n20 10 4\n")
    expected = str(pytest.raises(SpecError, Histogram.from_file, path).value)
    shown = []
    monkeypatch.setattr(
        beam_module.QFileDialog, "getOpenFileName", staticmethod(lambda *a, **k: (str(path), ""))
    )
    monkeypatch.setattr(
        beam_module.QMessageBox, "warning", staticmethod(lambda *a, **k: shown.append(a[-1]))
    )
    editor = tab.editors["energy_keV"]
    pick(editor, "histogram")
    editor.load_button.click()
    assert shown == [expected]
    assert ion(document)["energy_keV"] == {"histogram": {"file": None}}


def test_a_cancelled_file_dialog_changes_nothing(tab, document, with_setup, monkeypatch):
    monkeypatch.setattr(
        beam_module.QFileDialog, "getOpenFileName", staticmethod(lambda *a, **k: ("", ""))
    )
    editor = tab.editors["energy_keV"]
    pick(editor, "histogram")
    before = deepcopy(document.doc)
    editor.load_button.click()
    assert document.doc == before


def test_the_preview_draws_the_histogram_with_a_canvas(qtbot, tmp_path):
    from matplotlib.backends.backend_qtagg import FigureCanvasQTAgg

    path = write_histogram(tmp_path / "beam.txt", "10 5\n20 9\n30 3\n")
    preview = HistogramPreview(Histogram.from_file(path))
    qtbot.addWidget(preview)
    assert preview.findChildren(FigureCanvasQTAgg)


def test_a_loaded_fixed_shape_is_kept_until_the_user_picks_another_kind(tab, document, with_setup):
    fixed = {"fixed": {"value": 20.0}}
    document.edit(lambda doc: doc["ion"].update(energy_keV=deepcopy(fixed)))
    editor = tab.editors["energy_keV"]
    before = deepcopy(document.doc)
    received = record(editor)
    tab.number_ions.setValue(500)
    assert editor.value == fixed
    assert editor.kind_box.currentData() == "replace"
    assert "fixed:" in editor.yaml_view.toPlainText()
    assert received == []
    assert ion(document) == before["ion"]
    pick(editor, "gaussian")
    assert received[-1] == {"gaussian": {"mean": None, "sigma": None}}


def test_a_not_set_coordinate_next_to_a_set_one_is_stored_as_zero(tab, document, with_setup):
    pick(tab.editors["depth_nm"], "value")
    type_into(tab.editors["depth_nm"].fields["value"], "50")
    pick(tab.editors["y_nm"], "value")
    type_into(tab.editors["y_nm"].fields["value"], "1")
    pick(tab.editors["y_nm"], "unset")
    assert ion(document)["position"] == {"y_nm": 0.0, "z_nm": 0.0, "depth_nm": 50.0}
    # The document holds 0.0, so the coordinate shows as a fixed value of 0.0.
    assert tab.editors["y_nm"].kind_box.currentData() == "value"
    assert float(tab.editors["y_nm"].fields["value"].text()) == 0.0
    assert float(tab.editors["depth_nm"].fields["value"].text()) == 50.0


def test_opening_a_position_shows_its_zero_coordinates_as_fixed_values(
    tab, document, write_setup, tmp_path, boron_data
):
    data = deepcopy(boron_data)
    data["ion"]["position"] = {"y_nm": 1.0, "z_nm": 0.0, "depth_nm": 0.0}
    document.open(write_setup(tmp_path / "run.yaml", data))
    for key, number in (("y_nm", 1.0), ("z_nm", 0.0), ("depth_nm", 0.0)):
        editor = tab.editors[key]
        assert editor.kind_box.currentData() == "value"
        assert float(editor.fields["value"].text()) == number


def test_new_and_open_show_the_new_document_not_the_previous_position(
    tab, document, write_setup, tmp_path, boron_data
):
    pick(tab.editors["depth_nm"], "value")
    type_into(tab.editors["depth_nm"].fields["value"], "50")
    document.new()
    assert all(
        tab.editors[key].kind_box.currentData() == "unset" for key in ("y_nm", "z_nm", "depth_nm")
    )

    pick(tab.editors["depth_nm"], "value")
    type_into(tab.editors["depth_nm"].fields["value"], "50")
    data = deepcopy(boron_data)
    data["ion"]["position"] = {"y_nm": 1.0, "z_nm": 0.0, "depth_nm": 0.0}
    document.open(write_setup(tmp_path / "other.yaml", data))
    assert tab.editors["z_nm"].kind_box.currentData() == "value"
    assert float(tab.editors["z_nm"].fields["value"].text()) == 0.0
    assert float(tab.editors["depth_nm"].fields["value"].text()) == 0.0

    document.new()
    assert tab.editors["z_nm"].kind_box.currentData() == "unset"


def test_undo_after_a_coordinate_change_shows_the_restored_state(tab, document, with_setup):
    pick(tab.editors["depth_nm"], "value")
    type_into(tab.editors["depth_nm"].fields["value"], "50")
    pick(tab.editors["y_nm"], "value")
    type_into(tab.editors["y_nm"].fields["value"], "2")
    document.undo()
    assert ion(document)["position"] == {"y_nm": 0.0, "z_nm": 0.0, "depth_nm": 50.0}
    assert float(tab.editors["y_nm"].fields["value"].text()) == 0.0


def test_not_set_on_a_distribution_next_to_zeros_removes_the_position(tab, document, with_setup):
    document.edit(
        lambda doc: doc["ion"].update(
            position={
                "y_nm": {"gaussian": {"mean": 1.0, "sigma": 0.5}},
                "z_nm": 0.0,
                "depth_nm": 0.0,
            }
        )
    )
    pick(tab.editors["y_nm"], "unset")
    assert "position" not in ion(document)


def test_not_set_next_to_nonzero_coordinates_stores_zero(tab, document, with_setup):
    document.edit(
        lambda doc: doc["ion"].update(position={"y_nm": 1.0, "z_nm": 2.0, "depth_nm": 0.0})
    )
    pick(tab.editors["y_nm"], "unset")
    assert ion(document)["position"] == {"y_nm": 0.0, "z_nm": 2.0, "depth_nm": 0.0}
    assert tab.editors["y_nm"].kind_box.currentData() == "value"


def test_undo_of_a_position_shows_its_coordinates_not_set_again(tab, document, with_setup):
    pick(tab.editors["depth_nm"], "value")
    type_into(tab.editors["depth_nm"].fields["value"], "50")
    document.undo()
    assert "position" not in ion(document)
    assert all(
        tab.editors[key].kind_box.currentData() == "unset" for key in ("y_nm", "z_nm", "depth_nm")
    )
    document.redo()
    assert ion(document)["position"] == {"y_nm": 0.0, "z_nm": 0.0, "depth_nm": 50.0}
    assert tab.editors["depth_nm"].fields["value"].text() == "50.0"


# --- exact or binned, and the distribution plots -----------------------------------------


def choose_mode(tab, mode):
    tab.mode_combo.setCurrentIndex(tab.mode_combo.findData(mode))


def gaussian_energy(tab, mean="20", sigma="1"):
    editor = tab.editors["energy_keV"]
    pick(editor, "gaussian")
    type_into(editor.fields["mean"], mean)
    type_into(editor.fields["sigma"], sigma)


def dashed(plot):
    return [line for line in plot.axes.lines if line.get_linestyle() == "--"]


def test_a_gaussian_energy_plot_is_one_visible_line(tab, document, with_setup):
    gaussian_energy(tab)
    plot = tab.plots["energy_keV"]
    assert not plot.isHidden()
    assert len(plot.axes.lines) == 1


def test_a_plain_energy_hides_its_plot(tab, document, with_setup):
    assert tab.plots["energy_keV"].isHidden()


def test_automatic_with_calculation_3_reads_binned_and_says_why(tab, document, with_setup):
    gaussian_energy(tab)
    document.edit(lambda doc: doc.update(calculation=3))
    assert tab.mode_combo.currentText() == "Automatic (binned)"
    assert tab.mode_text.text() == BINNED_AUTO
    assert "beam" not in document.doc


def test_automatic_with_calculation_1_reads_exact(tab, document, with_setup):
    gaussian_energy(tab)
    assert tab.mode_combo.currentText() == "Automatic (exact)"
    assert tab.mode_text.text() == EXACT_TEXT
    assert "beam" not in document.doc


def test_the_mode_names_are_the_three_items(tab, document, with_setup):
    gaussian_energy(tab)
    texts = [tab.mode_combo.itemText(i) for i in range(tab.mode_combo.count())]
    assert texts == ["Automatic (exact)", "Exact (TRIM.DAT)", "Binned"]


def test_binned_with_four_energy_bins_stores_the_bins_and_five_edges(tab, document, with_setup):
    gaussian_energy(tab)
    choose_mode(tab, "binned")
    assert document.doc["beam"] == {"mode": "binned"}
    assert tab.mode_text.text() == BINNED_TEXT
    tab.energy_bins.setValue(4)
    assert document.doc["beam"] == {"mode": "binned", "bins": {"energy_keV": 4}}
    edges = sorted(
        {b.energy_range[0] for b in make_bins(to_spec(document.doc))}
        | {b.energy_range[1] for b in make_bins(to_spec(document.doc))}
    )
    assert len(edges) == 5
    assert len(dashed(tab.plots["energy_keV"])) == 5


def test_zero_bins_is_automatic_and_stores_no_bins(tab, document, with_setup):
    gaussian_energy(tab)
    choose_mode(tab, "binned")
    tab.energy_bins.setValue(4)
    tab.energy_bins.setValue(0)
    assert document.doc["beam"] == {"mode": "binned"}
    assert tab.energy_bins.specialValueText() == "Automatic (10)"
    assert tab.angle_bins.specialValueText() == "Automatic (6)"


def test_exact_keeps_the_bins_for_a_return_to_binned(tab, document, with_setup):
    gaussian_energy(tab)
    choose_mode(tab, "binned")
    tab.energy_bins.setValue(4)
    choose_mode(tab, "exact")
    assert document.doc["beam"] == {"mode": "exact", "bins": {"energy_keV": 4}}
    to_spec(document.doc)  # the library accepts the bins in exact mode
    assert tab.mode_text.text() == EXACT_TEXT
    choose_mode(tab, "binned")
    assert tab.energy_bins.value() == 4


def test_max_bins_survives_exact(tab, document, with_setup):
    gaussian_energy(tab)
    document.edit(lambda doc: doc.update(beam={"mode": "binned", "max_bins": 500}))
    choose_mode(tab, "exact")
    assert document.doc["beam"] == {"mode": "exact", "max_bins": 500}
    to_spec(document.doc)  # the library accepts it


def test_switching_back_to_automatic_removes_the_beam_key(tab, document, with_setup):
    gaussian_energy(tab)
    choose_mode(tab, "binned")
    tab.energy_bins.setValue(4)
    choose_mode(tab, "auto")
    assert "beam" not in document.doc


def test_the_bin_fields_are_enabled_only_in_binned_mode_for_distributed_quantities(
    tab, document, with_setup
):
    gaussian_energy(tab)
    assert not tab.energy_bins.isEnabled()
    choose_mode(tab, "binned")
    assert tab.energy_bins.isEnabled()
    assert not tab.angle_bins.isEnabled()  # the angle is a fixed 7 degrees


def gaussian_angle(tab, mean="7", sigma="0.5"):
    editor = tab.editors["angle_deg"]
    pick(editor, "gaussian")
    type_into(editor.fields["mean"], mean)
    type_into(editor.fields["sigma"], sigma)


def test_a_bin_count_for_a_quantity_that_becomes_fixed_is_removed(tab, document, with_setup):
    gaussian_energy(tab)
    gaussian_angle(tab)
    choose_mode(tab, "binned")
    tab.energy_bins.setValue(4)
    pick(tab.editors["energy_keV"], "value")
    type_into(tab.editors["energy_keV"].fields["value"], "20")
    # The angle is still distributed, so the beam stays binned, but its energy bins are gone.
    assert document.doc["beam"] == {"mode": "binned"}
    assert not tab.energy_bins.isEnabled()


def test_a_plain_beam_hides_the_mode_and_shows_the_single_run_label(tab, document, with_setup):
    assert tab.mode_group.isHidden()
    assert not tab.single_label.isHidden()
    assert tab.single_label.text() == "Single energy and angle: one SRIM run."


def test_a_distributed_beam_shows_the_mode_group(tab, document, with_setup):
    gaussian_energy(tab)
    assert not tab.mode_group.isHidden()
    assert tab.single_label.isHidden()


def test_a_beam_key_left_after_the_beam_becomes_plain_is_removed(tab, document, with_setup):
    gaussian_energy(tab)
    gaussian_angle(tab)
    choose_mode(tab, "binned")
    pick(tab.editors["energy_keV"], "value")
    type_into(tab.editors["energy_keV"].fields["value"], "20")
    assert document.doc["beam"] == {"mode": "binned"}
    pick(tab.editors["angle_deg"], "value")
    type_into(tab.editors["angle_deg"].fields["value"], "7")
    assert "beam" not in document.doc
    assert not tab.single_label.isHidden()
    assert tab.mode_group.isHidden()


def test_undo_of_the_mode_change_shows_the_previous_mode(tab, document, with_setup):
    gaussian_energy(tab)
    choose_mode(tab, "binned")
    assert tab.mode_combo.currentData() == "binned"
    document.undo()
    assert tab.mode_combo.currentData() == "auto"


def test_new_shows_the_new_mode_not_the_previous_one(tab, document, with_setup):
    gaussian_energy(tab)
    choose_mode(tab, "binned")
    document.new()
    assert tab.mode_group.isHidden()
    gaussian_energy(tab)
    assert tab.mode_combo.currentData() == "auto"


def test_focus_target_mode_focuses_the_mode_box(qtbot, tab, document, with_setup):
    gaussian_energy(tab)
    tab.show()
    qtbot.waitExposed(tab)
    tab.focus_target("mode")
    qtbot.waitUntil(lambda: QApplication.focusWidget() is tab.mode_combo)


def test_the_status_bar_shows_the_library_bin_warning_for_400_bins(qtbot, boron_data):
    win = MainWindow(AppSettings(route="wine"), checks_fn=lambda route: [])
    qtbot.addWidget(win)
    win.document.edit(lambda doc: doc.update(deepcopy(boron_data)))
    tab = win.editors["beam"]
    gaussian_energy(tab)
    pick(tab.editors["angle_deg"], "gaussian")
    type_into(tab.editors["angle_deg"].fields["mean"], "7")
    type_into(tab.editors["angle_deg"].fields["sigma"], "0.5")
    choose_mode(tab, "binned")
    tab.energy_bins.setValue(20)
    tab.angle_bins.setValue(20)
    assert "400 bins" in win.status.shown_text()
    win.close()


def test_open_shows_the_opened_mode_bins_and_edges(
    tab, document, write_setup, tmp_path, boron_data
):
    gaussian_energy(tab)
    choose_mode(tab, "exact")
    data = deepcopy(boron_data)
    data["ion"]["energy_keV"] = {"gaussian": {"mean": 20.0, "sigma": 1.0}}
    data["beam"] = {"mode": "binned", "bins": {"energy_keV": 4}}
    document.open(write_setup(tmp_path / "run.yaml", data))
    assert tab.mode_combo.currentData() == "binned"
    assert tab.energy_bins.value() == 4
    assert tab.energy_bins.isEnabled()
    assert len(dashed(tab.plots["energy_keV"])) == 5
    document.new()
    assert tab.mode_group.isHidden()
    assert tab.mode_combo.currentData() == "auto"
