"""The Scan tab: the scan of the run setup (the document's ``_scan``), through the document.

The tab is a pure function of the document. Each value it shows is set from the document with
signals blocked, and each edit is one call of :meth:`DocumentModel.edit` (one undo step). An edit
that would change nothing is skipped. "Run as a scan" adds :func:`~srim.app.scan_model.new_scan`
and unticking it removes the scan. The library judges every value: a number the tab cannot read
is refused (its text stays), and anything else goes to the document as typed (a blank is missing).

The ions per point belong to the scan; the Beam tab's number of ions is the single run's. Explicit
``points:``, a ``python:`` extractor and any extractor the tab does not offer are kept and shown as
read-only lines. ``keep_seed`` is shown as a line and never changed here.

``focus_target("<field>")`` is how a status bar click reaches a field: ``vary``, ``ions``,
``precision``, ``extract``, ``seed``, ``batch_ions`` or ``keep_seed``.
"""

import re
from collections.abc import Mapping
from copy import deepcopy
from dataclasses import dataclass
from typing import Any

from PySide6.QtCore import QRegularExpression
from PySide6.QtGui import QRegularExpressionValidator
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QDoubleSpinBox,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QHeaderView,
    QInputDialog,
    QLabel,
    QLineEdit,
    QPushButton,
    QSpinBox,
    QTableWidget,
    QVBoxLayout,
    QWidget,
)

from srim.app.document import DocumentModel
from srim.app.numbers import parse_number
from srim.app.scan_model import new_scan, scan_keys, values_from_range
from srim.scan.extract import available
from srim.scan.spec import normalise_key

#: the combo item that asks for a parameter by typed key
OTHER = "other"
FIXED = "fixed"
PRECISION = "precision"
MODE_CHOICES = (("Fixed number of ions", FIXED), ("Precision target", PRECISION))
#: the defaults of a precision target (a value equal to its default is left out of the file)
PRECISION_DEFAULTS = {"min_ions": 1000, "max_ions": 100_000, "batch_ions": 1000}
#: the extractors with a parameter, and the name of that parameter
PARAMETERS = {"vacancy_at": "depth_nm", "ions_in_layer": "layer_index"}
#: the value names each extractor writes, for the suggestions of the precision target
TARGET_NAMES = {
    "implant_summary": (
        "n_ions_stopped",
        "mean_depth_A",
        "straggle_depth_A",
        "lateral_range_A",
        "lateral_straggle_A",
        "radial_range_A",
        "radial_straggle_A",
        "skewness",
        "kurtosis",
    ),
    "peak_vacancy_depth": ("peak_vacancy_depth_A",),
    "sputter_yield": ("sputter_yield_atoms_per_ion",),
    "backscattered_fraction": ("backscattered_fraction",),
    "transmitted_fraction": ("transmitted_fraction",),
    "energy_to_recoils": ("energy_to_recoils_eV_per_ion",),
}
MAX_INT = 2147483647
NUMBER_PATTERN = r"[-+0-9.,eE]*"
SEPARATORS = re.compile(r"[\s,;]+")
PARAMETER_PROMPT = "Parameter (for example ion.energy_keV or target.0.thickness_nm):"


def _quietly(widget: QWidget, setter, value) -> None:
    """Set a widget from the document without it emitting an edit."""
    widget.blockSignals(True)
    try:
        setter(value)
    finally:
        widget.blockSignals(False)


def _float_spin(parent: QWidget, low: float, high: float) -> QDoubleSpinBox:
    spin = QDoubleSpinBox(parent)
    spin.setRange(low, high)
    spin.setDecimals(6)
    spin.setKeyboardTracking(False)
    return spin


def _int_spin(parent: QWidget, low: int, high: int) -> QSpinBox:
    spin = QSpinBox(parent)
    spin.setRange(low, high)
    spin.setKeyboardTracking(False)
    return spin


def _is_int(value: Any) -> bool:
    return isinstance(value, int) and not isinstance(value, bool)


