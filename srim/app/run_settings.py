"""The Run settings tab: the calculation, the seed, the description and the run options.

The tab is a pure function of the document: every widget is set from it on ``changed``, with
signals blocked, and an edit goes through :meth:`DocumentModel.edit` (one undo step). A setting
the document leaves out shows its :data:`~srim.spec.SETTINGS_SCHEMA` default. Choosing a value
equal to that default removes the key from ``settings`` (and an empty ``settings`` mapping goes
with it); a setting the user did not touch is left as it is, and so is any key the tab does not
show (``plot_mode``, a calculation 4 to 7 keeps its number). The library judges the values, so a
description with a double quote is stored and its message shows in the status bar.

``focus_target("<key>")`` is how a status bar click reaches a field; :attr:`RunSettingsTab.widgets`
holds the widgets by key.
"""

from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDoubleSpinBox,
    QFormLayout,
    QLineEdit,
    QSpinBox,
    QWidget,
)

from srim.app.document import DocumentModel
from srim.spec import SETTINGS_SCHEMA

#: the calculations the tab offers, by number
CALCULATIONS = {
    1: "Quick (Kinchin-Pease damage)",
    2: "Full damage cascades",
    3: "Monolayer collision steps (surface sputtering)",
}
#: calculations 4 to 7 are not offered; a setup that has one shows it as this item
LOADED_CALCULATIONS = range(4, 8)
COLLISIONS = {
    0: "No collision details",
    1: "Ions (COLLISON.txt)",
    2: "Ions and recoils (COLLISON.txt)",
}
#: the output files that are written when ticked, by setting key
OUTPUT_FLAGS = (
    ("ranges", "Ranges (RANGE_3D.txt)"),
    ("backscattered", "Backscattered ions (BACKSCAT.txt)"),
    ("transmit", "Transmitted ions (TRANSMIT.txt)"),
    ("sputtered", "Sputtered atoms (SPUTTER.txt)"),
)
MAX_INT = 2147483647
OFF_TEXT = "Off"


def _setting(doc: dict, key: str):
    """The value ``key`` has in the document, or its schema default when the setting is absent."""
    settings = doc.get("settings")
    if isinstance(settings, dict) and key in settings:
        return settings[key]
    return SETTINGS_SCHEMA[key][1]


def _store_setting(doc: dict, key: str, value) -> None:
    """Store ``value`` for ``key``; a value equal to the default removes the key."""
    settings = doc.get("settings")
    settings = dict(settings) if isinstance(settings, dict) else {}
    if value == SETTINGS_SCHEMA[key][1]:
        settings.pop(key, None)
    else:
        settings[key] = value
    if settings:
        doc["settings"] = settings
    else:
        doc.pop("settings", None)


def _quietly(widget: QWidget, setter, value) -> None:
    """Set a widget from the document without it emitting an edit."""
    widget.blockSignals(True)
    try:
        setter(value)
    finally:
        widget.blockSignals(False)


def _clamped(value, low: int, high: int) -> int | None:
    if isinstance(value, bool) or not isinstance(value, int):
        return None
    return min(max(value, low), high)


