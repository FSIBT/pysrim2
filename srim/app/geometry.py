"""The Geometry tab: the layer table, a layer's elements, and the material menu.

:class:`LayerTableModel` shows ``target`` of the document as one row per layer. Every change
goes through :meth:`DocumentModel.edit`, so each one is one undo step, and the library judges
the values (the app only refuses text that is not a number). A cell named by a blocking problem
is red, with the problem as its tooltip. :class:`ElementsDialog` edits one layer's elements, and
the material menu fills a layer from a predefined compound or from "My materials".

``selected_layer`` (the row, or -1) and ``select_layer(row)`` are the hooks the cross-section
drawing (:class:`srim.app.target_view.TargetView`, beside the table) uses in both directions.
``focus_target("<row>/<field>")`` is how a status bar click reaches a cell.
"""

import math
from copy import deepcopy

from PySide6.QtCore import (
    QAbstractTableModel,
    QByteArray,
    QMimeData,
    QModelIndex,
    Qt,
    Signal,
)
from PySide6.QtGui import QBrush, QColor
from PySide6.QtWidgets import (
    QAbstractItemView,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QHBoxLayout,
    QInputDialog,
    QLabel,
    QLineEdit,
    QMenu,
    QMessageBox,
    QPushButton,
    QSplitter,
    QStyledItemDelegate,
    QTableView,
    QTableWidget,
    QVBoxLayout,
    QWidget,
)

from srim.app.document import DocumentModel
from srim.app.elements import ElementChoice, element_label
from srim.app.materials import UserMaterial, load_user_materials, save_user_material
from srim.app.model import UNITS, new_element, new_layer
from srim.app.numbers import parse_number
from srim.app.periodic import choose_element
from srim.app.target_view import TargetView
from srim.core.compounds import formula_elements, predefined_compounds

#: the MIME type of a dragged layer row (its row number, as text)
ROW_MIME = "application/x-pysrim2-layer-row"
#: the background of a cell that a blocking problem names
PROBLEM_COLOR = QColor(244, 150, 150)
#: the element fields the dialog edits, in column order, with their headings
ELEMENT_FIELDS = (
    ("stoich", "Amount"),
    ("displacement_eV", "Displacement (eV)"),
    ("lattice_eV", "Lattice (eV)"),
    ("surface_eV", "Surface (eV)"),
)
NO_MATERIALS = "(none saved)"


def shown(value) -> str:
    """A number as a cell shows it, or "" for a missing value."""
    return "" if value is None else f"{value:g}"


def element_name(element: dict) -> str:
    """The symbol of an element, or its isotope (``Si-28``) when it has a mass."""
    symbol = element.get("symbol") or ""
    mass = element.get("mass_amu")
    return symbol if mass is None else element_label(symbol, mass)


def elements_text(layer: dict) -> str:
    """The Elements cell: ``"Si 1, O 2"``, an isotope as ``"Si-28 1"``."""
    parts = []
    for element in layer.get("elements") or []:
        name = element_name(element)
        stoich = element.get("stoich")
        parts.append(name if stoich is None else f"{name} {stoich:g}")
    return ", ".join(parts)


def _is_checked(value) -> bool:
    return value == Qt.CheckState.Checked or value == Qt.CheckState.Checked.value


