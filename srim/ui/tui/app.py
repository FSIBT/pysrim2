"""The live views of a scan, drawn with Textual: the plot (the default) and the dashboard.

:class:`ScanApp` takes the events of a scan from a queue (the scan runs in another thread and
puts every event there, see ``pysrim2 scan run``). Every ``REFRESH_SECONDS`` it applies all
the events waiting in the queue at once, then redraws the current view: updates are coalesced,
so a slow terminal never holds up the workers. ``None`` in the queue means the scan thread has
ended, and the app exits.

Keys: ``p`` pauses or resumes the scheduling (``pause_event``), ``q`` (or ``ctrl+c``) stops
the scan cleanly (``stop_event``; the app exits when the scan has ended), ``v`` switches between
the plot and the dashboard, and ``l`` opens the ``error.log`` of the selected failed point
(on the dashboard) or of the last failed point.

The dashboard's scan-points table sorts by a column when its header is selected.
"""

from __future__ import annotations

import queue
import threading
import time
from pathlib import Path

from rich.text import Text
from textual.app import App
from textual.binding import Binding
from textual.containers import VerticalScroll
from textual.coordinate import Coordinate
from textual.screen import ModalScreen, Screen
from textual.widgets import DataTable, Footer, ProgressBar, Static
from textual_plotext import PlotextPlot

from .plot import build, choose_axes, draw
from .state import ScanState, format_hms, sparkline, status_line

#: seconds between two redraws (and two drains of the event queue)
REFRESH_SECONDS = 0.25
#: the warnings the dashboard shows
WARNINGS_SHOWN = 5


def _read(path: Path | None) -> str | None:
    if path is None:
        return None
    try:
        return path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return None


class ScanApp(App):
    """The live view of one scan. ``view`` is ``"plot"`` or ``"dashboard"`` (the first one
    shown); ``plot`` is the ``plot=`` argument of the views (see :func:`~.plot.check_plot`)."""

    TITLE = "pysrim2 scan"
    BINDINGS = [
        Binding("p", "toggle_pause", "pause/resume"),
        Binding("q", "stop", "stop"),
        Binding("ctrl+c", "stop", "stop", priority=True, show=False),
        Binding("v", "switch_view", "plot/dashboard"),
        Binding("l", "show_log", "error log"),
    ]

    def __init__(
        self,
        scan,
        events: queue.Queue,
        *,
        stop_event: threading.Event | None = None,
        pause_event: threading.Event | None = None,
        view: str = "plot",
        plot=None,
        clock=time.time,
    ):
        super().__init__()
        self.state = ScanState(scan)
        self.events = events
        self.stop_event = stop_event if stop_event is not None else threading.Event()
        self.pause_event = pause_event if pause_event is not None else threading.Event()
        self.view_name = "dashboard" if view == "dashboard" else "plot"
        self.plot_spec = plot
        self.clock = clock
        #: a line the views show after the status (``stopping…``)
        self.note = ""
        self.ended = False

    def on_mount(self) -> None:
        self.install_screen(PlotScreen(), "plot")
        self.install_screen(DashboardScreen(), "dashboard")
        self.push_screen(self.view_name)  # switch_screen is for later (see action_switch_view)
        self.set_interval(REFRESH_SECONDS, self.tick)

    # -- the loop -----------------------------------------------------------------------------

    def tick(self) -> None:
        """Apply the waiting events, then redraw the current view (or exit at the end)."""
        self.drain()
        if self.ended:
            self.exit()
            return
        if isinstance(self.screen, PlotScreen | DashboardScreen):
            self.screen.update_from(self)

    def drain(self) -> None:
        """Apply every event waiting in the queue; ``None`` ends the scan."""
        while not self.ended:
            try:
                event = self.events.get_nowait()
            except queue.Empty:
                return
            if event is None:
                self.ended = True
                return
            self.state.apply(event)

    # -- keys ----------------------------------------------------------------------------------

    def action_toggle_pause(self) -> None:
        if self.pause_event.is_set():
            self.pause_event.clear()
        else:
            self.pause_event.set()

    def action_stop(self) -> None:
        self.stop_event.set()
        self.note = "stopping…"

    def action_switch_view(self) -> None:
        self.view_name = "plot" if self.view_name == "dashboard" else "dashboard"
        self.switch_screen(self.view_name)

    def action_show_log(self) -> None:
        row = None
        if isinstance(self.screen, DashboardScreen):
            selected = self.screen.selected_row()
            if selected is not None and selected.status == "failed":
                row = selected
        if row is None:
            row = self.state.failed_point()
        if row is None:
            self.notify("no failed point yet")
            return
        text = _read(self.state.log_path(row.index)) or row.error or "no error.log"
        self.push_screen(LogScreen(f"point {row.index}: error.log", text))


