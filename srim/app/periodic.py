"""The periodic table dialog: one button per element, and an isotope menu on each button.

The first entry of an element's menu is its natural mass (SRIM's default, no ``mass_amu``);
the others are its natural isotopes, most abundant first. Choosing an entry stores it in
``PeriodicTableDialog.choice`` and closes the dialog. :func:`choose_element` runs it.
"""

from PySide6.QtCore import QPoint
from PySide6.QtWidgets import QDialog, QGridLayout, QMenu, QPushButton

from srim.app.elements import MAX_Z, ElementChoice, grid_position, text_color
from srim.core.elementdb import ElementDB
from srim.core.isotopes import isotope_label, stable_isotopes


def isotope_menu(symbol, parent):
    """The menu of an element: its natural mass, then one entry per natural isotope

    Choosing an entry calls ``parent.pick`` with the :class:`ElementChoice` for it.
    """
    element = ElementDB.lookup(symbol)
    menu = QMenu(parent)
    natural = menu.addAction(f"Natural ({element['mass']:.4f} amu)")
    natural.triggered.connect(lambda *_: parent.pick(ElementChoice(symbol, None)))
    for isotope in stable_isotopes(symbol):
        text = (
            f"{isotope_label(isotope)}  {isotope.mass_amu:.6f} amu"
            f"  ({isotope.abundance_percent:.4g} %)"
        )
        action = menu.addAction(text)
        choice = ElementChoice(symbol, isotope.mass_amu)
        action.triggered.connect(lambda *_, choice=choice: parent.pick(choice))
    return menu


class PeriodicTableDialog(QDialog):
    """The periodic table, Z = 1 to 92; ``choice`` is set when an isotope is picked."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.choice = None
        self.setWindowTitle("Periodic table")
        layout = QGridLayout(self)
        for z in range(1, MAX_Z + 1):
            element = ElementDB.lookup(z)
            symbol = element["symbol"]
            row, column = grid_position(z)
            button = QPushButton(symbol)
            button.setObjectName(f"element-{symbol}")
            button.setToolTip(f"{element['name']}, Z = {z}")
            background = element["color"]
            button.setStyleSheet(
                f"background-color: {background}; color: {text_color(background)};"
            )
            button.setMinimumSize(44, 40)
            button.clicked.connect(lambda _=False, s=symbol, b=button: self._open_menu(s, b))
            layout.addWidget(button, row, column)

    def _open_menu(self, symbol, button):
        menu = isotope_menu(symbol, self)
        menu.exec(button.mapToGlobal(QPoint(0, button.height())))
        menu.deleteLater()

    def pick(self, choice):
        """Stores the chosen element and mass, and closes the dialog"""
        self.choice = choice
        self.accept()


def choose_element(parent, title):
    """Runs the periodic table dialog; returns an :class:`ElementChoice`, or None if cancelled"""
    dialog = PeriodicTableDialog(parent)
    dialog.setWindowTitle(title)
    dialog.exec()
    return dialog.choice