class LayerTableModel(QAbstractTableModel):
    """One row per layer of the document; the columns are :attr:`COLUMNS`."""

    COLUMNS = ("name", "elements", "density", "thickness", "unit", "phase")
    HEADERS = ("Name", "Elements", "Density (g/cm³)", "Thickness", "Unit", "Gas")

    def __init__(self, document: DocumentModel, parent=None):
        super().__init__(parent)
        self._document = document
        self._count = len(self._layers())
        self._marks: dict[tuple[int, str], str] = {}
        self._update_marks()
        document.changed.connect(self._on_document_changed)

    # --- the document ----------------------------------------------------------------

    def _layers(self) -> list:
        return self._document.doc.get("target") or []

    def _update_marks(self) -> None:
        """Map (row, field) to the message of each blocking problem about a layer's field."""
        self._marks = {}
        for problem in self._document.problems:
            if not problem.blocking or problem.target is None:
                continue
            parts = problem.target.split("/")
            if len(parts) == 3 and parts[0] == "geometry" and parts[1].isdigit():
                self._marks.setdefault((int(parts[1]), parts[2]), problem.text)

    def _on_document_changed(self) -> None:
        count = len(self._layers())
        self._update_marks()
        if count != self._count:
            self.beginResetModel()
            self._count = count
            self.endResetModel()
        elif count:
            last = len(self.COLUMNS) - 1
            self.dataChanged.emit(self.index(0, 0), self.index(count - 1, last))

    def _edit_layer(self, row: int, key: str, value) -> bool:
        """Set one field of a layer as one edit; an unchanged value is not an edit."""
        if self._layers()[row].get(key) == value:
            return True

        def apply(doc: dict) -> None:
            doc["target"][row][key] = value

        self._document.edit(apply)
        return True

    # --- Qt model interface -------------------------------------------------------------

    def rowCount(self, parent=None):  # noqa: N802 (Qt's name)
        return 0 if parent is not None and parent.isValid() else len(self._layers())

    def columnCount(self, parent=None):  # noqa: N802
        return 0 if parent is not None and parent.isValid() else len(self.COLUMNS)

    def headerData(self, section, orientation, role=Qt.ItemDataRole.DisplayRole):  # noqa: N802
        if orientation == Qt.Orientation.Horizontal and role == Qt.ItemDataRole.DisplayRole:
            return self.HEADERS[section]
        return None

    def flags(self, index):
        flags = (
            Qt.ItemFlag.ItemIsEnabled
            | Qt.ItemFlag.ItemIsSelectable
            | Qt.ItemFlag.ItemIsDragEnabled
            | Qt.ItemFlag.ItemIsDropEnabled
        )
        column = self.COLUMNS[index.column()]
        if column in ("name", "density", "thickness", "unit"):
            flags |= Qt.ItemFlag.ItemIsEditable
        if column == "phase":
            flags |= Qt.ItemFlag.ItemIsUserCheckable
        return flags

    def data(self, index, role=Qt.ItemDataRole.DisplayRole):
        if not index.isValid():
            return None
        row, column = index.row(), self.COLUMNS[index.column()]
        layers = self._layers()
        if row >= len(layers):
            return None
        layer = layers[row]
        if role == Qt.ItemDataRole.BackgroundRole and (row, column) in self._marks:
            return QBrush(PROBLEM_COLOR)
        if role == Qt.ItemDataRole.ToolTipRole and (row, column) in self._marks:
            return self._marks[(row, column)]
        if column == "phase":
            if role == Qt.ItemDataRole.CheckStateRole:
                checked = layer.get("phase") == 1
                return Qt.CheckState.Checked if checked else Qt.CheckState.Unchecked
            return None
        if role in (Qt.ItemDataRole.DisplayRole, Qt.ItemDataRole.EditRole):
            return self._text(layer, column)
        return None

    def _text(self, layer: dict, column: str) -> str:
        if column == "name":
            return layer.get("name") or ""
        if column == "elements":
            return elements_text(layer)
        if column == "density":
            return shown(layer.get("density_g_cm3"))
        if column == "thickness":
            nm = layer.get("thickness_nm")
            return shown(None if nm is None else nm / UNITS[layer.get("_unit", "nm")])
        return layer.get("_unit", "nm")  # unit

    def setData(self, index, value, role=Qt.ItemDataRole.EditRole):  # noqa: N802
        if not index.isValid() or index.row() >= len(self._layers()):
            return False
        row, column = index.row(), self.COLUMNS[index.column()]
        if column == "phase":
            if role != Qt.ItemDataRole.CheckStateRole:
                return False
            return self._edit_layer(row, "phase", 1 if _is_checked(value) else 0)
        if role != Qt.ItemDataRole.EditRole:
            return False
        if column == "name":
            if not isinstance(value, str):
                return False  # the view's cleanup after a drop sends None; a name is text
            return self._edit_layer(row, "name", value)
        if column == "unit":
            if value not in UNITS:
                return False
            return self._edit_layer(row, "_unit", value)
        if column not in ("density", "thickness"):
            return False  # the elements are edited in their dialog
        try:
            number = parse_number(str(value))
        except ValueError:
            return False
        if column == "density":
            return self._edit_layer(row, "density_g_cm3", number)
        layer = self._layers()[row]
        if number is None:
            return self._edit_layer(row, "thickness_nm", None)
        nm = number * UNITS[layer.get("_unit", "nm")]
        if not math.isfinite(nm):
            return False
        return self._edit_layer(row, "thickness_nm", nm)

    # --- moving layers ------------------------------------------------------------------

    def supportedDropActions(self):  # noqa: N802
        return Qt.DropAction.MoveAction

    def supportedDragActions(self):  # noqa: N802
        return Qt.DropAction.MoveAction

    def mimeTypes(self):  # noqa: N802
        return [ROW_MIME]

    def mimeData(self, indexes):  # noqa: N802
        data = QMimeData()
        if indexes:
            data.setData(ROW_MIME, QByteArray(str(indexes[0].row()).encode()))
        return data

    def removeRows(self, row, count, parent=None):  # noqa: N802
        """Layers are removed only by the Remove button, through the document.

        The view asks for this after a drop it has accepted; refusing keeps the layer.
        """
        return False

    def dropMimeData(self, data, action, row, column, parent):  # noqa: N802
        if action != Qt.DropAction.MoveAction or not data.hasFormat(ROW_MIME):
            return False
        try:
            source = int(bytes(data.data(ROW_MIME)).decode())
        except ValueError:
            return False
        if parent.isValid():
            destination = parent.row()  # dropped on a layer: before it
        elif row >= 0:
            destination = row
        else:
            destination = len(self._layers())
        return self.moveRows(QModelIndex(), source, 1, QModelIndex(), destination)

    def moveRows(self, source_parent, source_row, count, destination_parent, destination_child):  # noqa: N802,E501
        """Move the layer at ``source_row`` so that it sits before ``destination_child``.

        ``destination_child`` counts the rows before the move: 2 moves row 0 after row 1.
        Returns False for a move that changes nothing or is out of range.
        """
        if count != 1 or source_parent.isValid() or destination_parent.isValid():
            return False
        size = len(self._layers())
        if not 0 <= source_row < size or not 0 <= destination_child <= size:
            return False
        if destination_child in (source_row, source_row + 1):
            return False
        if not self.beginMoveRows(
            QModelIndex(), source_row, source_row, QModelIndex(), destination_child
        ):
            return False

        def move(doc: dict) -> None:
            layers = doc["target"]
            layer = layers.pop(source_row)
            before = destination_child - 1 if destination_child > source_row else destination_child
            layers.insert(before, layer)

        self._document.edit(move)
        self.endMoveRows()
        return True


