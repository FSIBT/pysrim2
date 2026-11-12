"""The Beam tab: the ion, the number of ions, and each beam quantity as a value or a distribution.

:class:`QuantityEditor` edits one quantity (energy, angle, azimuth or a position coordinate). It
shows the kind (a fixed value, one of the distributions, a Python function or a histogram file),
the kind's parameters and an optional "Limit to" range, and emits the value it now stands for:
a number, a ``{kind: params}`` mapping, or None for "Not set" (or a missing value). A number
field that is not a number is refused with the old value kept, and nothing is emitted. A value
the editor does not offer as a kind is shown as YAML and kept until the user picks another kind.

:class:`BeamTab` holds the ion button (the periodic table, with the isotopes), the number of
ions, and one editor per quantity. Each change goes through :meth:`DocumentModel.edit`, so it is
one undo step, and the library judges the values. ``focus_target("<quantity>")`` is how a status
bar click reaches a field.

The mode box (Automatic, Exact or Binned) and the bin counts are shown from the document only:
Automatic stores no ``beam`` key, Exact stores ``{"mode": "exact"}``, and Binned stores
``{"mode": "binned"}`` with the non-zero counts. Each quantity has a :class:`DistributionPlot`
under its editor. matplotlib is imported only by :mod:`srim.app.beam_plot`, and a histogram file
is previewed with it.
"""

import warnings
from collections.abc import Mapping
from copy import deepcopy
from pathlib import Path

import numpy as np
from PySide6.QtCore import QRegularExpression, Signal
from PySide6.QtGui import QRegularExpressionValidator
from PySide6.QtWidgets import (
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFileDialog,
    QFormLayout,
    QFrame,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPlainTextEdit,
    QPushButton,
    QScrollArea,
    QSpinBox,
    QVBoxLayout,
    QWidget,
)

from srim.app.beam_plot import DistributionPlot
from srim.app.document import DocumentModel
from srim.app.elements import ElementChoice, element_label
from srim.app.model import (
    BINNED_TEXT,
    EXACT_TEXT,
    default_beam_mode,
    is_distributed,
    to_spec,
)
from srim.app.numbers import parse_number
from srim.app.periodic import choose_element
from srim.app.quantity import (
    KINDS,
    LABELS,
    PARAMETERS,
    UNSET,
    build,
    describe,
    is_number,
    text_of,
    yaml_text,
)
from srim.beam.binned import make_bins
from srim.beam.shapes import Histogram
from srim.beam.spec import BIN_KEYS, DEFAULT_ANGLE_BINS, DEFAULT_ENERGY_BINS, DEFAULT_MAX_BINS
from srim.spec import SpecError

#: the choice that stands for a value the editor keeps as YAML (it is not a kind)
REPLACE = "replace"
#: the number fields, and the text field of a Python function
NUMBER_FIELDS = ("value", "mean", "sigma", "low", "mode", "high", "mu")
TEXT_FIELDS = ("ref",)
FIELD_LABELS = {
    "value": "Value",
    "mean": "Mean",
    "sigma": "Sigma",
    "low": "Low",
    "mode": "Mode",
    "high": "High",
    "mu": "Mu",
    "ref": "Function",
}
#: characters a number can be typed with; anything else is refused as it is typed
NUMBER_PATTERN = r"[-+0-9.,eE]*"
#: the quantities of the ion, by the key the status bar names them with
POSITION_KEYS = ("y_nm", "z_nm", "depth_nm")
HISTOGRAM_FILTER = "Histogram files (*.txt *.dat *.csv);;All files (*)"
ION_NOT_CHOSEN = "Ion: not chosen"

MODE_LABELS = {"exact": "Exact (TRIM.DAT)", "binned": "Binned"}
SINGLE_RUN_TEXT = "Single energy and angle: one SRIM run."


def _is_zero_or_missing(value) -> bool:
    """True for a Not set coordinate (None) or a plain 0.0 (not a distribution)."""
    return value is None or (is_number(value) and value == 0)


