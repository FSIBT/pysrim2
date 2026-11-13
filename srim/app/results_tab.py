"""The Results tab: the plots of a run's results, one point at a time, and a scan's own views.

The tab is a view of the run controller (:class:`srim.app.run_control.RunController`). While a
run goes it lists the points that are done so far (the controller's ``state``, and the point
folders under its ``folder``); when the run has ended it lists the points of its
:class:`~srim.scan.ScanResult` that finished with outputs (``done``, ``max_ions`` or ``cached``).
A single run has one point, and then the point choice is hidden.

The plot list is :data:`srim.app.plots.CATALOGUE` grouped by its groups. A plot the points cannot
give is greyed out, and its tooltip says what it needs (:func:`~srim.app.plots.missing_reason`).
Selecting a plot draws it once; while a run goes the shown plot (and each visible pop-out) is
drawn again every ``settings.refresh_seconds``. Nothing is drawn before there are results: the tab
says "No results yet." and, when every point failed, why.

A scan (more than one point) has a Scan group with two kinds of view
(:mod:`srim.app.scan_plots`): the Summary (a value against a parameter, coloured by another) and
an overlay per depth profile (one curve per checked point, at most
:data:`~srim.app.scan_plots.CURVE_LIMIT`, chosen by a check list and a filter). The scan table is
the result's table, or the table of the live state while the run goes; the same views serve both.

"From the previous setup" shows while the setup differs from the one the run was started with
(``controller.snapshot``). The comparison is :func:`srim.app.model.clean` of both setups, and the
``_scan`` section too (``clean`` drops the ``_`` keys).
"""

from dataclasses import dataclass
from pathlib import Path

from PySide6.QtCore import Qt, QTimer
from PySide6.QtWidgets import (
    QComboBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QPushButton,
    QTreeWidget,
    QTreeWidgetItem,
    QVBoxLayout,
    QWidget,
)

from srim.app.model import clean
from srim.app.plot_canvas import PlotCanvas, PlotWindow
from srim.app.plots import CATALOGUE, missing_reason, plot_data
from srim.app.runs import (
    MISSING_OUTPUTS_TEXT,
    SHOWN_STATUSES,
    has_outputs,
    open_point,
    outputs_missing,
)
from srim.app.scan_plots import (
    CURVE_LIMIT,
    draw_overlay,
    draw_summary,
    point_label,
    point_styles,
    scan_table,
    select_points,
    split_frames,
    stack_frames,
    summary_data,
    value_columns,
)
from srim.spec import SpecError

#: the text before any result (and what every plot needs until there is one)
NO_RESULTS_TEXT = "No results yet."
#: the text of a point whose results cannot be read
UNREADABLE_TEXT = "The results of this point could not be read."
#: the text when results exist but no plot is available for them
NOTHING_TEXT = "No plot is available for this point."
#: the banner while the setup differs from the one the run was made from
BANNER_TEXT = "From the previous setup"
POP_OUT_TEXT = "Pop out"
POINT_TEXT = "Point"
#: the scan group of the plot list, its summary, and the key prefix of its overlays
SCAN_GROUP_TEXT = "Scan"
SUMMARY_TEXT = "Summary"
SUMMARY_KEY = "scan:summary"
OVERLAY_PREFIX = "scan:overlay:"
#: the group whose plots get an overlay
DEPTH_GROUP = "Depth profiles"
#: the colour choice that colours nothing
NO_COLOUR_TEXT = "none"
#: the text of a summary with no value to plot yet
NO_VALUES_TEXT = "No value to plot yet."
#: the text when no checked point has the outputs of an overlay's plot
NO_CHECKED_TEXT = "No checked point has the outputs for this plot."
#: the text when no point is checked for an overlay
CHECK_TEXT = "Check the points to draw."
#: the note of an overlay with more checked points than it draws
NOTE_TEXT = "Showing {shown} of {total} curves; use the filter to choose"

_BY_KEY = {plot.key: plot for plot in CATALOGUE}


@dataclass(frozen=True)
class PointEntry:
    """A point the tab can show: its label, and its folder relative to the run folder."""

    label: str
    rel_path: str


@dataclass(frozen=True)
class ScanRow:
    """A point of a scan that has outputs: its index (the table's index label), its label and its
    folder relative to the scan folder."""

    index: int
    label: str
    rel_path: str