class RunSettingsTab(QWidget):
    """The run settings editor (see the module docstring)."""

    def __init__(self, document: DocumentModel, parent=None):
        super().__init__(parent)
        self.document = document
        self.widgets: dict[str, QWidget] = {}

        self.calculation = QComboBox(self)
        self.calculation.currentIndexChanged.connect(self._on_calculation)
        self.widgets["calculation"] = self.calculation

        self.seed = QSpinBox(self)
        self.seed.setRange(0, MAX_INT)
        self.seed.setKeyboardTracking(False)
        self.seed.valueChanged.connect(self._on_seed)
        self.widgets["seed"] = self.seed

        self.description = QLineEdit(self)
        self.description.editingFinished.connect(self._on_description)
        self.widgets["description"] = self.description

        self.flags: dict[str, QCheckBox] = {}
        for key, text in OUTPUT_FLAGS:
            box = QCheckBox(text, self)
            box.toggled.connect(lambda checked, k=key: self._on_flag(k, checked))
            self.flags[key] = box
            self.widgets[key] = box

        self.collisions = QComboBox(self)
        for value, text in COLLISIONS.items():
            self.collisions.addItem(text, value)
        self.collisions.currentIndexChanged.connect(self._on_collisions)
        self.widgets["collisions"] = self.collisions

        self.exyz = self._int_spin("exyz", " eV")
        self.bragg = QDoubleSpinBox(self)
        self.bragg.setRange(0.0, 10.0)
        self.bragg.setDecimals(2)
        self.bragg.setSingleStep(0.01)
        self.bragg.setKeyboardTracking(False)
        self.bragg.valueChanged.connect(lambda value: self._on_float("bragg_correction", value))
        self.widgets["bragg_correction"] = self.bragg
        self.autosave = self._int_spin("autosave", "")

        form = QFormLayout(self)
        form.addRow("Calculation", self.calculation)
        form.addRow("Random seed", self.seed)
        form.addRow("Description", self.description)
        for key, _text in OUTPUT_FLAGS:
            form.addRow(self.flags[key])
        form.addRow("Collisions", self.collisions)
        form.addRow("EXYZ.txt from", self.exyz)
        form.addRow("Bragg correction", self.bragg)
        form.addRow("Autosave every", self.autosave)

        document.changed.connect(self._refresh)
        self._refresh()

    def _int_spin(self, key: str, suffix: str) -> QSpinBox:
        spin = QSpinBox(self)
        spin.setRange(0, MAX_INT)
        spin.setSpecialValueText(OFF_TEXT)
        spin.setSuffix(suffix)
        spin.setKeyboardTracking(False)
        spin.valueChanged.connect(lambda value, k=key: self._on_int(k, value))
        self.widgets[key] = spin
        return spin

    def focus_target(self, rest: str) -> None:
        """Focus the field a status bar line names (``rest`` is the part after ``settings/``)."""
        widget = self.widgets.get(rest)
        if widget is not None:
            widget.setFocus()

    # --- showing the document -----------------------------------------------------------

    def _refresh(self) -> None:
        doc = self.document.doc
        self._show_calculation(doc.get("calculation"))
        seed = _clamped(doc.get("seed"), 0, MAX_INT)
        if seed is not None:
            _quietly(self.seed, self.seed.setValue, seed)
        text = _setting(doc, "description")
        if isinstance(text, str) and text != self.description.text():
            _quietly(self.description, self.description.setText, text)
        for key, box in self.flags.items():
            _quietly(box, box.setChecked, bool(_setting(doc, key)))
        index = self.collisions.findData(_setting(doc, "collisions"))
        _quietly(self.collisions, self.collisions.setCurrentIndex, index)
        for spin, key in ((self.exyz, "exyz"), (self.autosave, "autosave")):
            value = _clamped(_setting(doc, key), 0, MAX_INT)
            if value is not None:
                _quietly(spin, spin.setValue, value)
        bragg = _setting(doc, "bragg_correction")
        if isinstance(bragg, (int, float)) and not isinstance(bragg, bool):
            _quietly(self.bragg, self.bragg.setValue, float(bragg))

    def _show_calculation(self, calculation) -> None:
        """The offered calculations, plus the loaded one (4 to 7) while the setup has it."""
        loaded = calculation in LOADED_CALCULATIONS
        wanted = list(CALCULATIONS) + ([calculation] if loaded else [])
        have = [self.calculation.itemData(i) for i in range(self.calculation.count())]
        if wanted != have:
            self.calculation.blockSignals(True)
            self.calculation.clear()
            for value in wanted:
                text = CALCULATIONS.get(value, f"SRIM calculation {value}")
                self.calculation.addItem(text, value)
            self.calculation.blockSignals(False)
        index = self.calculation.findData(calculation)
        _quietly(self.calculation, self.calculation.setCurrentIndex, index)

    # --- the edits ---------------------------------------------------------------------

    def _on_calculation(self, index: int) -> None:
        value = self.calculation.itemData(index)
        if value is None or value == self.document.doc.get("calculation"):
            return
        self.document.edit(lambda doc: doc.__setitem__("calculation", int(value)))

    def _on_seed(self, value: int) -> None:
        if self.document.doc.get("seed") == value:
            return
        self.document.edit(lambda doc: doc.__setitem__("seed", int(value)))

    def _on_description(self) -> None:
        text = self.description.text()
        if _setting(self.document.doc, "description") == text:
            return
        self.document.edit(lambda doc: _store_setting(doc, "description", text))

    def _on_flag(self, key: str, checked: bool) -> None:
        value = 1 if checked else 0
        if _setting(self.document.doc, key) == value:
            return
        self.document.edit(lambda doc: _store_setting(doc, key, value))

    def _on_collisions(self, index: int) -> None:
        value = self.collisions.itemData(index)
        if value is None or value == _setting(self.document.doc, "collisions"):
            return
        self.document.edit(lambda doc: _store_setting(doc, "collisions", int(value)))

    def _on_int(self, key: str, value: int) -> None:
        if _setting(self.document.doc, key) == value:
            return
        self.document.edit(lambda doc: _store_setting(doc, key, int(value)))

    def _on_float(self, key: str, value: float) -> None:
        if _setting(self.document.doc, key) == value:
            return
        self.document.edit(lambda doc: _store_setting(doc, key, float(value)))
