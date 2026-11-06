"""The live views of a scan, drawn with Textual: the plot (the default) and the dashboard.

:class:`ScanApp` takes the events of a scan from a queue (the scan runs in another thread and
puts every event there, see ``pysrim2 scan run``). Every ``REFRESH_SECONDS`` it applies all
the events waiting in the queue at once, then redraws the current view: updates are coalesced,
so a slow terminal never holds up the workers. ``None`` in the queue means the scan thread has
ended, and the app exits. It stays open after the end (``wait``, the default) until ``q``
or ctrl+c; ``wait=False`` closes it at once.

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
#: the note of the views once the scan has ended and the app waits for ``q``
FINISHED_NOTE = "finished — press q to quit"


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
        Binding("ctrl+c", "interrupt", "stop", priority=True, show=False),
        Binding("v", "switch_view", "plot/dashboard"),
        Binding("l", "show_log", "error log"),
    ]
    #: the keys that do nothing while a modal (the log) is open; ctrl+c always works
    MODAL_BLOCKED = frozenset({"toggle_pause", "stop", "switch_view", "show_log"})

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
        wait: bool = True,
    ):
        super().__init__()
        self.state = ScanState(scan)
        self.events = events
        self.stop_event = stop_event if stop_event is not None else threading.Event()
        self.pause_event = pause_event if pause_event is not None else threading.Event()
        self.view_name = "dashboard" if view == "dashboard" else "plot"
        self.plot_spec = plot
        self.clock = clock
        #: True: the app stays open after the scan has ended, until q or ctrl+c
        self.wait = wait
        #: a line the views show after the status (``stopping…``)
        self.note = ""
        self.ended = False
        self.hint_shown = False  # "press ctrl+c again to quit now" has been shown

    def on_mount(self) -> None:
        self.install_screen(PlotScreen(), "plot")
        self.install_screen(DashboardScreen(), "dashboard")
        self.push_screen(self.view_name)  # switch_screen is for later (see action_switch_view)
        self.set_interval(REFRESH_SECONDS, self.tick)

    # -- the loop -----------------------------------------------------------------------------

    def tick(self) -> None:
        """Apply the waiting events, then redraw the current view (or exit at the end)."""
        self.drain()
        if self.ended and not self.holding:
            self.exit()
            return
        if isinstance(self.screen, PlotScreen | DashboardScreen):
            self.screen.update_from(self)

    def scan_over(self) -> bool:
        """True once the scan has ended: its ``scan_finished`` came, or its thread did."""
        return self.state.finished or self.ended

    @property
    def holding(self) -> bool:
        """The app waits for ``q`` after the scan has finished: not when the user stopped
        the scan (``q`` or SIGTERM set ``stop_event``) or when it is not waiting (``wait``)."""
        return self.wait and self.state.finished and not self.stop_event.is_set()

    @property
    def status_note(self) -> str:
        """The line the views show after the status: a note of the keys, or the end note."""
        if self.note:
            return self.note
        return FINISHED_NOTE if self.holding else ""

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
            self.take(event)

    def take(self, event) -> None:
        """Apply one event of the scan to the state (a subclass may act on other items)."""
        self.state.apply(event)

    # -- keys ----------------------------------------------------------------------------------

    def action_toggle_pause(self) -> None:
        if self.scan_over():  # nothing is left to pause
            return
        if self.pause_event.is_set():
            self.pause_event.clear()
        else:
            self.pause_event.set()

    def check_action(self, action, parameters):
        if isinstance(self.screen, ModalScreen) and action in self.MODAL_BLOCKED:
            return False
        return super().check_action(action, parameters)

    def action_stop(self) -> None:
        """``q``: stop the scan cleanly. Pressed again while it stops, it says how to quit.
        Once the scan has ended, ``q`` quits the app."""
        if self.scan_over():
            self.exit()
            return
        if not self.stop_event.is_set():
            self.stop_event.set()
            self.note = "stopping…"
        elif not self.hint_shown:
            self.hint_shown = True
            self.note = "still stopping… press ctrl+c again to quit now"

    def action_interrupt(self) -> None:
        """``ctrl+c``: stop the scan; once it is stopping, the next press quits the app now
        (the scan thread goes on stopping in the background, see ``_run_in_app``)."""
        if self.stop_event.is_set() and self.hint_shown:
            self.exit()
            return
        self.action_stop()

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


class _LiveScreen(Screen):
    """A view that redraws only what changed: a text widget is set only when its text is
    different from the one it shows."""

    def __init__(self):
        super().__init__()
        self._shown: dict[str, str] = {}

    def forget(self) -> None:
        """Show everything again at the next update (the state was replaced)."""
        self._shown.clear()

    def _show(self, widget_id: str, text: str) -> None:
        if self._shown.get(widget_id) != text:
            self._shown[widget_id] = text
            self.query_one(f"#{widget_id}", Static).update(Text(text))


class PlotScreen(_LiveScreen):
    """The plot of one result against the scanned parameters, and the status strip. The plot
    is drawn again only when the data changed (``state.data_version``) or the axes did."""

    DEFAULT_CSS = """
    PlotScreen PlotextPlot { height: 1fr; }
    PlotScreen #strip { height: 1; padding: 0 1; }
    """

    def __init__(self):
        super().__init__()
        self.data = None
        self.plot_rebuilds = 0  # how often the plot was drawn (tests watch it)
        self._drawn = None  # (data_version, axes) of the plot on screen

    def forget(self) -> None:
        super().forget()
        self._drawn = None

    def compose(self):
        yield PlotextPlot(id="plot")
        yield Static(id="strip")
        yield Footer()

    @property
    def status_text(self) -> str:
        return self._shown.get("strip", "")

    def update_from(self, app: ScanApp) -> None:
        state = app.state
        axes = choose_axes(state, app.plot_spec)
        if (state.data_version, axes) != self._drawn:
            self.data = build(state, *axes)
            widget = self.query_one(PlotextPlot)
            draw(widget.plt, self.data)
            widget.refresh()
            self._drawn = (state.data_version, axes)
            self.plot_rebuilds += 1
        self._show("strip", status_line(state, app.clock(), app.status_note))

    def on_screen_resume(self) -> None:
        self.update_from(self.app)


class DashboardScreen(_LiveScreen):
    """Overall counts, a gauge, the workers, a throughput sparkline, the points table (sorted
    by a column when its header is selected) and the latest warnings and errors.

    The points table is changed in place when only the rows changed (``state.rows_version``):
    a changed cell is updated, so the scroll position and the cursor stay. It is rebuilt
    (keeping the cursor and the scroll) only when the order or the columns change.
    """

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
        self.table_rebuilds = 0  # how often the points table was rebuilt (tests watch it)
        self._seen = None  # (rows_version, sort, reverse, columns) last shown
        self._order: list | None = None  # point indexes in the table's row order
        self._cells: dict = {}  # point index -> the cells shown in its row
        self._workers_drawn = None
        self._gauge = None

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

    def forget(self) -> None:
        super().forget()
        self._seen = None
        self._order = None  # the table is rebuilt at the next update
        self._workers_drawn = None
        self._gauge = None

    def on_screen_resume(self) -> None:
        self._seen = None  # the view was hidden: check the table again
        self.update_from(self.app)

    def update_from(self, app: ScanApp) -> None:
        state = app.state
        counts = state.counts()
        done = counts["done"] + counts["cached"]
        eta = state.eta_seconds()
        eta_text = "?" if eta is None else format_hms(eta)
        flags = " ".join(
            flag for flag in ("paused" if state.paused else "", app.status_note) if flag
        )
        self._show(
            "overall",
            f"points: done {done} (cached {counts['cached']})  running {counts['running']}  "
            f"queued {counts['queued']}  failed {counts['failed']}  "
            f"stopped {counts['stopped']}  of {state.n_points}\n"
            f"ions {state.total_ions()}  elapsed {format_hms(state.elapsed(app.clock()))}  "
            f"eta {eta_text}  workers {state.n_workers}  {flags}",
        )
        gauge = (max(state.n_points, 1), done + counts["failed"] + counts["stopped"])
        if gauge != self._gauge:
            self._gauge = gauge
            self.query_one("#gauge", ProgressBar).update(total=gauge[0], progress=gauge[1])
        self._update_workers(state)
        last = f"{state.throughput[-1]:.4g}" if state.throughput else "—"
        self._show("throughput", f"ions/s {sparkline(state.throughput) or '—'}  last {last}")
        self._update_points(state)
        lines = [f"{level}: {text}" for level, text in state.last_warnings(WARNINGS_SHOWN)]
        self._show("warnings", "\n".join(lines) or "no warnings")

    def _update_workers(self, state: ScanState) -> None:
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
        table = self.query_one("#workers", DataTable)
        table.clear()
        for row in rows:
            table.add_row(*(str(cell) for cell in row), key=str(row[0]))

    def _update_points(self, state: ScanState) -> None:
        ids = tuple(column for column, _ in state.table_columns())
        seen = (state.rows_version, self.sort_column, self.sort_reverse, ids)
        if seen == self._seen:
            return
        rows = state.sorted_rows(self.sort_column, self.sort_reverse)
        order = [row.index for row in rows]
        if ids != self.column_ids or order != self._order:
            self._rebuild(state, rows, ids)
        else:
            self._update_cells(state, rows, ids)
        self._seen = seen

    def _rebuild(self, state: ScanState, rows, ids: tuple) -> None:
        table = self.table
        selected, scroll = self.selected_index(), table.scroll_y
        if ids != self.column_ids:
            table.clear(columns=True)
            for column, label in state.table_columns():
                table.add_column(label, key=column)
            self.column_ids = ids
        else:
            table.clear()
        self._cells = {}
        for row in rows:
            cells = tuple(state.cell(row, column) for column in ids)
            self._cells[row.index] = cells
            table.add_row(*cells, key=str(row.index))
        self._order = [row.index for row in rows]
        self._restore_cursor(selected)
        table.scroll_to(y=scroll, animate=False)
        self.table_rebuilds += 1

    def _update_cells(self, state: ScanState, rows, ids: tuple) -> None:
        table = self.table
        for row in rows:
            cells = tuple(state.cell(row, column) for column in ids)
            previous = self._cells.get(row.index)
            if cells == previous:
                continue
            for column, value, before in zip(ids, cells, previous, strict=True):
                if value != before:
                    table.update_cell(str(row.index), column, value)
            self._cells[row.index] = cells

    def _restore_cursor(self, index) -> None:
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
        self._seen = None
        self._update_points(self.app.state)

    def on_data_table_header_selected(self, event: DataTable.HeaderSelected) -> None:
        if event.data_table.id != "points":
            return
        column = event.column_key.value
        reverse = column == self.sort_column and not self.sort_reverse
        self.sort_by(column, reverse=reverse)


class LogScreen(ModalScreen):
    """The text of an ``error.log``; ``escape``, ``q`` or ``l`` closes it."""

    DEFAULT_CSS = """
    LogScreen { align: center middle; }
    LogScreen VerticalScroll { width: 90%; height: 80%; border: round $accent; padding: 0 1; }
    """
    BINDINGS = [
        Binding("escape", "close", "close"),
        Binding("q", "close", "close", show=False),
        Binding("l", "close", "close", show=False),
    ]

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