def _is_number(value: Any) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool)


def _number_text(value: Any) -> str:
    if _is_number(value):
        return f"{value:.10g}"
    return "" if value is None else str(value)


def _format_values(values: Any) -> str:
    """The values as the Values cell shows them: ``10, 20``; a missing list is blank."""
    if not isinstance(values, list):
        return ""
    return ", ".join(_number_text(value) for value in values)


def _parse_values(text: str) -> list[float] | None:
    """The numbers in ``text`` (separated by commas, semicolons or spaces); None when blank.

    Raises :class:`ValueError` naming the first entry that is not a number.
    """
    tokens = [token for token in SEPARATORS.split(text.strip()) if token]
    if not tokens:
        return None
    return [parse_number(token) for token in tokens]


def _entry_name(entry: Any) -> str | None:
    """The name of an ``extract`` entry: the string itself, or the key of a one-key mapping."""
    if isinstance(entry, str):
        return entry
    if isinstance(entry, Mapping) and len(entry) == 1:
        return next(iter(entry))
    return None


def _entry_params(entry: Any) -> Mapping:
    """The parameters of a one-key mapping entry (empty for a name)."""
    if isinstance(entry, Mapping) and len(entry) == 1:
        params = next(iter(entry.values()))
        return params if isinstance(params, Mapping) else {}
    return {}


def _new_entry(name: str) -> Any:
    """The ``extract`` entry a ticked extractor adds: a name, or a mapping with a missing value."""
    if name in PARAMETERS:
        return {name: {PARAMETERS[name]: None}}
    return name


def _target_names(extract: list) -> list[str]:
    """The value names the ticked extractors write (the suggestions of the precision target)."""
    names: list[str] = []
    for entry in extract:
        name = _entry_name(entry)
        params = _entry_params(entry)
        if name in TARGET_NAMES:
            names.extend(TARGET_NAMES[name])
        if name == "vacancy_at" and _is_number(params.get("depth_nm")):
            names.append(f"vacancies_per_A_per_ion_at_{float(params['depth_nm']):g}nm")
        if name == "ions_in_layer" and _is_int(params.get("layer_index")):
            names.append(f"fraction_of_stopped_ions_in_layer_{params['layer_index']}")
    return list(dict.fromkeys(names))