def is_scan_key(key) -> bool:
    """True for the key of a scan view (the summary or an overlay)."""
    return key is not None and key.startswith("scan:")


def overlay_key(plot_key: str) -> str:
    """The key of the overlay of the depth profile ``plot_key``."""
    return OVERLAY_PREFIX + plot_key


def differs(doc: dict, snapshot: dict) -> bool:
    """True when the setup ``doc`` is not the setup ``snapshot`` (``_scan`` included)."""
    return clean(doc) != clean(snapshot) or doc.get("_scan") != snapshot.get("_scan")


def _label(names, values) -> str:
    """``ion.energy_keV = 10.0, …`` for a scanned point; ``Run`` for a point of a single run."""
    if not names:
        return "Run"
    return ", ".join(f"{name} = {values[name]}" for name in names)


def _finished_points(result, folder: Path) -> tuple[list[PointEntry], list[str]]:
    """The points of a finished run that can be shown, and the errors of the failed ones."""
    names = list(result.scan.param_names)
    entries, errors = [], []
    for _, row in result.table.iterrows():
        path = row.get("path")
        status = row.get("status")
        if status in SHOWN_STATUSES and isinstance(path, str) and has_outputs(folder / path):
            entries.append(PointEntry(_label(names, {n: row[n] for n in names}), path))
        elif status == "failed" and isinstance(row.get("error"), str):
            errors.append(row["error"])
    return entries, errors


def _live_points(state, folder: Path) -> tuple[list[PointEntry], list[str]]:
    """The points of a run that is going (or stopped) that can be shown, and the errors."""
    entries, errors = [], []
    for index in sorted(state.points):
        point = state.points[index]
        rel = f"points/{point.id[:16]}"
        if point.status in SHOWN_STATUSES and has_outputs(folder / rel):
            entries.append(PointEntry(_label(state.param_names, point.params), rel))
        elif point.status == "failed" and point.error:
            errors.append(point.error)
    return entries, errors


def _fill_combo(combo: QComboBox, items: list[str], default: str | None) -> None:
    """Fill ``combo`` with ``items``, keeping its text when it is still one of them."""
    current = combo.currentText()
    combo.blockSignals(True)
    combo.clear()
    combo.addItems(items)
    if current in items:
        combo.setCurrentIndex(items.index(current))
    elif default in items:
        combo.setCurrentIndex(items.index(default))
    combo.blockSignals(False)