class UnitDelegate(QStyledItemDelegate):
    """The Unit column edits with a menu of the display units."""

    def createEditor(self, parent, option, index):  # noqa: N802
        combo = QComboBox(parent)
        combo.addItems(list(UNITS))
        return combo

    def setEditorData(self, editor, index):  # noqa: N802
        editor.setCurrentText(str(index.data(Qt.ItemDataRole.EditRole) or "nm"))

    def setModelData(self, editor, model, index):  # noqa: N802
        model.setData(index, editor.currentText(), Qt.ItemDataRole.EditRole)


def _compound_elements(formula: str) -> list[dict]:
    """The elements of a formula with their ratios as amounts, for example SiO2: Si 1, O 2."""
    return [
        {**new_element(symbol), "stoich": float(ratio)}
        for symbol, ratio in formula_elements(formula)
    ]


class ElementsDialog(QDialog):
    """The elements of one layer. ``elements`` is the edited list; the caller stores it.

    Each row has the element (with "Choose…") and the amount and energies. A number that is not
    one is refused and the field goes back to its value; an empty amount is missing.
    """

    def __init__(self, elements: list[dict], parent=None):
        super().__init__(parent)
        self.setWindowTitle("Elements of the layer")
        self._elements = [deepcopy(element) for element in elements]
        self._labels: list[QLabel] = []
        self._choose_buttons: list[QPushButton] = []
        self._fields: list[dict[str, QLineEdit]] = []

        self.table = QTableWidget(0, 1 + len(ELEMENT_FIELDS), self)
        self.table.setHorizontalHeaderLabels(["Element", *(text for _, text in ELEMENT_FIELDS)])
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.add_button = QPushButton("Add element", self)
        self.remove_button = QPushButton("Remove", self)
        self.add_button.clicked.connect(self._add)
        self.remove_button.clicked.connect(self._remove)
        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel, self
        )
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)

        row_buttons = QHBoxLayout()
        row_buttons.addWidget(self.add_button)
        row_buttons.addWidget(self.remove_button)
        row_buttons.addStretch(1)
        layout = QVBoxLayout(self)
        layout.addWidget(self.table)
        layout.addLayout(row_buttons)
        layout.addWidget(buttons)
        self._rebuild()

    @property
    def elements(self) -> list[dict]:
        """A copy of the elements as the dialog now holds them."""
        return deepcopy(self._elements)

    def row_count(self) -> int:
        return self.table.rowCount()

    def label(self, row: int) -> str:
        return self._labels[row].text()

    def choose_button(self, row: int) -> QPushButton:
        return self._choose_buttons[row]

    def field(self, row: int, key: str) -> QLineEdit:
        return self._fields[row][key]

    def _rebuild(self) -> None:
        self.table.setRowCount(0)
        self._labels, self._choose_buttons, self._fields = [], [], []
        for row, element in enumerate(self._elements):
            self.table.insertRow(row)
            holder = QWidget(self.table)
            holder_layout = QHBoxLayout(holder)
            holder_layout.setContentsMargins(0, 0, 0, 0)
            label = QLabel(element_name(element), holder)
            choose = QPushButton("Choose…", holder)
            choose.clicked.connect(lambda _=False, r=row: self._choose(r))
            holder_layout.addWidget(label)
            holder_layout.addWidget(choose)
            self.table.setCellWidget(row, 0, holder)
            self._labels.append(label)
            self._choose_buttons.append(choose)
            fields = {}
            for column, (key, _) in enumerate(ELEMENT_FIELDS, start=1):
                edit = QLineEdit(shown(element.get(key)), self.table)
                edit.editingFinished.connect(lambda r=row, k=key, e=edit: self._set_number(r, k, e))
                self.table.setCellWidget(row, column, edit)
                fields[key] = edit
            self._fields.append(fields)
        self.table.resizeColumnsToContents()

    def _set_number(self, row: int, key: str, edit: QLineEdit) -> None:
        try:
            value = parse_number(edit.text())
        except ValueError:
            edit.setText(shown(self._elements[row].get(key)))  # refused: the old value stays
            return
        self._elements[row][key] = value
        edit.setText(shown(value))

    def _choose(self, row: int) -> None:
        choice = choose_element(self, "Choose an element")
        if choice is None:
            return
        self._replace_element(row, choice)

    def _replace_element(self, row: int, choice: ElementChoice) -> None:
        element = self._elements[row]
        element["symbol"] = choice.symbol
        if choice.mass_amu is None:
            element.pop("mass_amu", None)
        else:
            element["mass_amu"] = choice.mass_amu
        self._labels[row].setText(element_name(element))

    def _add(self) -> None:
        choice = choose_element(self, "Add an element")
        if choice is None:
            return
        self._elements.append(new_element(choice.symbol, choice.mass_amu))
        self._rebuild()

    def _remove(self) -> None:
        rows = sorted({index.row() for index in self.table.selectionModel().selectedIndexes()})
        if not rows:
            return
        del self._elements[rows[0]]
        self._rebuild()


