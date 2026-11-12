"""The Run settings tab: the calculation, seed, description and run options, through the document.

Each edit is one call of ``document.edit``. A setting equal to its ``SETTINGS_SCHEMA`` default is
removed from ``settings``, and an empty ``settings`` mapping is removed with it.
"""

from copy import deepcopy

import pytest

pytest.importorskip("PySide6")
pytest.importorskip("pytestqt")

from PySide6.QtWidgets import QApplication  # noqa: E402

from srim.app.document import DocumentModel  # noqa: E402
from srim.app.run_settings import RunSettingsTab  # noqa: E402
from srim.app.settings import AppSettings  # noqa: E402
from srim.app.window import MainWindow  # noqa: E402


@pytest.fixture
def document(qtbot):
    return DocumentModel()


@pytest.fixture
def tab(qtbot, document):
    widget = RunSettingsTab(document)
    qtbot.addWidget(widget)
    return widget


@pytest.fixture
def loaded(document, boron_data):
    """The document of the boron reference case: no settings, calculation 1."""
    document.edit(lambda doc: doc.update(deepcopy(boron_data)))
    return document


def combo_pick(box, value):
    box.setCurrentIndex(box.findData(value))


def type_into(edit, text):
    edit.setText(text)
    edit.editingFinished.emit()


def test_ticking_ranges_stores_it_and_unticking_removes_it(tab, loaded):
    tab.widgets["ranges"].setChecked(True)
    assert loaded.doc["settings"] == {"ranges": 1}
    tab.widgets["ranges"].setChecked(False)
    assert "settings" not in loaded.doc


def test_collisions_ions_and_recoils_stores_2(tab, loaded):
    combo_pick(tab.widgets["collisions"], 2)
    assert loaded.doc["settings"] == {"collisions": 2}
    combo_pick(tab.widgets["collisions"], 0)
    assert "settings" not in loaded.doc


def test_the_default_description_stores_nothing_and_another_is_stored(tab, loaded):
    type_into(tab.widgets["description"], "pysrim run")
    assert loaded.doc.get("settings", {}) == {}
    type_into(tab.widgets["description"], "my run")
    assert loaded.doc["settings"] == {"description": "my run"}


def test_calculation_2_is_stored_as_the_calculation(tab, loaded):
    combo_pick(tab.widgets["calculation"], 2)
    assert loaded.doc["calculation"] == 2


def test_a_loaded_calculation_5_is_shown_and_kept(tab, loaded):
    loaded.edit(lambda doc: doc.__setitem__("calculation", 5))
    assert tab.widgets["calculation"].currentText() == "SRIM calculation 5"
    tab.widgets["seed"].setValue(7)
    assert loaded.doc["calculation"] == 5
    assert tab.widgets["calculation"].currentText() == "SRIM calculation 5"


def test_choosing_a_calculation_drops_the_loaded_one_from_the_list(tab, loaded):
    loaded.edit(lambda doc: doc.__setitem__("calculation", 5))
    combo_pick(tab.widgets["calculation"], 1)
    assert loaded.doc["calculation"] == 1
    assert tab.widgets["calculation"].findData(5) == -1


def test_the_seed_is_stored(tab, loaded):
    tab.widgets["seed"].setValue(42)
    assert loaded.doc["seed"] == 42


def test_exyz_off_removes_the_setting_and_a_value_stores_it(tab, loaded):
    tab.widgets["exyz"].setValue(5)
    assert loaded.doc["settings"] == {"exyz": 5}
    tab.widgets["exyz"].setValue(0)
    assert "settings" not in loaded.doc


def test_bragg_correction_of_1_is_the_default(tab, loaded):
    tab.widgets["bragg_correction"].setValue(1.5)
    assert loaded.doc["settings"] == {"bragg_correction": 1.5}
    tab.widgets["bragg_correction"].setValue(1.0)
    assert "settings" not in loaded.doc


def test_autosave_off_and_a_value(tab, loaded):
    tab.widgets["autosave"].setValue(10)
    assert loaded.doc["settings"] == {"autosave": 10}
    tab.widgets["autosave"].setValue(0)
    assert "settings" not in loaded.doc


def test_showing_the_document_makes_no_edit(tab, document, loaded):
    loaded.edit(lambda doc: doc.__setitem__("settings", {"plot_mode": 0}))
    undo_before = document.can_undo
    tab._refresh()
    assert document.doc["settings"] == {"plot_mode": 0}
    assert document.can_undo == undo_before
    assert tab.widgets["description"].text() == "pysrim run"


def test_a_quote_in_the_description_is_the_librarys_message_in_the_status_bar(
    qtbot, document, boron_data
):
    win = MainWindow(AppSettings(route="wine"), checks_fn=lambda route: [], document=document)
    qtbot.addWidget(win)
    document.edit(lambda doc: doc.update(deepcopy(boron_data)))
    tab = win.editors["settings"]
    type_into(tab.widgets["description"], 'a"b')
    targets = [problem.target for problem in document.problems]
    assert "settings/description" in targets
    assert 'double quote' in win.status.shown_text()


def test_focus_target_description_focuses_the_line_edit(qtbot, tab):
    tab.show()
    qtbot.waitExposed(tab)
    tab.focus_target("description")
    qtbot.waitUntil(lambda: QApplication.focusWidget() is tab.widgets["description"])


def test_a_status_bar_click_on_a_setting_selects_the_tab_and_focuses_it(qtbot, document):
    win = MainWindow(AppSettings(route="wine"), checks_fn=lambda route: [], document=document)
    qtbot.addWidget(win)
    win.show()
    qtbot.waitExposed(win)
    win.status.jump.emit("settings/description")
    assert win.tabs.currentWidget() is win.editors["settings"]
    qtbot.waitUntil(
        lambda: QApplication.focusWidget() is win.editors["settings"].widgets["description"]
    )


def test_a_document_with_plot_mode_keeps_it_across_edits(tab, document, loaded):
    loaded.edit(lambda doc: doc.__setitem__("settings", {"plot_mode": 0}))
    tab.widgets["ranges"].setChecked(True)
    tab.widgets["seed"].setValue(3)
    combo_pick(tab.widgets["collisions"], 1)
    assert document.doc["settings"] == {"plot_mode": 0, "ranges": 1, "collisions": 1}