class RangeDialog(QDialog):
    """A range of values: its start and stop, how many values, and linear or logarithmic steps."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Range of values")
        self.start = _float_spin(self, -1e9, 1e9)
        self.stop = _float_spin(self, -1e9, 1e9)
        self.stop.setValue(10.0)
        self.steps = _int_spin(self, 2, 1000)
        self.steps.setValue(5)
        self.log = QCheckBox("Logarithmic steps (both ends above zero)", self)
        self.message = QLabel(self)
        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel, self
        )
        buttons.accepted.connect(self._accept)
        buttons.rejected.connect(self.reject)
        form = QFormLayout(self)
        form.addRow("Start", self.start)
        form.addRow("Stop", self.stop)
        form.addRow("Number of values", self.steps)
        form.addRow(self.log)
        form.addRow(self.message)
        form.addRow(buttons)

    def values(self) -> list[float]:
        """The values of the range. Raises :class:`ValueError` for a range that is refused."""
        return values_from_range(
            self.start.value(), self.stop.value(), self.steps.value(), self.log.isChecked()
        )

    def _accept(self) -> None:
        try:
            self.values()
        except ValueError as error:
            self.message.setText(str(error))
            return
        self.accept()


@dataclass(eq=False)
class _Row:
    """One row of the vary table: its parameter, the cell widgets and the text last shown."""

    name: str
    combo: QComboBox
    edit: QLineEdit
    range_button: QPushButton
    shown: str | None = None


class ScanTab(QWidget):
    """The scan editor (see the module docstring)."""

    def __init__(self, document: DocumentModel, parent=None):
        super().__init__(parent)
        self.document = document
        self.rows: list[_Row] = []
        self.kept_lines: list[QLabel] = []
        self._kept_shown: list[str] = []
        self._target_shown: list[str] = []

        self.enabled_box = QCheckBox("Run as a scan", self)
        self.enabled_box.toggled.connect(self._on_enabled)

        self.body = QWidget(self)
        body = QVBoxLayout(self.body)
        body.setContentsMargins(0, 0, 0, 0)

        # the parameters to vary
        self.table = QTableWidget(0, 2, self.body)
        self.table.setHorizontalHeaderLabels(["Parameter", "Values"])
        self.table.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
        self.table.verticalHeader().setVisible(False)
        self.table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self.table.setSelectionMode(QTableWidget.SelectionMode.SingleSelection)
        self.table.setToolTip("Values are separated by commas, semicolons or spaces.")
        self.add_button = QPushButton("Add row", self.body)
        self.add_button.clicked.connect(lambda _checked=False: self.add_row())
        self.remove_button = QPushButton("Remove row", self.body)
        self.remove_button.clicked.connect(lambda _checked=False: self.remove_row())
        vary_buttons = QHBoxLayout()
        vary_buttons.addWidget(self.add_button)
        vary_buttons.addWidget(self.remove_button)
        vary_buttons.addStretch(1)
        vary = QGroupBox("Parameters to vary", self.body)
        vary_layout = QVBoxLayout(vary)
        vary_layout.addWidget(self.table)
        vary_layout.addLayout(vary_buttons)
        body.addWidget(vary)

        # the ions per point
        self.note = QLabel(
            "Ions per point are separate from the number of ions on the Beam tab.", self.body
        )
        self.note.setWordWrap(True)
        self.mode = QComboBox(self.body)
        for text, data in MODE_CHOICES:
            self.mode.addItem(text, data)
        self.mode.currentIndexChanged.connect(self._on_mode)
        self.ions = _int_spin(self.body, 0, MAX_INT)
        self.ions.setSpecialValueText("")
        self.ions.valueChanged.connect(self._on_ions)
        self.batch_ions = _int_spin(self.body, 0, MAX_INT)
        self.batch_ions.setSpecialValueText("One run per point")
        self.batch_ions.valueChanged.connect(self._on_batch_ions)
        self.precision_target = QComboBox(self.body)
        self.precision_target.setEditable(True)
        self.precision_target.setToolTip("The value the precision is judged on.")
        self.precision_target.activated.connect(lambda _index: self._on_target())
        self.precision_target.lineEdit().editingFinished.connect(self._on_target)
        self.rel = _float_spin(self.body, 0.0, 1e9)
        self.rel.setSpecialValueText("Not set")
        self.rel.valueChanged.connect(lambda value: self._on_precision_float("rel", value))
        self.abs = _float_spin(self.body, 0.0, 1e9)
        self.abs.setSpecialValueText("Not set")
        self.abs.valueChanged.connect(lambda value: self._on_precision_float("abs", value))
        self.min_ions = _int_spin(self.body, 1, MAX_INT)
        self.min_ions.valueChanged.connect(lambda value: self._on_precision_int("min_ions", value))
        self.max_ions = _int_spin(self.body, 1, MAX_INT)
        self.max_ions.valueChanged.connect(lambda value: self._on_precision_int("max_ions", value))
        self.precision_batch = _int_spin(self.body, 1, MAX_INT)
        self.precision_batch.valueChanged.connect(
            lambda value: self._on_precision_int("batch_ions", value)
        )
        self._precision_widgets = (
            self.precision_target,
            self.rel,
            self.abs,
            self.min_ions,
            self.max_ions,
            self.precision_batch,
        )
        ions_form = QFormLayout()
        ions_form.addRow("Ions", self.mode)
        ions_form.addRow("Ions per point", self.ions)
        ions_form.addRow("Batch of ions", self.batch_ions)
        ions_form.addRow("Precision target", self.precision_target)
        ions_form.addRow("Relative error", self.rel)
        ions_form.addRow("Absolute error", self.abs)
        ions_form.addRow("Minimum ions", self.min_ions)
        ions_form.addRow("Maximum ions", self.max_ions)
        ions_form.addRow("Ions per batch", self.precision_batch)
        ions = QGroupBox("Ions per point", self.body)
        ions_layout = QVBoxLayout(ions)
        ions_layout.addWidget(self.note)
        ions_layout.addLayout(ions_form)
        body.addWidget(ions)

        # the values extracted from each point
        self.extract_boxes: dict[str, QCheckBox] = {}
        extract_layout = QVBoxLayout()
        for name in available():
            box = QCheckBox(name, self.body)
            box.toggled.connect(lambda checked, n=name: self._on_extract(n, checked))
            self.extract_boxes[name] = box
            extract_layout.addWidget(box)
        self.vacancy_depth = QLineEdit(self.body)
        self.vacancy_depth.setValidator(
            QRegularExpressionValidator(QRegularExpression(NUMBER_PATTERN), self.body)
        )
        self.vacancy_depth.editingFinished.connect(self._on_vacancy_depth)
        self.layer_index = QLineEdit(self.body)
        self.layer_index.setValidator(
            QRegularExpressionValidator(QRegularExpression(NUMBER_PATTERN), self.body)
        )
        self.layer_index.editingFinished.connect(self._on_layer_index)
        parameter_form = QFormLayout()
        parameter_form.addRow("vacancy_at depth (nm)", self.vacancy_depth)
        parameter_form.addRow("ions_in_layer layer (0 = surface)", self.layer_index)
        self._kept_layout = QVBoxLayout()
        extract = QGroupBox("Values from each point", self.body)
        extract_group = QVBoxLayout(extract)
        extract_group.addLayout(extract_layout)
        extract_group.addLayout(parameter_form)
        extract_group.addLayout(self._kept_layout)
        body.addWidget(extract)

        # the seed
        self.seed = _int_spin(self.body, -1, MAX_INT)
        self.seed.setSpecialValueText("")
        self.seed.setToolTip("Blank: the scan draws each point's seed.")
        self.seed.valueChanged.connect(self._on_seed)
        self.keep_label = QLabel("Runs the setup's own seed (one point)", self.body)
        seed_form = QFormLayout()
        seed_form.addRow("Random seed", self.seed)
        seed_form.addRow(self.keep_label)
        body.addLayout(seed_form)

        outer = QVBoxLayout(self)
        outer.addWidget(self.enabled_box)
        outer.addWidget(self.body)

        document.changed.connect(self._refresh)
        self._refresh()

    def focus_target(self, rest: str) -> None:
        """Focus the field a status bar line names (``rest`` is the part after ``scan/``)."""
        widget = {
            "vary": self.table,
            "ions": self.ions,
            "precision": self.precision_target,
            "extract": next(iter(self.extract_boxes.values()), None),
            "seed": self.seed,
            "batch_ions": self.batch_ions,
            "keep_seed": self.enabled_box,
        }.get(rest)
        if widget is not None:
            widget.setFocus()

    # --- showing the document -----------------------------------------------------------

    def _refresh(self) -> None:
        doc = self.document.doc
        scan = doc.get("_scan")
        shown = scan if scan is not None else new_scan()
        _quietly(self.enabled_box, self.enabled_box.setChecked, scan is not None)
        self.body.setEnabled(scan is not None)
        self._show_vary(shown.get("grid") or {}, doc)
        self._show_ions(shown)
        self._show_extract(shown)
        seed = shown.get("seed")
        _quietly(self.seed, self.seed.setValue, seed if _is_int(seed) and seed >= 0 else -1)
        self.keep_label.setVisible(bool(shown.get("keep_seed")))

    def _show_vary(self, grid: dict, doc: dict) -> None:
        names = list(grid)
        if [row.name for row in self.rows] != names:
            self._rebuild_rows(names, scan_keys(doc))
        for row in self.rows:
            _quietly(row.combo, row.combo.setCurrentIndex, row.combo.findData(row.name))
            text = _format_values(grid.get(row.name))
            if text != row.shown:
                row.shown = text
                _quietly(row.edit, row.edit.setText, text)

    def _rebuild_rows(self, names: list[str], keys: list[tuple[str, str]]) -> None:
        """Build the vary table again for ``names`` (the order of the grid)."""
        self.table.setRowCount(0)
        self.rows = []
        known = dict(keys)
        for index, name in enumerate(names):
            combo = QComboBox(self.table)
            for key, label in keys:
                combo.addItem(label, key)
            if name not in known:
                combo.addItem(name, name)
            combo.addItem("Other…", OTHER)
            edit = QLineEdit(self.table)
            range_button = QPushButton("Range…", self.table)
            holder = QWidget(self.table)
            holder_layout = QHBoxLayout(holder)
            holder_layout.setContentsMargins(0, 0, 0, 0)
            holder_layout.addWidget(edit, 1)
            holder_layout.addWidget(range_button)
            row = _Row(name, combo, edit, range_button)
            self.rows.append(row)
            self.table.insertRow(index)
            self.table.setCellWidget(index, 0, combo)
            self.table.setCellWidget(index, 1, holder)
            combo.currentIndexChanged.connect(lambda i, r=row: self._on_parameter(r, i))
            edit.editingFinished.connect(lambda r=row: self._on_values(r))
            range_button.clicked.connect(lambda _checked=False, r=row: self._on_range(r))

    def _show_ions(self, scan: dict) -> None:
        precision = scan.get("precision")
        mode = PRECISION if precision is not None else FIXED
        _quietly(self.mode, self.mode.setCurrentIndex, self.mode.findData(mode))
        ions = scan.get("ions")
        _quietly(self.ions, self.ions.setValue, ions if _is_int(ions) and ions > 0 else 0)
        batch = scan.get("batch_ions")
        _quietly(self.batch_ions, self.batch_ions.setValue, batch if _is_int(batch) else 0)
        precision = precision if isinstance(precision, Mapping) else {}
        self._show_target(scan)
        for spin, key in ((self.rel, "rel"), (self.abs, "abs")):
            value = precision.get(key)
            _quietly(spin, spin.setValue, float(value) if _is_number(value) else 0.0)
        for spin, key in (
            (self.min_ions, "min_ions"),
            (self.max_ions, "max_ions"),
            (self.precision_batch, "batch_ions"),
        ):
            value = precision.get(key)
            _quietly(
                spin,
                spin.setValue,
                value if _is_int(value) else PRECISION_DEFAULTS[key],
            )
        fixed = mode == FIXED
        self.ions.setEnabled(fixed)
        self.batch_ions.setEnabled(fixed)
        for widget in self._precision_widgets:
            widget.setEnabled(not fixed)

    def _show_target(self, scan: dict) -> None:
        names = _target_names(scan.get("extract") or [])
        if names != self._target_shown:
            self._target_shown = names
            self.precision_target.blockSignals(True)
            try:
                self.precision_target.clear()
                for name in names:
                    self.precision_target.addItem(name)
            finally:
                self.precision_target.blockSignals(False)
        precision = scan.get("precision")
        target = precision.get("target") if isinstance(precision, Mapping) else None
        text = target if isinstance(target, str) else ""
        if self.precision_target.currentText() != text:
            _quietly(self.precision_target, self.precision_target.setEditText, text)

    def _show_extract(self, scan: dict) -> None:
        entries = scan.get("extract") or []
        by_name = {}
        for entry in entries:
            name = _entry_name(entry)
            if name is not None and name not in by_name:
                by_name[name] = entry
        for name, box in self.extract_boxes.items():
            _quietly(box, box.setChecked, name in by_name)
        depth = by_name.get("vacancy_at")
        layer = by_name.get("ions_in_layer")
        self.vacancy_depth.setEnabled(depth is not None)
        self.layer_index.setEnabled(layer is not None)
        depth_value = _entry_params(depth).get("depth_nm") if depth is not None else None
        layer_value = _entry_params(layer).get("layer_index") if layer is not None else None
        _quietly(
            self.vacancy_depth,
            self.vacancy_depth.setText,
            _number_text(depth_value),
        )
        _quietly(self.layer_index, self.layer_index.setText, _number_text(layer_value))
        self._show_kept(scan)

    def _show_kept(self, scan: dict) -> None:
        """Read-only lines for the points and for the entries the tab does not offer."""
        texts = []
        points = scan.get("points")
        if points is not None:
            if isinstance(points, list):
                count = len(points)
                noun = "point" if count == 1 else "points"
                texts.append(f"{count} explicit {noun} from the file")
            else:
                texts.append("Explicit points from the file")
        for entry in scan.get("extract") or []:
            if _entry_name(entry) not in self.extract_boxes:
                texts.append(str(entry))
        if texts == self._kept_shown:
            return
        self._kept_shown = texts
        for label in self.kept_lines:
            label.deleteLater()
        self.kept_lines = []
        for text in texts:
            label = QLabel(text, self.body)
            label.setWordWrap(True)
            self._kept_layout.addWidget(label)
            self.kept_lines.append(label)

    # --- the edits ---------------------------------------------------------------------

    def _edit(self, fn) -> None:
        """Apply ``fn`` to the scan as one undo step, unless it changes nothing.

        ``fn`` takes the scan (the document's ``_scan``) and must be free of side effects, since it
        runs once on a copy to see whether anything changes.
        """
        scan = self.document.doc.get("_scan")
        if scan is None:
            return
        trial = deepcopy(scan)
        fn(trial)
        if trial == scan:
            return
        self.document.edit(lambda doc: fn(doc["_scan"]))

    def _on_enabled(self, checked: bool) -> None:
        if (self.document.doc.get("_scan") is not None) == checked:
            return

        def fn(doc: dict) -> None:
            if checked:
                doc["_scan"] = new_scan()
            else:
                doc.pop("_scan", None)

        self.document.edit(fn)

    def _on_mode(self, index: int) -> None:
        mode = self.mode.itemData(index)

        def fn(scan: dict) -> None:
            if mode == PRECISION:
                scan.pop("ions", None)
                if scan.get("precision") is None:
                    scan["precision"] = {}
            else:
                scan.pop("precision", None)
                scan.setdefault("ions", None)

        self._edit(fn)

    def _on_ions(self, value: int) -> None:
        self._edit(lambda scan: scan.__setitem__("ions", value if value else None))

    def _on_batch_ions(self, value: int) -> None:
        self._edit(lambda scan: scan.__setitem__("batch_ions", value if value else None))

    def _on_seed(self, value: int) -> None:
        self._edit(lambda scan: scan.__setitem__("seed", value if value >= 0 else None))

    def _on_target(self) -> None:
        text = self.precision_target.currentText().strip()

        def fn(scan: dict) -> None:
            if scan.get("precision") is not None:
                scan["precision"] = {**scan["precision"], "target": text}

        self._edit(fn)

    def _on_precision_float(self, key: str, value: float) -> None:
        self._set_precision(key, value if value else None, None)

    def _on_precision_int(self, key: str, value: int) -> None:
        self._set_precision(key, value, PRECISION_DEFAULTS[key])

    def _set_precision(self, key: str, value: Any, default: Any) -> None:
        """Store ``value`` under ``key`` of the precision target.

        None or the default is left out.
        """

        def fn(scan: dict) -> None:
            if scan.get("precision") is None:
                return
            precision = dict(scan["precision"])
            if value is None or value == default:
                precision.pop(key, None)
            else:
                precision[key] = value
            scan["precision"] = precision

        self._edit(fn)

    def _on_extract(self, name: str, checked: bool) -> None:
        def fn(scan: dict) -> None:
            entries = list(scan.get("extract") or [])
            present = any(_entry_name(entry) == name for entry in entries)
            if checked:
                if not present:
                    entries.append(_new_entry(name))
            else:
                entries = [entry for entry in entries if _entry_name(entry) != name]
            scan["extract"] = entries

        self._edit(fn)

    def _set_parameter(self, name: str, param: str, value: Any) -> None:
        """Store ``value`` as the parameter of the ticked extractor ``name``."""

        def fn(scan: dict) -> None:
            entries = list(scan.get("extract") or [])
            for index, entry in enumerate(entries):
                if _entry_name(entry) == name and isinstance(entry, Mapping):
                    params = dict(_entry_params(entry))
                    params[param] = value
                    entries[index] = {name: params}
            scan["extract"] = entries

        self._edit(fn)

    def _on_vacancy_depth(self) -> None:
        try:
            value = parse_number(self.vacancy_depth.text())
        except ValueError:
            return
        self._set_parameter("vacancy_at", "depth_nm", value)

    def _on_layer_index(self) -> None:
        try:
            value = parse_number(self.layer_index.text())
        except ValueError:
            return
        if value is not None:
            if not value.is_integer():
                return
            value = int(value)
        self._set_parameter("ions_in_layer", "layer_index", value)

    def _on_parameter(self, row: _Row, index: int) -> None:
        key = row.combo.itemData(index)
        if key == OTHER:
            text, ok = QInputDialog.getText(self, "Parameter", PARAMETER_PROMPT)
            key = normalise_key(text) if ok else ""
            if not key:
                self._refresh()
                return
        if key and key != row.name:
            self._rename(row.name, key)
        self._refresh()

    def _rename(self, old: str, new: str) -> None:
        def fn(scan: dict) -> None:
            grid = scan.get("grid") or {}
            if new in grid:
                return
            scan["grid"] = {(new if key == old else key): value for key, value in grid.items()}

        self._edit(fn)

    def _on_values(self, row: _Row) -> None:
        text = row.edit.text()
        if text == row.shown:
            return
        try:
            values = _parse_values(text)
        except ValueError as error:
            row.edit.setToolTip(str(error))
            return
        row.edit.setToolTip("")

        def fn(scan: dict) -> None:
            grid = scan.get("grid") or {}
            if row.name in grid:
                grid[row.name] = None if values is None else list(values)
            scan["grid"] = grid

        self._edit(fn)
        row.shown = _format_values(values)
        _quietly(row.edit, row.edit.setText, row.shown)

    def _on_range(self, row: _Row) -> None:
        dialog = RangeDialog(self)
        try:
            if dialog.exec() != QDialog.DialogCode.Accepted:
                return
            values = dialog.values()
        finally:
            dialog.deleteLater()

        def fn(scan: dict) -> None:
            grid = scan.get("grid") or {}
            if row.name in grid:
                grid[row.name] = list(values)
            scan["grid"] = grid

        self._edit(fn)

    def add_row(self) -> None:
        """Add a parameter with no values yet: the first one of the list that the grid lacks."""
        scan = self.document.doc.get("_scan")
        if scan is None:
            return
        grid = scan.get("grid") or {}
        free = [key for key, _label in scan_keys(self.document.doc) if key not in grid]
        if not free:
            return
        name = free[0]

        def fn(scan: dict) -> None:
            grid = scan.get("grid") or {}
            grid.setdefault(name, None)
            scan["grid"] = grid

        self._edit(fn)

    def remove_row(self) -> None:
        """Remove the selected parameter from the grid."""
        index = self.table.currentRow()
        if not 0 <= index < len(self.rows):
            return
        name = self.rows[index].name

        def fn(scan: dict) -> None:
            grid = scan.get("grid") or {}
            grid.pop(name, None)
            scan["grid"] = grid

        self._edit(fn)