class PlotScreen(Screen):
    """The plot of one result against the scanned parameters, and the status strip."""

    DEFAULT_CSS = """
    PlotScreen PlotextPlot { height: 1fr; }
    PlotScreen #strip { height: 1; padding: 0 1; }
    """

    def compose(self):
        yield PlotextPlot(id="plot")
        yield Static(id="strip")
        yield Footer()

    def on_mount(self) -> None:
        self._drawn = None
        self.data = None
        self.status_text = ""

    def update_from(self, app: ScanApp) -> None:
        """Redraw the plot when the state or the axes changed, and the status strip."""
        state = app.state
        axes = choose_axes(state, app.plot_spec)
        if (state.version, axes) != self._drawn:
            self.data = build(state, *axes)
            widget = self.query_one(PlotextPlot)
            draw(widget.plt, self.data)
            widget.refresh()
            self._drawn = (state.version, axes)
        self.status_text = status_line(state, app.clock(), app.note)
        self.query_one("#strip", Static).update(Text(self.status_text))

    def on_screen_resume(self) -> None:
        self.update_from(self.app)


class DashboardScreen(Screen):
    """Overall counts, a gauge, the workers, a throughput sparkline, the points table (sorted
    by a column when its header is selected) and the latest warnings and errors."""

    DEFAULT_CSS = """
    DashboardScreen #overall { height: auto; padding: 0 1; }
    DashboardScreen #gauge { margin: 0 1; }
    DashboardScreen #workers { height: auto; max-height: 8; }
    DashboardScreen #throughput { height: 1; padding: 0 1; }
    DashboardScreen #points { height: 1fr; }
    DashboardScreen #warnings { height: auto; max-height: 7; padding: 0 1; }
    """

    def __init__(self):
        super().__init__()
        self.sort_column = "index"
        self.sort_reverse = False
        #: the table's column ids, in order (the table is rebuilt when they change)
        self.column_ids: tuple = ()
        self._drawn = None  # what the points table shows (see _update_points)
        self._workers_drawn = None  # what the workers table shows

    def compose(self):
        yield Static(id="overall")
        yield ProgressBar(show_eta=False, id="gauge")
        yield DataTable(id="workers", cursor_type="none")
        yield Static(id="throughput")
        yield DataTable(id="points", cursor_type="row")
        yield Static(id="warnings")
        yield Footer()

    @property
    def table(self) -> DataTable:
        return self.query_one("#points", DataTable)

    def on_mount(self) -> None:
        self.query_one("#workers", DataTable).add_columns(
            "worker", "point", "ions", "ions/s", "cpu"
        )

    def on_screen_resume(self) -> None:
        self._drawn = None
        self.update_from(self.app)

    def update_from(self, app: ScanApp) -> None:
        state = app.state
        counts = state.counts()
        done = counts["done"] + counts["cached"]
        eta = state.eta_seconds()
        eta_text = "?" if eta is None else format_hms(eta)
        flags = " ".join(flag for flag in ("paused" if state.paused else "", app.note) if flag)
        overall = (
            f"points: done {done} (cached {counts['cached']})  running {counts['running']}  "
            f"queued {counts['queued']}  failed {counts['failed']}  "
            f"stopped {counts['stopped']}  of {state.n_points}\n"
            f"ions {state.total_ions()}  elapsed {format_hms(state.elapsed(app.clock()))}  "
            f"eta {eta_text}  workers {state.n_workers}  {flags}"
        )
        self.query_one("#overall", Static).update(Text(overall))
        self.query_one("#gauge", ProgressBar).update(
            total=max(state.n_points, 1), progress=done + counts["failed"] + counts["stopped"]
        )
        self._update_workers(state)
        last = f"{state.throughput[-1]:.4g}" if state.throughput else "—"
        line = f"ions/s {sparkline(state.throughput) or '—'}  last {last}"
        self.query_one("#throughput", Static).update(Text(line))
        self._update_points(state)
        lines = [f"{level}: {text}" for level, text in state.last_warnings(WARNINGS_SHOWN)]
        self.query_one("#warnings", Static).update(Text("\n".join(lines) or "no warnings"))

    def _update_workers(self, state: ScanState) -> None:
        table = self.query_one("#workers", DataTable)
        rows = [
            (
                worker.ident,
                "idle" if worker.point is None else worker.point,
                "" if worker.point is None else state.points[worker.point].ions,
                "—" if worker.ions_per_s is None else f"{worker.ions_per_s:.4g}",
                "n/a" if worker.cpu_percent is None else f"{worker.cpu_percent:.0f}%",
            )
            for worker in state.workers.values()
        ]
        if rows == self._workers_drawn:
            return
        self._workers_drawn = rows
        table.clear()
        for row in rows:
            table.add_row(*(str(cell) for cell in row), key=str(row[0]))

    def _update_points(self, state: ScanState) -> None:
        columns = state.table_columns()
        ids = tuple(column for column, _ in columns)
        table = self.table
        version = (state.version, self.sort_column, self.sort_reverse, ids)
        if version == self._drawn:
            return
        selected = self.selected_index()
        if ids != self.column_ids:
            table.clear(columns=True)
            for column, label in columns:
                table.add_column(label, key=column)
            self.column_ids = ids
        table.clear()
        for row in state.sorted_rows(self.sort_column, self.sort_reverse):
            cells = [state.cell(row, column) for column in ids]
            table.add_row(*cells, key=str(row.index))
        self._restore_selection(selected)
        self._drawn = version

    def _restore_selection(self, index) -> None:
        if index is None:
            return
        for position, row in enumerate(self.table.ordered_rows):
            if row.key.value == str(index):
                self.table.move_cursor(row=position)
                return

    def selected_index(self) -> int | None:
        """The index of the point on the cursor row of the points table (None if none)."""
        table = self.table
        if table.row_count == 0:
            return None
        try:
            key = table.coordinate_to_cell_key(Coordinate(table.cursor_row, 0)).row_key.value
        except Exception:  # the cursor is not on a row yet
            return None
        return int(key)

    def selected_row(self):
        """The state row of the selected point (None when no point is selected)."""
        index = self.selected_index()
        if index is None:
            return None
        return self.app.state.points.get(index)

    def sort_by(self, column: str, reverse: bool = False) -> None:
        """Sort the points table by ``column`` (an id of the state's table columns)."""
        self.sort_column = column
        self.sort_reverse = reverse
        self._drawn = None
        self._update_points(self.app.state)

    def on_data_table_header_selected(self, event: DataTable.HeaderSelected) -> None:
        if event.data_table.id != "points":
            return
        column = event.column_key.value
        reverse = column == self.sort_column and not self.sort_reverse
        self.sort_by(column, reverse=reverse)


class LogScreen(ModalScreen):
    """The text of an ``error.log``; ``escape`` (or ``l``) closes it."""

    DEFAULT_CSS = """
    LogScreen { align: center middle; }
    LogScreen VerticalScroll { width: 90%; height: 80%; border: round $accent; padding: 0 1; }
    """
    BINDINGS = [Binding("escape", "close", "close"), Binding("l", "close", "close", show=False)]

    def __init__(self, title: str, text: str):
        super().__init__()
        self.title_text = title
        self.text = text

    def compose(self):
        with VerticalScroll():
            yield Static(Text(f"{self.title_text}\n\n{self.text}"))
        yield Footer()

    def action_close(self) -> None:
        self.dismiss()


__all__ = ["REFRESH_SECONDS", "DashboardScreen", "LogScreen", "PlotScreen", "ScanApp"]