class GeometryTab(QWidget):
    """The editor for the layers. Its ``selected_layer`` is the selected row, or -1."""

    selected_layer = Signal(int)

    def __init__(self, document: DocumentModel, parent=None):
        super().__init__(parent)
        self.document = document
        self._selected = -1
        self._resetting = False

        self.model = LayerTableModel(document, self)
        self.table = QTableView(self)
        self.table.setModel(self.model)
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.table.setDragEnabled(True)
        self.table.setAcceptDrops(True)
        self.table.setDropIndicatorShown(True)
        self.table.setDragDropMode(QAbstractItemView.DragDropMode.InternalMove)
        # The view would otherwise clear the moved row's cells after a drop (overwrite mode).
        self.table.setDragDropOverwriteMode(False)
        self.table.doubleClicked.connect(self._on_double_click)
        self.table.setItemDelegateForColumn(
            self.model.COLUMNS.index("unit"), UnitDelegate(self.table)
        )

        self.add_button = QPushButton("Add layer", self)
        self.remove_button = QPushButton("Remove layer", self)
        self.up_button = QPushButton("Up", self)
        self.down_button = QPushButton("Down", self)
        self.elements_button = QPushButton("Elements…", self)
        self.material_button = QPushButton("Material…", self)
        self.save_material_button = QPushButton("Save as material…", self)
        self.add_button.clicked.connect(self.add_layer)
        self.remove_button.clicked.connect(self.remove_layer)
        self.up_button.clicked.connect(lambda: self.move_layer(-1))
        self.down_button.clicked.connect(lambda: self.move_layer(1))
        self.elements_button.clicked.connect(self.edit_elements)
        self.save_material_button.clicked.connect(self.save_material)

        self._build_material_menu()

        buttons = QHBoxLayout()
        for button in (
            self.add_button,
            self.remove_button,
            self.up_button,
            self.down_button,
            self.elements_button,
            self.material_button,
            self.save_material_button,
        ):
            buttons.addWidget(button)
        buttons.addStretch(1)
        # The to-scale drawing of the target sits beside the table; the two select each other.
        self.target_view = TargetView(document, self)
        self.target_view.set_selected(self._selected)
        self.target_view.layer_clicked.connect(self.select_layer)
        self.selected_layer.connect(self.target_view.set_selected)
        self.splitter = QSplitter(Qt.Orientation.Horizontal, self)
        self.splitter.addWidget(self.table)
        self.splitter.addWidget(self.target_view)
        self.splitter.setStretchFactor(0, 3)
        self.splitter.setStretchFactor(1, 2)

        layout = QVBoxLayout(self)
        layout.addLayout(buttons)
        layout.addWidget(self.splitter)

        # Connected after setModel, so the selection model's reset handling runs first.
        self.table.selectionModel().currentRowChanged.connect(self._on_current_row)
        self.model.modelAboutToBeReset.connect(self._reset_started)
        self.model.modelReset.connect(self._reset_done)
        document.changed.connect(self._on_document_changed)
        self._on_document_changed()

    # --- selection ------------------------------------------------------------------

    def _layer_count(self) -> int:
        return len(self.document.doc.get("target") or [])

    def _valid(self, row: int) -> bool:
        return 0 <= row < self._layer_count()

    def select_layer(self, row: int) -> None:
        """Select the layer at ``row``; -1, or a row out of range, selects none."""
        self._show_selection(row if self._valid(row) else -1)

    def _show_selection(self, row: int) -> None:
        if self._valid(row):
            self.table.setCurrentIndex(self.model.index(row, 0))
            self.table.selectRow(row)
        else:
            self.table.clearSelection()
            self.table.setCurrentIndex(QModelIndex())
        self._set_selected(row if self._valid(row) else -1)

    def _set_selected(self, row: int) -> None:
        if row != self._selected:
            self._selected = row
            self.selected_layer.emit(row)
        self._update_buttons()

    def _on_current_row(self, current, _previous) -> None:
        if self._resetting:
            return  # the model is rebuilding; _on_document_changed restores the selection
        self._set_selected(current.row() if current.isValid() else -1)

    def _reset_started(self) -> None:
        self._resetting = True

    def _reset_done(self) -> None:
        self._resetting = False

    def _on_document_changed(self) -> None:
        row = self._selected
        if row >= self._layer_count():
            row = self._layer_count() - 1
        self._show_selection(row if self._valid(row) else -1)
        self._update_buttons()

    def _update_buttons(self) -> None:
        count = self._layer_count()
        row = self._selected
        valid = 0 <= row < count
        self.remove_button.setEnabled(valid)
        self.up_button.setEnabled(valid and row > 0)
        self.down_button.setEnabled(valid and row < count - 1)
        self.elements_button.setEnabled(valid)
        self.material_button.setEnabled(valid)
        self.save_material_button.setEnabled(valid)

    # --- layers -----------------------------------------------------------------------

    def add_layer(self) -> None:
        self.document.edit(lambda doc: doc["target"].append(new_layer()))
        self.select_layer(self._layer_count() - 1)

    def remove_layer(self) -> None:
        row = self._selected
        if not self._valid(row):
            return
        self.document.edit(lambda doc: doc["target"].pop(row))
        self.select_layer(min(row, self._layer_count() - 1))

    def move_layer(self, delta: int) -> None:
        row = self._selected
        target = row + delta
        if not self._valid(row) or not self._valid(target):
            return
        self.model.moveRows(
            QModelIndex(), row, 1, QModelIndex(), target if delta < 0 else target + 1
        )
        self.select_layer(target)

    def focus_target(self, rest: str) -> None:
        """Select the layer and the cell a status bar line names, and edit a text cell."""
        head, _, field = rest.partition("/")
        if not head.isdigit() or not self._valid(int(head)):
            return
        row = int(head)
        self.select_layer(row)
        if field not in self.model.COLUMNS or field == "unit":
            return
        index = self.model.index(row, self.model.COLUMNS.index(field))
        self.table.setCurrentIndex(index)
        if field in ("name", "density", "thickness"):
            self.table.edit(index)

    # --- elements and materials --------------------------------------------------------

    def edit_elements(self) -> None:
        row = self._selected
        if not self._valid(row):
            return
        layer = self.document.doc["target"][row]
        dialog = ElementsDialog(layer.get("elements") or [], self)
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return
        elements = dialog.elements
        self.document.edit(lambda doc: doc["target"][row].__setitem__("elements", elements))

    def _on_double_click(self, index) -> None:
        if self.model.COLUMNS[index.column()] == "elements":
            self.select_layer(index.row())
            self.edit_elements()

    def _use_material(self, name: str, density, phase: int, elements: list[dict]) -> None:
        row = self._selected
        if not self._valid(row):
            return

        def apply(doc: dict) -> None:
            layer = doc["target"][row]
            layer["name"] = name
            layer["density_g_cm3"] = density
            layer["phase"] = phase
            layer["elements"] = deepcopy(elements)

        self.document.edit(apply)

    def _build_material_menu(self) -> None:
        menu = QMenu(self.material_button)
        for compound in predefined_compounds():
            action = menu.addAction(compound.name)
            action.triggered.connect(
                lambda _=False, c=compound: self._use_material(
                    c.name, c.density_g_cm3, c.phase, _compound_elements(c.formula)
                )
            )
        menu.addSeparator()
        self._my_materials = QMenu("My materials", menu)
        self._my_materials.aboutToShow.connect(self._fill_my_materials)
        menu.addMenu(self._my_materials)
        self.material_button.setMenu(menu)
        self._fill_my_materials()

    def _fill_my_materials(self) -> None:
        self._my_materials.clear()
        materials = load_user_materials()
        if not materials:
            self._my_materials.addAction(NO_MATERIALS).setEnabled(False)
        for material in materials:
            action = self._my_materials.addAction(material.name)
            action.triggered.connect(
                lambda _=False, m=material: self._use_material(
                    m.name, m.density_g_cm3, m.phase, m.elements
                )
            )

    def save_material(self) -> None:
        row = self._selected
        if not self._valid(row):
            return
        layer = self.document.doc["target"][row]
        name, ok = QInputDialog.getText(
            self, "Save as material", "Name of the material:", text=layer.get("name") or ""
        )
        if not ok or not name.strip():
            return
        material = UserMaterial(
            name.strip(),
            layer.get("density_g_cm3"),
            layer.get("phase", 0),
            deepcopy(layer.get("elements") or []),
        )
        try:
            save_user_material(material)
        except (ValueError, OSError) as error:
            QMessageBox.warning(self, "Save as material", str(error))
            return
        self._fill_my_materials()