def _number_validator(parent: QWidget) -> QRegularExpressionValidator:
    return QRegularExpressionValidator(QRegularExpression(NUMBER_PATTERN), parent)


class QuantityEditor(QGroupBox):
    """One beam quantity. ``value_changed`` carries the value it now stands for (see module doc).

    ``optional`` quantities offer "Not set" (None); a required one has none, and its None means
    a missing value.
    """

    value_changed = Signal(object)

    def __init__(self, title: str, unit: str, optional: bool, parent=None):
        super().__init__(f"{title} ({unit})" if unit else title, parent)
        self._optional = optional
        self._value = None
        self._file: str | None = None

        self.kind_box = QComboBox(self)
        self.kind_box.currentIndexChanged.connect(self._on_kind_changed)
        self.fields: dict[str, QLineEdit] = {}
        self._rows: dict[str, tuple[QLabel, QWidget]] = {}
        for name in NUMBER_FIELDS:
            edit = QLineEdit(self)
            edit.setValidator(_number_validator(edit))
            edit.editingFinished.connect(self._commit)
            self.fields[name] = edit
            self._rows[name] = (QLabel(FIELD_LABELS[name], self), edit)
        ref = QLineEdit(self)
        ref.setPlaceholderText("python:<module>:<name>")
        ref.editingFinished.connect(self._commit)
        self.fields["ref"] = ref
        self._rows["ref"] = (QLabel(FIELD_LABELS["ref"], self), ref)

        self.file_label = QLabel("No file loaded", self)
        self.load_button = QPushButton("Load…", self)
        self.load_button.clicked.connect(self._load_histogram)
        file_holder = QWidget(self)
        file_row = QHBoxLayout(file_holder)
        file_row.setContentsMargins(0, 0, 0, 0)
        file_row.addWidget(self.file_label, 1)
        file_row.addWidget(self.load_button)
        self._rows["file"] = (QLabel("File", self), file_holder)

        self.limit_low = QLineEdit(self)
        self.limit_high = QLineEdit(self)
        for edit in (self.limit_low, self.limit_high):
            edit.setValidator(_number_validator(edit))
            edit.editingFinished.connect(self._commit)
        limit_holder = QWidget(self)
        limit_row = QHBoxLayout(limit_holder)
        limit_row.setContentsMargins(0, 0, 0, 0)
        limit_row.addWidget(self.limit_low)
        limit_row.addWidget(QLabel("to", self))
        limit_row.addWidget(self.limit_high)
        self._rows["limit"] = (QLabel("Limit to", self), limit_holder)

        self.yaml_view = QPlainTextEdit(self)
        self.yaml_view.setReadOnly(True)
        self.yaml_view.setMaximumHeight(96)
        self._rows["yaml"] = (QLabel("Kept as YAML", self), self.yaml_view)

        form = QFormLayout(self)
        form.addRow("Kind", self.kind_box)
        for name in (*NUMBER_FIELDS, *TEXT_FIELDS, "file", "limit", "yaml"):
            label, widget = self._rows[name]
            form.addRow(label, widget)

        self.set_value(None)

    # --- the value ---------------------------------------------------------------------

    @property
    def value(self):
        """The value the editor stands for (a copy)."""
        return deepcopy(self._value)

    def set_value(self, value) -> None:
        """Show ``value`` without emitting ``value_changed``."""
        value = deepcopy(value)
        if value == self._value and self.kind_box.count():
            return
        self._value = value
        shown = describe(value, self._optional)
        self.kind_box.blockSignals(True)
        self._fill_kinds(replace=not shown.editable)
        key = shown.kind if shown.editable else REPLACE
        self.kind_box.setCurrentIndex(self.kind_box.findData(key))
        self.kind_box.blockSignals(False)
        if not shown.editable:
            self._clear_fields()
            self._file = None
            self._update_file_label()
            self._show(None, yaml_text(value))
            return
        for name, edit in self.fields.items():
            edit.setText(text_of(shown.params.get(name)) if name in shown.params else "")
        if shown.limit is None:
            self.limit_low.setText("")
            self.limit_high.setText("")
        else:
            self.limit_low.setText(text_of(shown.limit[0]))
            self.limit_high.setText(text_of(shown.limit[1]))
        self._file = shown.params.get("file") if shown.kind == "histogram" else None
        self._update_file_label()
        self._show(shown.kind)

    def focus_first(self) -> None:
        """Focus the first field of the kind, or the kind when there is none."""
        kind = self._current_kind()
        for name in PARAMETERS.get(kind, ()):
            if name == "file":
                self.load_button.setFocus()
            else:
                self.fields[name].setFocus()
            return
        self.kind_box.setFocus()

    # --- showing -----------------------------------------------------------------------

    def _fill_kinds(self, replace: bool) -> None:
        self.kind_box.clear()
        if self._optional:
            self.kind_box.addItem(LABELS[UNSET], UNSET)
        if replace:
            self.kind_box.addItem("Replace (kept as YAML)", REPLACE)
        for kind in KINDS:
            self.kind_box.addItem(LABELS[kind], kind)

    def _clear_fields(self) -> None:
        for edit in self.fields.values():
            edit.setText("")
        self.limit_low.setText("")
        self.limit_high.setText("")

    def _show(self, kind: str | None, yaml: str | None = None) -> None:
        """Show the rows of ``kind``; a YAML text replaces them all."""
        params = PARAMETERS.get(kind, ()) if kind else ()
        for name in (*NUMBER_FIELDS, *TEXT_FIELDS):
            self._set_row_visible(name, name in params)
        self._set_row_visible("file", kind == "histogram")
        self._set_row_visible("limit", kind is not None and kind in PARAMETERS and kind != "value")
        self._set_row_visible("yaml", yaml is not None)
        if yaml is not None:
            self.yaml_view.setPlainText(yaml)

    def _set_row_visible(self, name: str, visible: bool) -> None:
        label, widget = self._rows[name]
        label.setVisible(visible)
        widget.setVisible(visible)

    def _update_file_label(self) -> None:
        if self._file:
            self.file_label.setText(Path(self._file).name)
            self.file_label.setToolTip(str(self._file))
        else:
            self.file_label.setText("No file loaded")
            self.file_label.setToolTip("")

    # --- the edits ---------------------------------------------------------------------

    def _current_kind(self) -> str | None:
        kind = self.kind_box.currentData()
        return None if kind == REPLACE else kind

    def _on_kind_changed(self, _index: int) -> None:
        kind = self._current_kind()
        if kind is None:
            return  # the "Replace" placeholder: the value stays as it is
        self._clear_fields()
        self._file = None
        self._update_file_label()
        self._show(kind)
        self._commit()

    def _commit(self) -> None:
        """Read the fields into a value and emit it when it differs from the one shown."""
        kind = self._current_kind()
        if kind is None:
            return
        if kind == UNSET:
            new = None
        else:
            params = {}
            for name in PARAMETERS[kind]:
                if name == "file":
                    params[name] = self._file
                elif name in TEXT_FIELDS:
                    params[name] = self.fields[name].text().strip() or None
                else:
                    params[name] = self._number(name)
            limit = None if kind == "value" else self._limit()
            new = build(kind, params, limit)
        if new == self._value:
            return
        self._value = deepcopy(new)
        self.value_changed.emit(deepcopy(new))

    def _old_params(self) -> dict:
        shown = describe(self._value, self._optional)
        return shown.params if shown.editable else {}

    def _number(self, name: str) -> float | None:
        edit = self.fields[name]
        try:
            return parse_number(edit.text())
        except ValueError:
            old = self._old_params().get(name)
            edit.setText(text_of(old))  # refused: the old value stays
            return old

    def _limit(self) -> tuple | None:
        old = describe(self._value, self._optional).limit or (None, None)
        bounds = []
        for index, edit in enumerate((self.limit_low, self.limit_high)):
            try:
                bounds.append(parse_number(edit.text()))
            except ValueError:
                bounds.append(old[index])
                edit.setText(text_of(old[index]))
        if bounds == [None, None]:
            return None
        return (bounds[0], bounds[1])

    def _load_histogram(self) -> None:
        path, _ = QFileDialog.getOpenFileName(self, "Load a histogram", "", HISTOGRAM_FILTER)
        if not path:
            return
        try:
            histogram = Histogram.from_file(path)
        except SpecError as error:
            QMessageBox.warning(self, "Load a histogram", str(error))
            return
        preview = HistogramPreview(histogram, self)
        accepted = preview.exec() == QDialog.DialogCode.Accepted
        preview.deleteLater()
        if not accepted:
            return
        self._file = str(histogram.file)
        self._update_file_label()
        self._commit()