class ResultsTab(QWidget):
    """The plots of the run's results. See the module docstring.

    ``controller`` is the run controller (its signals and ``running``, ``state``, ``result``,
    ``folder`` and ``snapshot``), ``document`` the setup, ``settings`` the app settings.
    """

    def __init__(self, controller, document, settings, parent=None):
        super().__init__(parent)
        self.controller = controller
        self.document = document
        self.settings = settings
        #: the pop-out windows that are open
        self.pop_outs: list[PlotWindow] = []
        self._entries: list[PointEntry] = []
        self._errors: list[str] = []
        self._results = None
        self._unreadable = False
        #: True when a finished run's points have no output folders (see outputs_missing)
        self._missing = False
        self._items: dict[str, QTreeWidgetItem] = {}
        #: the scan's table, and its points that have outputs (refreshed with the points)
        self._table = None
        self._total = 0
        self._scan_rows: list[ScanRow] = []
        self._overlay_signature = None
        self._timer = QTimer(self)

        self.banner = QLabel(BANNER_TEXT, self)
        self.banner.setWordWrap(True)
        self.empty_label = QLabel(NO_RESULTS_TEXT, self)
        self.empty_label.setWordWrap(True)
        self.plot_list = QTreeWidget(self)
        self.plot_list.setHeaderHidden(True)
        self.plot_list.setMinimumWidth(190)
        self._build_plot_items()
        self.point_label = QLabel(POINT_TEXT, self)
        self.point_combo = QComboBox(self)
        self.point_combo.setToolTip("The point whose results the plots show")
        self.canvas = PlotCanvas(self)
        self.pop_out = QPushButton(POP_OUT_TEXT, self)
        self.pop_out.setToolTip("Open the plot in a window of its own")
        self._build_scan_bars()

        point_row = QHBoxLayout()
        point_row.addWidget(self.point_label)
        point_row.addWidget(self.point_combo, 1)
        buttons = QHBoxLayout()
        buttons.addStretch(1)
        buttons.addWidget(self.pop_out)
        right = QVBoxLayout()
        right.addLayout(point_row)
        right.addWidget(self.banner)
        right.addWidget(self.empty_label)
        right.addWidget(self.summary_bar)
        right.addWidget(self.overlay_bar)
        right.addWidget(self.canvas, 1)
        right.addLayout(buttons)
        body = QHBoxLayout(self)
        body.addWidget(self.plot_list)
        body.addLayout(right, 1)

        self.plot_list.currentItemChanged.connect(self._on_plot_selected)
        self.point_combo.currentIndexChanged.connect(self._on_point_changed)
        self.pop_out.clicked.connect(self._on_pop_out)
        self._timer.timeout.connect(self._on_tick)
        controller.started.connect(self._on_started)
        controller.finished.connect(self._on_finished)
        controller.failed.connect(self._on_failed)
        document.changed.connect(self._update_banner)

        self._rebuild()
        self._refresh_view(draw=False)
        self._update_banner()

    # --- the plot list -------------------------------------------------------------

    def _build_plot_items(self) -> None:
        groups: dict[str, QTreeWidgetItem] = {}
        for plot in CATALOGUE:
            group = groups.get(plot.group)
            if group is None:
                group = QTreeWidgetItem(self.plot_list, [plot.group])
                group.setFlags(group.flags() & ~Qt.ItemFlag.ItemIsSelectable)
                group.setExpanded(True)
                groups[plot.group] = group
            item = QTreeWidgetItem(group, [plot.title])
            item.setData(0, Qt.ItemDataRole.UserRole, plot.key)
            self._items[plot.key] = item
        scan = QTreeWidgetItem(self.plot_list, [SCAN_GROUP_TEXT])
        scan.setFlags(scan.flags() & ~Qt.ItemFlag.ItemIsSelectable)
        scan.setExpanded(True)
        scan.setHidden(True)  # a single run has no scan views
        self._scan_group = scan
        self._add_scan_item(SUMMARY_KEY, SUMMARY_TEXT)
        for plot in CATALOGUE:
            if plot.group == DEPTH_GROUP:
                self._add_scan_item(overlay_key(plot.key), f"Overlay: {plot.title}")

    def _add_scan_item(self, key: str, text: str) -> None:
        item = QTreeWidgetItem(self._scan_group, [text])
        item.setData(0, Qt.ItemDataRole.UserRole, key)
        self._items[key] = item

    def _build_scan_bars(self) -> None:
        """The summary's choices; the overlay's points, filter and note (each for its view)."""
        self.summary_bar = QWidget(self)
        self.summary_x = QComboBox(self.summary_bar)
        self.summary_x.setToolTip("The parameter along the horizontal axis")
        self.summary_y = QComboBox(self.summary_bar)
        self.summary_y.setToolTip("The extracted value along the vertical axis")
        self.summary_colour = QComboBox(self.summary_bar)
        self.summary_colour.setToolTip("The parameter that colours the lines")
        row = QHBoxLayout(self.summary_bar)
        row.setContentsMargins(0, 0, 0, 0)
        for text, combo in (
            ("x", self.summary_x),
            ("y", self.summary_y),
            ("colour", self.summary_colour),
        ):
            row.addWidget(QLabel(text, self.summary_bar))
            row.addWidget(combo, 1)
        self.summary_bar.setVisible(False)
        self.summary_x.currentIndexChanged.connect(self._on_summary_changed)
        self.summary_y.currentIndexChanged.connect(self._on_summary_changed)
        self.summary_colour.currentIndexChanged.connect(self._on_summary_changed)

        self.overlay_bar = QWidget(self)
        self.overlay_points = QListWidget(self.overlay_bar)
        self.overlay_points.setMaximumHeight(150)
        self.overlay_points.setToolTip("The points drawn as curves (at most twelve)")
        self.overlay_filter = QLineEdit(self.overlay_bar)
        self.overlay_filter.setPlaceholderText("Filter, for example `ion.energy_keV` > 10")
        self.overlay_filter.setToolTip("Check the points that match this condition")
        self.overlay_status = QLabel(self.overlay_bar)
        self.overlay_status.setWordWrap(True)
        self.overlay_note = QLabel(self.overlay_bar)
        self.overlay_note.setWordWrap(True)
        filter_row = QHBoxLayout()
        filter_row.addWidget(QLabel("Filter", self.overlay_bar))
        filter_row.addWidget(self.overlay_filter, 1)
        column = QVBoxLayout(self.overlay_bar)
        column.setContentsMargins(0, 0, 0, 0)
        column.addWidget(self.overlay_points)
        column.addLayout(filter_row)
        column.addWidget(self.overlay_status)
        column.addWidget(self.overlay_note)
        self.overlay_bar.setVisible(False)
        self.overlay_status.setVisible(False)
        self.overlay_note.setVisible(False)
        self.overlay_filter.returnPressed.connect(self._apply_filter)
        self.overlay_points.itemChanged.connect(self._on_overlay_checked)

    def item_for(self, key: str) -> QTreeWidgetItem:
        """The list entry of the plot or scan view ``key``."""
        return self._items[key]

    def _selected_key(self) -> str | None:
        item = self.plot_list.currentItem()
        return None if item is None else item.data(0, Qt.ItemDataRole.UserRole)

    def _enabled(self, key: str) -> bool:
        return not self._items[key].isDisabled()

    def _show_availability(self) -> None:
        for plot in CATALOGUE:
            reason = (
                NO_RESULTS_TEXT if self._results is None else missing_reason(plot, self._results)
            )
            item = self._items[plot.key]
            item.setDisabled(reason is not None)
            item.setToolTip(0, reason or "")
        if self._scan_rows:
            scan_reason = None
        else:
            scan_reason = MISSING_OUTPUTS_TEXT if self._missing else NO_RESULTS_TEXT
        for key, item in self._items.items():
            if is_scan_key(key):
                item.setDisabled(scan_reason is not None)
                item.setToolTip(0, scan_reason or "")

    def _key_to_show(self) -> str | None:
        """The selected plot when it is available, else the first available one of the catalogue."""
        current = self._selected_key()
        if current is not None and self._enabled(current):
            return current
        for plot in CATALOGUE:
            if self._enabled(plot.key):
                return plot.key
        return None

    def _select(self, key: str) -> None:
        self.plot_list.blockSignals(True)
        self.plot_list.setCurrentItem(self._items[key])
        self.plot_list.blockSignals(False)

    def _on_plot_selected(self, current, _previous) -> None:
        if current is None or current.isDisabled():
            return
        if current.data(0, Qt.ItemDataRole.UserRole) is not None:
            self._refresh_view(draw=True)

    # --- the points -----------------------------------------------------------------

    def _current_points(self) -> tuple[list[PointEntry], list[str], int]:
        """The points that can be shown, the errors of the failed ones, and the point count."""
        folder = self.controller.folder
        result = self.controller.result
        if result is not None:
            if folder is None:
                return [], [], len(result.scan.points())
            entries, errors = _finished_points(result, Path(folder))
            return entries, errors, len(result.scan.points())
        state = self.controller.state
        if state is not None and folder is not None:
            entries, errors = _live_points(state, Path(folder))
            return entries, errors, state.n_points
        return [], [], 0

    def _scan_table(self):
        """The scan's table: the finished result's, else the live state's (None before a run)."""
        result = self.controller.result
        if result is not None:
            return result.table
        state = self.controller.state
        if state is not None and state.points:
            return scan_table(state)
        return None

    def _scan_names(self) -> list[str]:
        result = self.controller.result
        if result is not None:
            return list(result.scan.param_names)
        state = self.controller.state
        return list(state.param_names) if state is not None else []

    def _scan_points(self) -> list[ScanRow]:
        """The points of the scan's table that have outputs, in point order."""
        folder = self.controller.folder
        table = self._table
        if table is None or folder is None:
            return []
        names = self._scan_names()
        rows = []
        for index, row in table.iterrows():
            path = row.get("path")
            if (
                row.get("status") in SHOWN_STATUSES
                and isinstance(path, str)
                and has_outputs(Path(folder) / path)
            ):
                rows.append(ScanRow(int(index), point_label(names, row), path))
        return rows

    def _rebuild(self) -> None:
        """Take the points again. The chosen point is kept when it is still there."""
        entries, errors, total = self._current_points()
        self._entries = entries
        self._errors = errors
        self._total = total
        self._missing = outputs_missing(self.controller.result, self.controller.folder)
        self._table = self._scan_table()
        self._scan_rows = self._scan_points()
        self._scan_group.setHidden(total <= 1)
        previous = self.point_combo.currentData()
        self.point_combo.blockSignals(True)
        self.point_combo.clear()
        for entry in entries:
            self.point_combo.addItem(entry.label, entry.rel_path)
        index = self.point_combo.findData(previous) if previous is not None else -1
        if index < 0 and entries:
            index = 0
        self.point_combo.setCurrentIndex(index)
        self.point_combo.blockSignals(False)

    def _open_selected(self):
        """The results of the chosen point, or None (and whether they could not be read)."""
        rel = self.point_combo.currentData()
        self._unreadable = False
        if rel is None or self.controller.folder is None:
            return None
        try:
            return open_point(Path(self.controller.folder), rel)
        except (OSError, ValueError, SpecError):
            self._unreadable = True
            return None

    def _empty_text(self) -> str:
        if self._unreadable:
            return UNREADABLE_TEXT
        if self._missing:
            return MISSING_OUTPUTS_TEXT
        if self._errors:
            return f"{NO_RESULTS_TEXT}\n{self._errors[0]}"
        return NO_RESULTS_TEXT

    def _show_point_choice(self, visible: bool) -> None:
        self.point_label.setVisible(visible)
        self.point_combo.setVisible(visible)

    def _refresh_view(self, draw: bool, refresh: bool = False) -> None:
        """Open the chosen point, grey out the plots it cannot give, and show the plot.

        ``draw`` asks for the shown plot to be drawn (once); ``refresh`` makes it a refresh of
        a running run (see :meth:`PlotCanvas.refresh`). A selection that only follows the
        availability is not drawn here.
        """
        self._results = self._open_selected()
        self._show_availability()
        key = self._key_to_show()
        if is_scan_key(key):
            self._show_scan_view(key, draw=draw, refresh=refresh)
            return
        self.summary_bar.setVisible(False)
        self.overlay_bar.setVisible(False)
        self._show_point_choice(self._total > 1)
        shown = self._results is not None
        self.canvas.setVisible(shown)
        self.empty_label.setText(self._empty_text() if not shown else "")
        self.empty_label.setVisible(not shown)
        self.pop_out.setEnabled(shown)
        if not shown:
            return
        key = self._key_to_show()
        if key is None:
            self.canvas.setVisible(False)
            self.empty_label.setText(NOTHING_TEXT)
            self.empty_label.setVisible(True)
            self.pop_out.setEnabled(False)
            return
        self._select(key)
        if draw:
            self._draw(self.canvas, _BY_KEY[key], self._results, refresh)

    @staticmethod
    def _draw(canvas: PlotCanvas, plot, results, refresh: bool) -> None:
        if refresh:
            canvas.refresh(plot, results)
        else:
            canvas.show_plot(plot, results)

    def _on_point_changed(self, _index: int) -> None:
        self._refresh_view(draw=self.isVisible())

    def showEvent(self, event):  # noqa: N802 (Qt's name)
        # A hidden tab does not draw while a run goes; the plot is drawn when the tab shows.
        super().showEvent(event)
        if self.controller.running or self.controller.result is not None:
            self._rebuild()
            self._refresh_view(draw=True)

    # --- the scan views -------------------------------------------------------------

    def _show_scan_view(self, key: str, draw: bool, refresh: bool = False) -> None:
        """Show a scan view (the summary or an overlay): its choices, and its plot if ``draw``."""
        self._select(key)
        self.empty_label.setVisible(False)
        self.canvas.setVisible(True)
        self.pop_out.setEnabled(False)
        self._show_point_choice(False)
        is_summary = key == SUMMARY_KEY
        self.summary_bar.setVisible(is_summary)
        self.overlay_bar.setVisible(not is_summary)
        if is_summary:
            self._fill_summary_choices()
        else:
            self._fill_overlay_points()
        if draw:
            self._draw_scan(key, refresh)

    def _fill_summary_choices(self) -> None:
        names = self._scan_names()
        values = value_columns(self._table, names) if self._table is not None else []
        _fill_combo(self.summary_x, names, names[0] if names else None)
        _fill_combo(self.summary_y, values, values[0] if values else None)
        colours = [*names, NO_COLOUR_TEXT]
        _fill_combo(self.summary_colour, colours, names[1] if len(names) > 1 else NO_COLOUR_TEXT)

    def _summary_choice(self) -> tuple[str | None, str | None, str | None]:
        """The summary's ``x``, ``y`` and colour (None for no colour, or a missing choice)."""
        x = self.summary_x.currentText() or None
        y = self.summary_y.currentText() or None
        colour = self.summary_colour.currentText()
        hue = None if colour in ("", NO_COLOUR_TEXT) else colour
        return x, y, hue

    def _on_summary_changed(self, _index: int) -> None:
        self._redraw_selected_scan()

    def _fill_overlay_points(self) -> None:
        """The check list of the points with outputs. A point keeps its check while it is listed;
        a new point is checked while fewer than :data:`CURVE_LIMIT` are."""
        signature = [(row.index, row.label) for row in self._scan_rows]
        if signature == self._overlay_signature:
            self._update_overlay_note()
            return
        kept = {}
        for row in range(self.overlay_points.count()):
            item = self.overlay_points.item(row)
            kept[item.data(Qt.ItemDataRole.UserRole)] = item.checkState() == Qt.CheckState.Checked
        self.overlay_points.blockSignals(True)
        self.overlay_points.clear()
        checked = 0
        for scan_row in self._scan_rows:
            if scan_row.index in kept:
                is_checked = kept[scan_row.index]
            else:
                is_checked = checked < CURVE_LIMIT
            checked += is_checked
            item = QListWidgetItem(scan_row.label)
            item.setFlags(item.flags() | Qt.ItemFlag.ItemIsUserCheckable)
            item.setCheckState(Qt.CheckState.Checked if is_checked else Qt.CheckState.Unchecked)
            item.setData(Qt.ItemDataRole.UserRole, scan_row.index)
            self.overlay_points.addItem(item)
        self.overlay_points.blockSignals(False)
        self._overlay_signature = signature
        self._update_overlay_note()

    def _checked_rows(self) -> list[ScanRow]:
        """The points that are checked, in point order."""
        by_index = {row.index: row for row in self._scan_rows}
        rows = []
        for position in range(self.overlay_points.count()):
            item = self.overlay_points.item(position)
            if item.checkState() == Qt.CheckState.Checked:
                rows.append(by_index[item.data(Qt.ItemDataRole.UserRole)])
        return rows

    def _update_overlay_note(self) -> None:
        total = len(self._checked_rows())
        note = NOTE_TEXT.format(shown=CURVE_LIMIT, total=total) if total > CURVE_LIMIT else ""
        self.overlay_note.setText(note)
        self.overlay_note.setVisible(bool(note))

    def _on_overlay_checked(self, _item) -> None:
        self._update_overlay_note()
        self._redraw_selected_scan()

    def _apply_filter(self) -> None:
        """Check the points that match the filter (an empty filter changes nothing)."""
        text = self.overlay_filter.text()
        if not text.strip():
            self._set_overlay_status("")
            return
        if self._table is None:
            return
        try:
            matched = set(select_points(self._table, text))
        except ValueError as error:
            self._set_overlay_status(str(error))
            return
        self._set_overlay_status("")
        self.overlay_points.blockSignals(True)
        for position in range(self.overlay_points.count()):
            item = self.overlay_points.item(position)
            checked = item.data(Qt.ItemDataRole.UserRole) in matched
            item.setCheckState(Qt.CheckState.Checked if checked else Qt.CheckState.Unchecked)
        self.overlay_points.blockSignals(False)
        self._update_overlay_note()
        self._redraw_selected_scan()

    def _set_overlay_status(self, text: str) -> None:
        self.overlay_status.setText(text)
        self.overlay_status.setVisible(bool(text))

    def _redraw_selected_scan(self) -> None:
        """A change of a choice draws the selected scan view again, when the tab is shown."""
        key = self._selected_key()
        if is_scan_key(key) and self.isVisible():
            self._draw_scan(key, refresh=False)

    def _draw_scan(self, key: str, refresh: bool) -> None:
        """Draw a scan view from the current table and choices (in a thread, as a plot is)."""
        if key == SUMMARY_KEY:
            self._draw_summary(refresh)
        else:
            self._draw_overlay(key, refresh)

    def _draw_summary(self, refresh: bool) -> None:
        table = self._table
        x, y, hue = self._summary_choice()

        def load():
            if table is None or x is None or y is None:
                raise ValueError(NO_VALUES_TEXT)
            data = summary_data(table, x, y, hue)
            if data.empty:
                raise ValueError(NO_VALUES_TEXT)
            return data

        def draw(frame, ax):
            draw_summary(frame, x, y, hue, point_styles(frame, hue), ax)

        self._send(SUMMARY_KEY, load, draw, refresh)

    def _draw_overlay(self, key: str, refresh: bool) -> None:
        plot = _BY_KEY[key[len(OVERLAY_PREFIX) :]]
        folder = Path(self.controller.folder) if self.controller.folder is not None else None
        chosen = self._checked_rows()[:CURVE_LIMIT]
        styles = point_styles(self._table, None) if self._table is not None else {}
        labels = {row.index: row.label for row in chosen}

        def load():
            if not chosen or folder is None:
                raise ValueError(CHECK_TEXT)
            frames = {}
            for row in chosen:
                try:
                    frames[row.index] = plot_data(plot, open_point(folder, row.rel_path))
                except (ValueError, OSError, SpecError):
                    continue  # this point has no outputs for the plot
            if not frames:
                raise ValueError(NO_CHECKED_TEXT)
            stacked = stack_frames(frames)
            # the points that could not be read are counted for the plot's note
            stacked.attrs["missing"] = len(chosen) - len(frames)
            stacked.attrs["total"] = len(chosen)
            return stacked

        def draw(frame, ax):
            draw_overlay(
                plot,
                split_frames(frame),
                styles,
                labels,
                ax,
                missing=frame.attrs.get("missing", 0),
                total=frame.attrs.get("total", 0),
            )

        self._send(key, load, draw, refresh)

    def _send(self, key, load, draw, refresh: bool) -> None:
        if refresh:
            self.canvas.refresh_job(key, load, draw)
        else:
            self.canvas.show_job(key, load, draw)

    # --- the run ----------------------------------------------------------------------

    def _on_started(self) -> None:
        self._update_banner()
        self._timer.setInterval(self.settings.refresh_seconds * 1000)
        self._timer.start()
        self._rebuild()
        self._refresh_view(draw=self.isVisible())

    def _on_finished(self, _result=None) -> None:
        self._timer.stop()
        self._rebuild()
        self._refresh_view(draw=self.isVisible())
        self._update_banner()

    def _on_failed(self, _message: str) -> None:
        self._timer.stop()
        self._rebuild()
        self._refresh_view(draw=self.isVisible())

    def _on_tick(self) -> None:
        """While a run goes, every refresh interval: the shown plot is drawn again only while this
        tab is shown, and each visible pop-out is drawn again whatever tab is shown."""
        if not self.controller.running:
            self._timer.stop()
            return
        visible = [window for window in self.pop_outs if window.isVisible()]
        if not self.isVisible() and not visible:
            return
        self._rebuild()
        self._refresh_view(draw=self.isVisible(), refresh=True)
        if self._results is None:
            return
        for window in visible:
            window.canvas.refresh(window.plot, self._results)

    # --- the banner and the pop-outs -----------------------------------------------------

    def _update_banner(self) -> None:
        snapshot = self.controller.snapshot
        self.banner.setVisible(snapshot is not None and differs(self.document.doc, snapshot))

    def _on_pop_out(self) -> None:
        key = self._selected_key()
        if key is None or is_scan_key(key) or self._results is None or not self._enabled(key):
            return
        plot = _BY_KEY[key]
        window = PlotWindow(plot)
        window.closed.connect(self._on_pop_out_closed)
        self.pop_outs.append(window)
        window.canvas.show_plot(plot, self._results)
        window.show()

    def _on_pop_out_closed(self, window) -> None:
        if window in self.pop_outs:
            self.pop_outs.remove(window)
