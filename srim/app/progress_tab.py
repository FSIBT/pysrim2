"""The Progress tab: the run of the setup as it goes, and as it ended (until the next run starts).

The tab is a view of the run controller (:class:`srim.app.run_control.RunController`): its
``state`` is a :class:`srim.ui.tui.state.ScanState`, and the summary, counts, tables and warnings
are read from it (through its helpers: ``status_line``, ``counts``, ``table_columns``, ``cell``,
``sorted_rows``, ``last_warnings``, ``failed_point`` and ``log_path``). Nothing here decides a
status or a value. The window's Pause and Stop actions are the tab's buttons.

The tab redraws on the controller's ``updated`` signal, and on a one-second timer (the elapsed
time) while a run goes; the timer stops when the run ends. A click on a column header sorts the
points by that column (a second click reverses the order). "Open log" opens the ``error.log`` of
the selected point, or else of the last point that failed, with the desktop's default program.
"""

from pathlib import Path

from PySide6.QtCore import QAbstractTableModel, Qt, QTimer, QUrl
from PySide6.QtGui import QDesktopServices
from PySide6.QtWidgets import (
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QListWidget,
    QPushButton,
    QTableView,
    QTableWidget,
    QTableWidgetItem,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from srim.scan.events import now
from srim.ui.tui.state import ScanState, format_value, status_line

#: the text before any run
NO_RUN_TEXT = "No run yet. Start one from the Run menu."
#: how often the elapsed time is redrawn while a run goes (milliseconds)
TICK_MS = 1000
#: most recent warnings and errors shown
WARNINGS_SHOWN = 20
#: the statuses of the count line, in this order; a status with no points is left out
COUNT_ORDER = ("done", "running", "queued", "cached", "failed", "stopped")
WORKER_HEADINGS = ("Worker", "Point", "Ions/s")


def counts_text(state: ScanState) -> str:
    """``done 1 · queued 1 · failed 1``: the statuses that have points, in :data:`COUNT_ORDER`."""
    counts = state.counts()
    return " · ".join(f"{status} {counts[status]}" for status in COUNT_ORDER if counts[status])


class PointsModel(QAbstractTableModel):
    """The points of the state, one row each, in the order of the sort column.

    The rows and columns are taken again on each :meth:`refresh`. The model is reset only when
    the state, the columns or the order of the rows change; otherwise only the cells are
    redrawn (so the selection and the scroll position stay).
    """

    def __init__(self, parent=None):
        super().__init__(parent)
        self._state: ScanState | None = None
        self._columns: list[tuple[str, str]] = []
        self._rows: list = []
        self._column = "index"
        self._reverse = False

    # -- the Qt model -------------------------------------------------------------------

    @staticmethod
    def _is_child(parent) -> bool:
        return parent is not None and parent.isValid()

    def rowCount(self, parent=None):  # noqa: N802 (Qt's name)
        return 0 if self._is_child(parent) else len(self._rows)

    def columnCount(self, parent=None):  # noqa: N802 (Qt's name)
        return 0 if self._is_child(parent) else len(self._columns)

    def data(self, index, role=Qt.ItemDataRole.DisplayRole):
        if not index.isValid() or role != Qt.ItemDataRole.DisplayRole or self._state is None:
            return None
        row = self._rows[index.row()]
        return self._state.cell(row, self._columns[index.column()][0])

    def headerData(self, section, orientation, role=Qt.ItemDataRole.DisplayRole):  # noqa: N802
        if (
            orientation == Qt.Orientation.Horizontal
            and role == Qt.ItemDataRole.DisplayRole
            and 0 <= section < len(self._columns)
        ):
            return self._columns[section][1]
        return super().headerData(section, orientation, role)

    # -- what the tab asks ----------------------------------------------------------------

    def columns(self) -> list[tuple[str, str]]:
        """``(column id, heading)`` of the columns shown (see ``ScanState.table_columns``)."""
        return list(self._columns)

    def point_at(self, row: int):
        """The point shown in ``row``."""
        return self._rows[row]

    def row_of(self, index: int) -> int | None:
        """The row that shows the point with this index (None when it is not shown)."""
        for row, point in enumerate(self._rows):
            if point.index == index:
                return row
        return None

    def sort_section(self) -> tuple[int, bool]:
        """The column the rows are sorted by (its section) and whether the order is reversed."""
        ids = [column_id for column_id, _ in self._columns]
        section = ids.index(self._column) if self._column in ids else 0
        return section, self._reverse

    def sort_by(self, section: int) -> None:
        """Sort by the column of ``section``; the same column again reverses the order."""
        if self._state is None or not 0 <= section < len(self._columns):
            return
        column = self._columns[section][0]
        if column == self._column:
            self._reverse = not self._reverse
        else:
            self._column, self._reverse = column, False
        self.refresh(self._state)

    def refresh(self, state: ScanState | None) -> None:
        """Take the rows and columns of ``state`` (None: no run, so the table is empty)."""
        columns = state.table_columns() if state is not None else []
        rows = state.sorted_rows(self._column, self._reverse) if state is not None else []
        same_run = state is self._state
        same_layout = columns == self._columns and [p.index for p in rows] == [
            p.index for p in self._rows
        ]
        if same_run and same_layout:
            self._rows = rows
            if rows and columns:
                self.dataChanged.emit(self.index(0, 0), self.index(len(rows) - 1, len(columns) - 1))
            return
        self.beginResetModel()
        self._state = state
        self._columns = columns
        self._rows = rows
        self.endResetModel()


class ProgressTab(QWidget):
    """The run's progress: the summary, the counts, the workers, the points and the warnings.

    ``controller`` is the run controller (its signals and ``state``); ``pause_action`` and
    ``stop_action`` are the window's Run actions, shown as buttons here.
    """

    def __init__(self, controller, pause_action, stop_action, parent=None):
        super().__init__(parent)
        self._controller = controller
        self._selected: int | None = None  # the index of the selected point
        self._restoring = False
        #: redraws the elapsed time while a run goes (it runs only then)
        self.timer = QTimer(self)
        self.timer.setInterval(TICK_MS)
        self.timer.timeout.connect(self.refresh)

        self.summary = QLabel(NO_RUN_TEXT, self)
        self.summary.setWordWrap(True)
        self.counts = QLabel("", self)
        self.counts.setWordWrap(True)

        self.pause_button = QToolButton(self)
        self.pause_button.setDefaultAction(pause_action)
        self.stop_button = QToolButton(self)
        self.stop_button.setDefaultAction(stop_action)
        self.open_log_button = QPushButton("Open log", self)
        self.open_log_button.setEnabled(False)
        self.open_log_button.clicked.connect(self._open_log)

        self.workers_table = QTableWidget(0, len(WORKER_HEADINGS), self)
        self.workers_table.setHorizontalHeaderLabels(list(WORKER_HEADINGS))
        self.workers_table.verticalHeader().setVisible(False)
        self.workers_table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)

        self.points_model = PointsModel(self)
        self.points_table = QTableView(self)
        self.points_table.setModel(self.points_model)
        self.points_table.setSelectionBehavior(QTableView.SelectionBehavior.SelectRows)
        self.points_table.setSelectionMode(QTableView.SelectionMode.SingleSelection)
        self.points_table.setEditTriggers(QTableView.EditTrigger.NoEditTriggers)
        header = self.points_table.horizontalHeader()
        header.setSectionsClickable(True)
        header.setSortIndicatorShown(True)
        header.setSectionResizeMode(QHeaderView.ResizeMode.ResizeToContents)
        header.sectionClicked.connect(self._on_header_clicked)
        self.points_table.selectionModel().selectionChanged.connect(self._on_selection)

        self.warnings = QListWidget(self)

        buttons = QHBoxLayout()
        buttons.addWidget(self.pause_button)
        buttons.addWidget(self.stop_button)
        buttons.addWidget(self.open_log_button)
        buttons.addStretch(1)
        layout = QVBoxLayout(self)
        layout.addWidget(self.summary)
        layout.addWidget(self.counts)
        layout.addLayout(buttons)
        layout.addWidget(self.points_table, 3)
        layout.addWidget(QLabel("Workers", self))
        layout.addWidget(self.workers_table, 1)
        layout.addWidget(QLabel("Warnings and errors (newest first)", self))
        layout.addWidget(self.warnings, 1)

        controller.started.connect(self._on_started)
        controller.updated.connect(self.refresh)
        controller.finished.connect(self._on_ended)
        controller.failed.connect(self._on_ended)
        self.refresh()

    # -- the controller's signals -----------------------------------------------------------

    def _on_started(self) -> None:
        self._selected = None
        self.timer.start()
        self.refresh()

    def _on_ended(self, *_args) -> None:
        # The final state stays on screen until the next run starts.
        self.timer.stop()
        self.refresh()

    # -- drawing ------------------------------------------------------------------------------

    def refresh(self) -> None:
        """Draw the tab from the controller's state."""
        state = self._controller.state
        self.points_model.refresh(state)
        self._restore_selection()
        self._show_header(state)
        self._show_workers(state)
        self._show_warnings(state)
        self._update_log_button()

    def _show_header(self, state: ScanState | None) -> None:
        if state is None:
            self.summary.setText(NO_RUN_TEXT)
            self.counts.setText("")
            return
        self.summary.setText(status_line(state, now()))
        self.counts.setText(counts_text(state))
        section, reverse = self.points_model.sort_section()
        order = Qt.SortOrder.DescendingOrder if reverse else Qt.SortOrder.AscendingOrder
        self.points_table.horizontalHeader().setSortIndicator(section, order)

    def _show_workers(self, state: ScanState | None) -> None:
        workers = sorted(state.workers.values(), key=lambda w: w.ident) if state else []
        self.workers_table.setRowCount(len(workers))
        for row, worker in enumerate(workers):
            cells = (
                str(worker.ident),
                "—" if worker.point is None else str(worker.point),
                format_value(worker.ions_per_s),
            )
            for column, text in enumerate(cells):
                self.workers_table.setItem(row, column, QTableWidgetItem(text))

    def _show_warnings(self, state: ScanState | None) -> None:
        self.warnings.clear()
        if state is None:
            return
        for level, text in reversed(state.last_warnings(WARNINGS_SHOWN)):
            self.warnings.addItem(f"{level.capitalize()}: {text}")

    # -- the points table -----------------------------------------------------------------

    def _on_header_clicked(self, section: int) -> None:
        self.points_model.sort_by(section)
        self._show_header(self._controller.state)

    def _on_selection(self, *_args) -> None:
        if self._restoring:
            return
        rows = self.points_table.selectionModel().selectedRows()
        self._selected = self.points_model.point_at(rows[0].row()).index if rows else None
        self._update_log_button()

    def _restore_selection(self) -> None:
        """Select again the point that was selected before the rows were taken again."""
        row = None if self._selected is None else self.points_model.row_of(self._selected)
        self._restoring = True
        try:
            if row is None:
                self.points_table.clearSelection()
            else:
                self.points_table.selectRow(row)
        finally:
            self._restoring = False

    # -- the log -------------------------------------------------------------------------

    def _log_path(self) -> Path | None:
        """The log to open: of the selected point, else of the last point that failed. None when
        there is no such file."""
        state = self._controller.state
        if state is None:
            return None
        index = self._selected
        if index is None:
            failed = state.failed_point()
            if failed is None:
                return None
            index = failed.index
        path = state.log_path(index)
        return path if path is not None and path.exists() else None

    def _update_log_button(self) -> None:
        self.open_log_button.setEnabled(self._log_path() is not None)

    def _open_log(self) -> None:
        path = self._log_path()
        if path is not None:
            QDesktopServices.openUrl(QUrl.fromLocalFile(str(path)))