class HistogramPreview(QDialog):
    """The bars of a histogram file, with OK to use it and Cancel to keep the setup."""

    def __init__(self, histogram: Histogram, parent=None):
        super().__init__(parent)
        from matplotlib.backends.backend_qtagg import FigureCanvasQTAgg
        from matplotlib.figure import Figure

        self.setWindowTitle("Histogram file")
        edges = np.asarray(histogram.edges, dtype=float)
        counts = np.asarray(histogram.counts, dtype=float)
        figure = Figure(figsize=(5, 3), layout="constrained")
        axes = figure.add_subplot()
        axes.bar(edges[:-1], counts, width=np.diff(edges), align="edge")
        axes.set_xlabel("Value")
        axes.set_ylabel("Count")
        canvas = FigureCanvasQTAgg(figure)

        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel, self
        )
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout = QVBoxLayout(self)
        layout.addWidget(QLabel(f"{len(counts)} bins from {edges[0]:g} to {edges[-1]:g}", self))
        layout.addWidget(canvas)
        layout.addWidget(buttons)


class BeamTab(QWidget):
    """The Beam editor: the ion, the number of ions, the beam mode, and one editor per quantity.

    ``editors`` is keyed by the quantity: ``energy_keV``, ``angle_deg``, ``azimuth_deg`` and the
    position coordinates ``y_nm``, ``z_nm`` and ``depth_nm``. ``plots`` holds the distribution
    plot under each editor, by the same key.
    """

    def __init__(self, document: DocumentModel, parent=None):
        super().__init__(parent)
        self.document = document

        self.ion_button = QPushButton(ION_NOT_CHOSEN, self)
        self.ion_button.setToolTip("Choose the element and its isotope")
        self.ion_button.clicked.connect(self.choose_ion)
        self.number_ions = QSpinBox(self)
        self.number_ions.setRange(1, 2147483647)
        # A typed number is one edit when Enter is pressed; an arrow or wheel step is one edit too.
        self.number_ions.setKeyboardTracking(False)
        self.number_ions.valueChanged.connect(self._on_number_ions)

        self.mode_combo = QComboBox(self)
        self.mode_combo.addItem("Automatic", "auto")
        self.mode_combo.addItem(MODE_LABELS["exact"], "exact")
        self.mode_combo.addItem(MODE_LABELS["binned"], "binned")
        self.mode_combo.currentIndexChanged.connect(self._on_mode)
        self.mode_text = QLabel(self)
        self.mode_text.setWordWrap(True)
        self.energy_bins = self._bin_spin(DEFAULT_ENERGY_BINS, "energy_keV")
        self.angle_bins = self._bin_spin(DEFAULT_ANGLE_BINS, "angle_deg")
        self.mode_group = QWidget(self)
        mode_form = QFormLayout(self.mode_group)
        mode_form.setContentsMargins(0, 0, 0, 0)
        mode_form.addRow("Beam mode", self.mode_combo)
        mode_form.addRow(self.mode_text)
        mode_form.addRow("Energy bins", self.energy_bins)
        mode_form.addRow("Angle bins", self.angle_bins)
        self.single_label = QLabel(SINGLE_RUN_TEXT, self)
        self.single_label.setWordWrap(True)

        self.editors: dict[str, QuantityEditor] = {
            "energy_keV": QuantityEditor("Energy", "keV", optional=False, parent=self),
            "angle_deg": QuantityEditor("Angle", "degrees", optional=False, parent=self),
            "azimuth_deg": QuantityEditor("Azimuth", "degrees", optional=True, parent=self),
            "y_nm": QuantityEditor("Position y", "nm", optional=True, parent=self),
            "z_nm": QuantityEditor("Position z", "nm", optional=True, parent=self),
            "depth_nm": QuantityEditor("Depth", "nm", optional=True, parent=self),
        }
        for key, editor in self.editors.items():
            editor.value_changed.connect(lambda value, k=key: self._on_value(k, value))
        self.plots: dict[str, DistributionPlot] = {
            key: DistributionPlot(self) for key in self.editors
        }

        top = QHBoxLayout()
        top.addWidget(self.ion_button)
        top.addStretch(1)
        counts = QFormLayout()
        counts.addRow("Number of ions", self.number_ions)
        # The body scrolls, so the six plots fit a small window. The tab is what the window adds.
        body = QWidget()
        layout = QVBoxLayout(body)
        layout.addLayout(top)
        layout.addLayout(counts)
        layout.addWidget(self.mode_group)
        layout.addWidget(self.single_label)
        for key, editor in self.editors.items():
            layout.addWidget(editor)
            layout.addWidget(self.plots[key])
        layout.addStretch(1)
        self.scroll = QScrollArea(self)
        self.scroll.setWidgetResizable(True)
        self.scroll.setFrameShape(QFrame.Shape.NoFrame)
        self.scroll.setWidget(body)
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.addWidget(self.scroll)

        document.changed.connect(self._refresh)
        self._refresh()

    def _bin_spin(self, default: int, key: str) -> QSpinBox:
        spin = QSpinBox(self)
        spin.setRange(0, DEFAULT_MAX_BINS)
        spin.setSpecialValueText(f"Automatic ({default})")
        spin.setKeyboardTracking(False)
        spin.valueChanged.connect(lambda value, k=key: self._on_bins(k, value))
        return spin

    # --- showing the document -----------------------------------------------------------

    def _refresh(self) -> None:
        doc = self.document.doc
        ion = doc.get("ion") or {}
        symbol = ion.get("symbol")
        if symbol:
            self.ion_button.setText(f"Ion: {element_label(symbol, ion.get('mass_amu'))}")
        else:
            self.ion_button.setText(ION_NOT_CHOSEN)
        count = doc.get("number_ions")
        if isinstance(count, int) and not isinstance(count, bool):
            self.number_ions.blockSignals(True)
            self.number_ions.setValue(min(max(count, 1), 2147483647))
            self.number_ions.blockSignals(False)
        position = ion.get("position") if isinstance(ion.get("position"), dict) else {}
        values = {
            "energy_keV": ion.get("energy_keV"),
            "angle_deg": ion.get("angle_deg"),
            "azimuth_deg": ion.get("azimuth_deg"),
        }
        # No position means every coordinate is Not set; a position shows what it holds.
        for name in POSITION_KEYS:
            values[name] = position.get(name)
        for key, editor in self.editors.items():
            editor.set_value(values[key])
        self._refresh_mode(doc)
        self._refresh_plots(doc, values)

    def _refresh_mode(self, doc: dict) -> None:
        distributed = is_distributed(doc)
        self.mode_group.setVisible(distributed)
        self.single_label.setVisible(not distributed)

        mode = _mode_of(doc)
        default_mode, default_text = default_beam_mode(doc)
        self.mode_combo.blockSignals(True)
        self.mode_combo.setItemText(0, f"Automatic ({default_mode})")
        self.mode_combo.setCurrentIndex(self.mode_combo.findData(mode))
        self.mode_combo.blockSignals(False)
        if mode == "auto":
            self.mode_text.setText(default_text)
        else:
            self.mode_text.setText(EXACT_TEXT if mode == "exact" else BINNED_TEXT)

        beam = doc.get("beam") if isinstance(doc.get("beam"), dict) else {}
        ion = doc.get("ion") or {}
        for spin, key in ((self.energy_bins, "energy_keV"), (self.angle_bins, "angle_deg")):
            count, given = _bin_count(beam, key)
            spin.blockSignals(True)
            spin.setValue(count)
            spin.blockSignals(False)
            # Bins written as edges are kept as they are: the count shows, but is not edited here.
            spin.setEnabled(mode == "binned" and isinstance(ion.get(key), Mapping) and not given)
            spin.setToolTip("The bin edges are kept as written in the setup" if given else "")

    def _refresh_plots(self, doc: dict, values: dict) -> None:
        base_dir = self.document.base_dir
        edges = _bin_edges(doc, base_dir)
        for key, plot in self.plots.items():
            plot.set_distribution(values[key], key, base_dir, edges.get(key))

    # --- the edits ---------------------------------------------------------------------

    def _edit(self, apply) -> None:
        """Apply an edit, then drop what the beam no longer needs (one undo step)."""

        def tidy(doc: dict) -> None:
            apply(doc)
            _tidy_beam(doc)

        self.document.edit(tidy)

    def choose_ion(self) -> None:
        choice = choose_element(self, "Choose the ion")
        if choice is None:
            return
        self._set_ion(choice)

    def _set_ion(self, choice: ElementChoice) -> None:
        ion = self.document.doc.get("ion") or {}
        if ion.get("symbol") == choice.symbol and ion.get("mass_amu") == choice.mass_amu:
            return

        def apply(doc: dict) -> None:
            ion_doc = doc.setdefault("ion", {})
            ion_doc["symbol"] = choice.symbol
            if choice.mass_amu is None:
                ion_doc.pop("mass_amu", None)  # SRIM's natural mass
            else:
                ion_doc["mass_amu"] = choice.mass_amu

        self.document.edit(apply)

    def _on_number_ions(self, value: int) -> None:
        if self.document.doc.get("number_ions") == value:
            return
        self.document.edit(lambda doc: doc.__setitem__("number_ions", int(value)))

    def _on_mode(self, index: int) -> None:
        choice = self.mode_combo.itemData(index)
        if choice is None or choice == _mode_of(self.document.doc):
            return

        def apply(doc: dict) -> None:
            if choice == "auto":
                doc.pop("beam", None)
            elif choice == "exact":
                # The other keys (max_bins, and the bins that wait for binned) are kept as they are.
                beam = doc.get("beam")
                beam = dict(beam) if isinstance(beam, dict) else {}
                beam["mode"] = "exact"
                doc["beam"] = beam
            else:
                beam = doc.get("beam")
                beam = dict(beam) if isinstance(beam, dict) else {}
                beam["mode"] = "binned"
                doc["beam"] = beam

        self._edit(apply)

    def _on_bins(self, key: str, value: int) -> None:
        beam = self.document.doc.get("beam")
        if not isinstance(beam, dict) or _bin_count(beam, key) == (value, False):
            return

        def apply(doc: dict) -> None:
            beam_doc = doc.setdefault("beam", {"mode": "binned"})
            bins = dict(beam_doc["bins"]) if isinstance(beam_doc.get("bins"), dict) else {}
            if value == 0:
                bins.pop(key, None)  # Automatic
            else:
                bins[key] = int(value)
            if bins:
                beam_doc["bins"] = bins
            else:
                beam_doc.pop("bins", None)

        self._edit(apply)

    def _on_value(self, key: str, value) -> None:
        if key in POSITION_KEYS:
            self._set_position(key, value)
            return

        def apply(doc: dict) -> None:
            ion_doc = doc.setdefault("ion", {})
            if value is None and key == "azimuth_deg":
                ion_doc.pop(key, None)  # Not set
            else:
                ion_doc[key] = deepcopy(value)

        self._edit(apply)

    def _set_position(self, key: str, value) -> None:
        """Store one coordinate of the position.

        A position with no coordinate set is removed. Choosing Not set on a coordinate stores 0.0
        for it, unless the other two are plain 0.0, in which case the position is removed. Setting
        a coordinate creates the position with the other two at 0.0 when there is none.
        """

        def apply(doc: dict) -> None:
            ion_doc = doc.setdefault("ion", {})
            current = ion_doc.get("position")
            current = current if isinstance(current, dict) else {}
            others = [current.get(name) for name in POSITION_KEYS if name != key]
            if value is None and all(_is_zero_or_missing(other) for other in others):
                ion_doc.pop("position", None)
                return
            position = {}
            for name in POSITION_KEYS:
                if name == key:
                    position[name] = 0.0 if value is None else deepcopy(value)
                else:
                    stored = current.get(name)
                    position[name] = 0.0 if stored is None else deepcopy(stored)
            ion_doc["position"] = position

        self._edit(apply)

    # --- the editor protocol -------------------------------------------------------------

    def focus_target(self, rest: str) -> None:
        """Focus the field a status bar line names (``rest`` is the part after ``beam/``)."""
        if rest == "ion":
            self.ion_button.setFocus()
        elif rest == "number_ions":
            self.number_ions.setFocus()
        elif rest == "mode":
            self.mode_combo.setFocus()
        elif rest == "energy_bins":
            self.energy_bins.setFocus()
        elif rest == "angle_bins":
            self.angle_bins.setFocus()
        elif rest in self.editors:
            self.editors[rest].focus_first()


