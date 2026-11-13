"""The Results tab: the plots of a run's results, one point at a time.

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
    QPushButton,
    QTreeWidget,
    QTreeWidgetItem,
    QVBoxLayout,
    QWidget,
)

from srim.app.model import clean
from srim.app.plot_canvas import PlotCanvas, PlotWindow
from srim.app.plots import CATALOGUE, missing_reason
from srim.app.runs import SHOWN_STATUSES, open_point
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

_BY_KEY = {plot.key: plot for plot in CATALOGUE}


@dataclass(frozen=True)
class PointEntry:
    """A point the tab can show: its label, and its folder relative to the run folder."""

    label: str
    rel_path: str


def differs(doc: dict, snapshot: dict) -> bool:
    """True when the setup ``doc`` is not the setup ``snapshot`` (the ``_scan`` section included)."""
    return clean(doc) != clean(snapshot) or doc.get("_scan") != snapshot.get("_scan")


def _label(names, values) -> str:
    """``ion.energy_keV = 10.0, …`` for a scanned point; ``Run`` for a point of a single run."""
    if not names:
        return "Run"
    return ", ".join(f"{name} = {values[name]}" for name in names)


def _has_outputs(point_dir: Path) -> bool:
    """A point's folder holds its run.yaml (or units.csv, when it was batched)."""
    return (point_dir / "run.yaml").is_file() or (point_dir / "units.csv").is_file()


def _finished_points(result, folder: Path) -> tuple[list[PointEntry], list[str]]:
    """The points of a finished run that can be shown, and the errors of the failed ones."""
    names = list(result.scan.param_names)
    entries, errors = [], []
    for _, row in result.table.iterrows():
        path = row.get("path")
        status = row.get("status")
        if status in SHOWN_STATUSES and isinstance(path, str) and _has_outputs(folder / path):
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
        if point.status in SHOWN_STATUSES and _has_outputs(folder / rel):
            entries.append(PointEntry(_label(state.param_names, point.params), rel))
        elif point.status == "failed" and point.error:
            errors.append(point.error)
    return entries, errors


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
        self._items: dict[str, QTreeWidgetItem] = {}
        self._timer = QTimer(self)

        self.banner = QLabel(BANNER_TEXT, self)
        self.banner.setWordWrap(True)
        self.empty_label = QLabel(NO_RESULTS_TEXT, self)
        self.empty_label.setWordWrap(True)
        self.plot_list = QTreeWidget(self)
        self.plot_list.setHeaderHidden(True)
        self.plot_list.setMinimumWidth(190)
        self._build_plot_items()
        self.point_combo = QComboBox(self)
        self.point_combo.setToolTip("The point whose results the plots show")
        self.canvas = PlotCanvas(self)
        self.pop_out = QPushButton(POP_OUT_TEXT, self)
        self.pop_out.setToolTip("Open the plot in a window of its own")

        point_row = QHBoxLayout()
        point_row.addWidget(QLabel(POINT_TEXT, self))
        point_row.addWidget(self.point_combo, 1)
        buttons = QHBoxLayout()
        buttons.addStretch(1)
        buttons.addWidget(self.pop_out)
        right = QVBoxLayout()
        right.addLayout(point_row)
        right.addWidget(self.banner)
        right.addWidget(self.empty_label)
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

    def item_for(self, key: str) -> QTreeWidgetItem:
        """The list entry of the plot ``key``."""
        return self._items[key]

    def _selected_key(self) -> str | None:
        item = self.plot_list.currentItem()
        return None if item is None else item.data(0, Qt.ItemDataRole.UserRole)

    def _enabled(self, key: str) -> bool:
        return not self._items[key].isDisabled()

    def _show_availability(self) -> None:
        for plot in CATALOGUE:
            reason = NO_RESULTS_TEXT if self._results is None else missing_reason(plot, self._results)
            item = self._items[plot.key]
            item.setDisabled(reason is not None)
            item.setToolTip(0, reason or "")

    def _key_to_show(self) -> str | None:
        """The selected plot when it is available, else the first available one."""
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
        if current is None or current.isDisabled() or self._results is None:
            return
        key = current.data(0, Qt.ItemDataRole.UserRole)
        if key is not None:
            self.canvas.show_plot(_BY_KEY[key], self._results)

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

    def _rebuild(self) -> None:
        """Take the points again. The chosen point is kept when it is still there."""
        entries, errors, total = self._current_points()
        self._entries = entries
        self._errors = errors
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
        self.point_combo.setVisible(total > 1)

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
        if self._errors:
            return f"{NO_RESULTS_TEXT}\n{self._errors[0]}"
        return NO_RESULTS_TEXT

    def _refresh_view(self, draw: bool) -> None:
        """Open the chosen point, grey out the plots it cannot give, and show the plot.

        ``draw`` asks for the shown plot to be drawn (once); a selection that only follows the
        availability is not drawn here.
        """
        self._results = self._open_selected()
        self._show_availability()
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
            self.canvas.show_plot(_BY_KEY[key], self._results)

    def _on_point_changed(self, _index: int) -> None:
        self._refresh_view(draw=True)

    # --- the run ----------------------------------------------------------------------

    def _on_started(self) -> None:
        self._update_banner()
        self._timer.setInterval(self.settings.refresh_seconds * 1000)
        self._timer.start()
        self._rebuild()
        self._refresh_view(draw=True)

    def _on_finished(self, _result=None) -> None:
        self._timer.stop()
        self._rebuild()
        self._refresh_view(draw=True)
        self._update_banner()

    def _on_failed(self, _message: str) -> None:
        self._timer.stop()
        self._rebuild()
        self._refresh_view(draw=True)

    def _on_tick(self) -> None:
        """While a run goes: take the points again, draw the shown plot and the visible pop-outs."""
        if not self.controller.running:
            self._timer.stop()
            return
        self._rebuild()
        self._refresh_view(draw=True)
        if self._results is None:
            return
        for window in list(self.pop_outs):
            if window.isVisible():
                window.canvas.show_plot(window.plot, self._results)

    # --- the banner and the pop-outs -----------------------------------------------------

    def _update_banner(self) -> None:
        snapshot = self.controller.snapshot
        self.banner.setVisible(snapshot is not None and differs(self.document.doc, snapshot))

    def _on_pop_out(self) -> None:
        key = self._selected_key()
        if key is None or self._results is None or not self._enabled(key):
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
