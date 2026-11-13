"""The Save results dialog: a run's results in the library's formats (File > Save results…).

The dialog chooses the folder, the name, the formats (a format whose extra is not installed is
greyed out with the reason as its tooltip), the collision option and, for a scan, the points. Save
runs :func:`srim.app.export_results.export_run` in a thread (:func:`srim.app.workers.run_in_thread`)
and asks before it replaces anything: only the paths the export would write are removed.
"""

import os
from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QCheckBox,
    QDialog,
    QFileDialog,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMessageBox,
    QPushButton,
    QRadioButton,
    QVBoxLayout,
    QWidget,
)

from srim.app import export_results as exports
from srim.app.export_results import FORMATS, ExportRequest
from srim.app.workers import run_in_thread
from srim.spec import SpecError

#: the label of each format's check box
FORMAT_LABELS = {
    "parquet": "Parquet (a folder of tables)",
    "hdf5": "HDF5 (one file)",
    "csv": "CSV (a folder of tables)",
    "raw": "SRIM output files (copied unchanged)",
}
COLLISION_TEXT = "Include collision details (can be large)"
TITLE = "Save results"
REPLACE_TEXT = "{count} item(s) already exist in {folder}, for example {example}. Replace them?"


class SaveResultsDialog(QDialog):
    """The export of one finished run folder. ``message`` is set when a save succeeds (the text
    for the status bar); ``failure`` when it does not."""

    def __init__(self, run_folder, parent=None):
        super().__init__(parent)
        self.setWindowTitle(TITLE)
        self.run_folder = Path(run_folder)
        self.contents = exports.read_contents(self.run_folder)
        self.message: str | None = None
        self.failure: str | None = None
        self._busy = False

        self.folder_edit = QLineEdit(str(self.run_folder.parent), self)
        self.browse_button = QPushButton("Browse…", self)
        self.name_edit = QLineEdit(self.contents.name, self)
        folder_row = QHBoxLayout()
        folder_row.addWidget(self.folder_edit, 1)
        folder_row.addWidget(self.browse_button)

        self.format_boxes: dict[str, QCheckBox] = {}
        format_layout = QVBoxLayout()
        for fmt in FORMATS:
            box = QCheckBox(FORMAT_LABELS[fmt], self)
            reason = exports.format_available(fmt)
            if reason is None:
                box.setChecked(True)
            else:
                box.setEnabled(False)
                box.setToolTip(reason)
            self.format_boxes[fmt] = box
            format_layout.addWidget(box)

        self.collision_box = QCheckBox(COLLISION_TEXT, self)

        self.all_points = QRadioButton("All points", self)
        self.chosen_points = QRadioButton("Chosen points", self)
        self.all_points.setChecked(True)
        self.point_list = QListWidget(self)
        self._fill_points()
        self.points_widget = QWidget(self)
        points_layout = QVBoxLayout(self.points_widget)
        points_layout.setContentsMargins(0, 0, 0, 0)
        points_layout.addWidget(self.all_points)
        points_layout.addWidget(self.chosen_points)
        points_layout.addWidget(self.point_list)
        self.points_widget.setVisible(not self.contents.single)

        self.status_label = QLabel(self)
        self.status_label.setWordWrap(True)
        self.save_button = QPushButton("Save", self)
        self.save_button.setDefault(True)
        self.cancel_button = QPushButton("Cancel", self)
        buttons = QHBoxLayout()
        buttons.addStretch(1)
        buttons.addWidget(self.cancel_button)
        buttons.addWidget(self.save_button)

        layout = QVBoxLayout(self)
        layout.addWidget(QLabel("Folder", self))
        layout.addLayout(folder_row)
        layout.addWidget(QLabel("Name", self))
        layout.addWidget(self.name_edit)
        layout.addWidget(QLabel("Formats", self))
        layout.addLayout(format_layout)
        layout.addWidget(self.collision_box)
        layout.addWidget(self.points_widget)
        layout.addWidget(self.status_label)
        layout.addLayout(buttons)

        self.browse_button.clicked.connect(self._browse)
        self.save_button.clicked.connect(self.save)
        self.cancel_button.clicked.connect(self.reject)
        self.folder_edit.textChanged.connect(self._refresh)
        self.name_edit.textChanged.connect(self._refresh)
        for box in self.format_boxes.values():
            box.toggled.connect(self._refresh)
        self.all_points.toggled.connect(self._refresh)
        self.chosen_points.toggled.connect(self._refresh)
        self.point_list.itemChanged.connect(self._refresh)
        self._refresh()

    def _fill_points(self) -> None:
        for point in self.contents.points:
            item = QListWidgetItem(f"{point.label} ({point.short_id}, {point.status})")
            item.setData(Qt.ItemDataRole.UserRole, point.index)
            if point.exportable:
                item.setFlags(item.flags() | Qt.ItemFlag.ItemIsUserCheckable)
                item.setCheckState(Qt.CheckState.Checked)
            else:
                item.setFlags(Qt.ItemFlag.NoItemFlags)
                item.setToolTip(f"Not exported: {point.reason}")
            self.point_list.addItem(item)
        self.chosen_points.setEnabled(bool(self.contents.points))

    # --- the request ---------------------------------------------------------------

    def _point_indices(self) -> tuple[int, ...] | None:
        """The points to export: None for all of them (or a plain run), else the checked ones."""
        if self.contents.single or self.all_points.isChecked():
            return None
        indices = []
        for row in range(self.point_list.count()):
            item = self.point_list.item(row)
            if item.checkState() == Qt.CheckState.Checked:
                indices.append(item.data(Qt.ItemDataRole.UserRole))
        return tuple(indices)

    def request(self) -> ExportRequest:
        """The export the dialog says. Raises :class:`ValueError` with the reason when it is
        not one (no folder, a bad name, no format, no point chosen)."""
        folder = self.folder_edit.text().strip()
        if not folder:
            raise ValueError("choose a folder")
        points = self._point_indices()
        if points == ():
            raise ValueError("choose at least one point")
        return ExportRequest(
            folder=Path(folder),
            name=self.name_edit.text().strip(),
            formats=tuple(fmt for fmt in FORMATS if self.format_boxes[fmt].isChecked()),
            collision=self.collision_box.isChecked(),
            points=points,
        )

    def _refresh(self, *_args) -> None:
        if self._busy:
            return
        try:
            self.request()
        except ValueError as problem:
            self.save_button.setEnabled(False)
            self.status_label.setText(str(problem))
            return
        self.save_button.setEnabled(True)
        self.status_label.setText("")

    def _set_busy(self, busy: bool) -> None:
        self._busy = busy
        widgets = (
            self.folder_edit,
            self.browse_button,
            self.name_edit,
            self.collision_box,
            self.all_points,
            self.chosen_points,
            self.point_list,
            self.save_button,
            self.cancel_button,
            *self.format_boxes.values(),
        )
        for widget in widgets:
            widget.setEnabled(not busy)
        if not busy:
            self._refresh()
        else:
            self.status_label.setText("Saving…")

    def _browse(self) -> None:
        folder = QFileDialog.getExistingDirectory(self, TITLE, self.folder_edit.text())
        if folder:
            self.folder_edit.setText(folder)

    # --- saving ----------------------------------------------------------------------

    def save(self) -> None:
        """Save the results. Asks before replacing anything; the export runs in a thread."""
        if self._busy:
            return
        try:
            request = self.request()
            existing = [
                path
                for path in exports.targets(self.run_folder, request)
                if os.path.lexists(path)
            ]
        except (OSError, ValueError, SpecError) as problem:
            self.status_label.setText(str(problem))
            return
        if existing:
            answer = QMessageBox.question(
                self,
                TITLE,
                REPLACE_TEXT.format(
                    count=len(existing), folder=request.folder, example=existing[0].name
                ),
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                QMessageBox.StandardButton.No,
            )
            if answer != QMessageBox.StandardButton.Yes:
                return
            try:
                exports.remove_targets(existing)
            except OSError as problem:
                self.status_label.setText(f"Not saved: {problem}")
                return
        self._set_busy(True)
        run_in_thread(
            lambda: self._export(request),
            lambda outcome: self._exported(request, outcome),
            self._failed,
        )

    def _export(self, request: ExportRequest):
        written = exports.export_run(self.run_folder, request)
        return written, exports.skipped_points(self.run_folder, request)

    def _exported(self, request: ExportRequest, outcome) -> None:
        written, skipped = outcome
        text = f"Saved {len(written)} files to {request.folder}"
        if skipped:
            text += f". Not exported (not finished): {', '.join(skipped)}"
        self.message = text
        self._set_busy(False)
        self.accept()

    def _failed(self, error: BaseException) -> None:
        self.failure = str(error) or type(error).__name__
        self._set_busy(False)
        self.status_label.setText(f"Not saved: {self.failure}")

    def closeEvent(self, event):  # noqa: N802 (Qt's name)
        # A save in a thread is not abandoned: the dialog stays until it has ended.
        if self._busy:
            event.ignore()
            return
        super().closeEvent(event)