# --- the beam mode, from the document ----------------------------------------------------


def _mode_of(doc: dict) -> str:
    """``auto`` (no ``beam`` key), or the mode the ``beam`` key gives (SRIM's default is exact)."""
    beam = doc.get("beam")
    if not isinstance(beam, dict):
        return "auto"
    return str(beam.get("mode", "exact"))


def _bin_count(beam: dict, key: str) -> tuple[int, bool]:
    """The bin count of ``key`` in ``beam`` (0 for Automatic), and whether it is given as edges."""
    bins = beam.get("bins") if isinstance(beam.get("bins"), dict) else {}
    value = bins.get(key, 0)
    if isinstance(value, list):
        return max(len(value) - 1, 0), True
    if isinstance(value, int) and not isinstance(value, bool):
        return value, False
    return 0, False


def _tidy_beam(doc: dict) -> None:
    """Drop a ``beam`` key the setup no longer needs, and bins of a quantity that is now fixed.

    The library refuses both, so they go in the same edit that makes them stale.
    """
    if not is_distributed(doc):
        doc.pop("beam", None)
        return
    beam = doc.get("beam")
    if not isinstance(beam, dict) or not isinstance(beam.get("bins"), dict):
        return
    ion = doc.get("ion") or {}
    bins = beam["bins"]
    for key in BIN_KEYS:
        if key in bins and not isinstance(ion.get(key), Mapping):
            del bins[key]
    if not bins:
        del beam["bins"]


def _effective_mode(doc: dict) -> str:
    mode = _mode_of(doc)
    return default_beam_mode(doc)[0] if mode == "auto" else mode


def _bin_edges(doc: dict, base_dir) -> dict[str, list[float]]:
    """The bin edges of the energy and of the angle, when the setup is binned and distributed.

    The edges are those of the library's bins (:func:`srim.beam.binned.make_bins`). A setup the
    library refuses has none, and the library's warnings are not shown here (the status bar has
    them).
    """
    if _effective_mode(doc) != "binned" or not is_distributed(doc):
        return {}
    try:
        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            bins = make_bins(to_spec(doc, base_dir))
    except SpecError:
        return {}
    edges: dict[str, list[float]] = {}
    for key, attr in (("energy_keV", "energy_range"), ("angle_deg", "angle_range")):
        ranges = [getattr(b, attr) for b in bins]
        if not ranges or any(r is None for r in ranges):
            continue
        edges[key] = sorted({r[0] for r in ranges} | {r[1] for r in ranges})
    return edges
