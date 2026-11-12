"""The periodic-table dialog and its isotope menu. Menu actions are triggered directly."""

import pytest

pytest.importorskip("PySide6")
pytest.importorskip("pytestqt")

from PySide6.QtWidgets import QDialog, QPushButton  # noqa: E402

from srim.app.elements import ElementChoice  # noqa: E402
from srim.app.periodic import PeriodicTableDialog, choose_element, isotope_menu  # noqa: E402
from srim.core.isotopes import stable_isotopes  # noqa: E402


@pytest.fixture
def dialog(qtbot):
    widget = PeriodicTableDialog()
    qtbot.addWidget(widget)
    return widget


def buttons(dialog):
    return dialog.findChildren(QPushButton)


def test_the_dialog_has_one_button_per_element(dialog):
    element_buttons = [b for b in buttons(dialog) if b.objectName().startswith("element-")]
    assert len(element_buttons) == 92


def test_a_button_is_named_by_its_symbol_and_tooltip_gives_name_and_z(dialog):
    button = dialog.findChild(QPushButton, "element-Si")
    assert button.text() == "Si"
    assert button.toolTip() == "Silicon, Z = 14"


def test_a_button_is_coloured_from_the_element_database(dialog):
    button = dialog.findChild(QPushButton, "element-Si")
    assert "#F0C8A0" in button.styleSheet()


def test_the_silicon_menu_offers_natural_then_three_isotopes(dialog, qtbot):
    menu = isotope_menu("Si", dialog)
    texts = [action.text() for action in menu.actions()]
    assert len(texts) == 4
    assert texts[0].startswith("Natural (28.0855")
    assert texts[1].startswith("Si-28")
    si28 = stable_isotopes("Si")[0]
    assert texts[1] == f"Si-28  {si28.mass_amu:.6f} amu  ({si28.abundance_percent:.4g} %)"


def test_triggering_an_isotope_chooses_it_and_accepts(dialog, qtbot):
    menu = isotope_menu("Si", dialog)
    si29 = next(a for a in menu.actions() if a.text().startswith("Si-29"))
    si29.trigger()
    assert dialog.choice.symbol == "Si"
    assert dialog.choice.mass_amu == pytest.approx(28.9764947, abs=1e-6)
    assert dialog.result() == QDialog.DialogCode.Accepted


def test_triggering_natural_chooses_no_mass(dialog, qtbot):
    menu = isotope_menu("Si", dialog)
    menu.actions()[0].trigger()
    assert dialog.choice == ElementChoice("Si", None)


def test_an_element_without_isotopes_has_only_the_natural_action(dialog):
    menu = isotope_menu("Tc", dialog)
    assert len(menu.actions()) == 1
    assert menu.actions()[0].text().startswith("Natural")


def test_choose_element_returns_what_the_dialog_picked(monkeypatch, qtbot):
    def pick_si29(self):
        self.pick(ElementChoice("Si", 28.9764947))
        return QDialog.DialogCode.Accepted

    monkeypatch.setattr(PeriodicTableDialog, "exec", pick_si29)
    assert choose_element(None, "Choose an element") == ElementChoice("Si", 28.9764947)


def test_choose_element_returns_none_when_nothing_is_picked(monkeypatch, qtbot):
    monkeypatch.setattr(PeriodicTableDialog, "exec", lambda self: QDialog.DialogCode.Rejected)
    assert choose_element(None, "Choose an element") is None
