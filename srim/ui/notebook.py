"""``LiveView``: a scan's plot and status line, updated in a Jupyter cell while the scan runs.

``run_scan(scan, on_event=LiveView())`` shows the status line and the plot in the cell and
updates them as the events come in, at most every ``every`` seconds, and once more when the scan
has finished. The plot is the one of ``ScanResult.plot`` (the same axes, one series per value of
the hue parameter, error bars from ``<value>_err``); ``plot="X,Y[,HUE]"`` picks the axes as
``pysrim2 scan run --plot`` does. The status line is the one of the terminal views.

The view runs in the scan's thread, so it draws quickly and never raises: if it fails, the view
says why (a warning, and the status line when it is shown) and stops, and the scan goes on.
It needs IPython (for the display) and matplotlib, both imported when the view is made. No
widgets are used.

The scan's points come from the scan folder's ``scan.yaml``, which is written before the scan
starts (``scan_started`` carries the folder).
"""

from __future__ import annotations

import time
import warnings
from pathlib import Path

#: the message of the plot while no point has a value yet (as in the terminal views)
_NEEDS_IPYTHON = (
    'LiveView needs IPython and matplotlib: pip install "pysrim2[plot]" ipython '
    "(in Jupyter, IPython is there already)"
)


class LiveView:
    """An event consumer for ``run_scan(on_event=...)`` that shows the scan in a notebook cell.

    ``plot`` is ``None`` (the defaults: the first scanned parameter on x, the second as the
    hue, the first extracted value on y) or ``"X,Y[,HUE]"``. ``every`` is the least number of
    seconds between two updates; ``clock`` gives the time (seconds since the epoch, as the
    event times do) and exists for the tests.
    """

    def __init__(self, plot: str | None = None, every: float = 1.0, *, clock=time.time):
        if every < 0:
            raise ValueError(f"every must not be negative, not {every!r}")
        try:
            from IPython.display import Pretty, display
            from matplotlib.figure import Figure
        except ImportError as exc:
            raise ImportError(_NEEDS_IPYTHON) from exc
        self.plot = plot
        self.every = every
        self.clock = clock
        self._display = display
        self._pretty = Pretty
        self._figure_class = Figure
        self.state = None
        self._status_handle = None
        self._plot_handle = None
        self._last_update = None
        self._drawn = None  # (data_version, axes) of the figure shown
        self._failed = False

    def __call__(self, event) -> None:
        if self._failed:
            return
        try:
            if self.state is None:
                if event.kind != "scan_started":
                    return
                self._begin(event)
            self.state.apply(event)
            now = self.clock()
            due = self._last_update is None or now - self._last_update >= self.every
            if due or event.kind == "scan_finished":
                self._update(now)
        except Exception as exc:  # the scan goes on; the view says why it stopped
            self._fail(exc)

    # -- the view -------------------------------------------------------------------------

    def _begin(self, event) -> None:
        from srim.scan import Scan
        from srim.ui.tui.plot import check_plot
        from srim.ui.tui.state import ScanState

        scan = Scan.from_yaml(Path(event.scan_dir) / "scan.yaml")
        check_plot(scan.param_names, self.plot)
        self.state = ScanState(scan)
        self._status_handle = self._display(self._pretty(""), display_id=True)
        self._plot_handle = self._display(self._figure_class(figsize=(7, 4.5)), display_id=True)

    def _update(self, now: float) -> None:
        from srim.ui.tui.plot import build, choose_axes
        from srim.ui.tui.state import status_line

        self._last_update = now
        state = self.state
        if self._status_handle is not None:
            self._status_handle.update(self._pretty(status_line(state, now)))
        axes = choose_axes(state, self.plot)
        if self._plot_handle is not None and (state.data_version, axes) != self._drawn:
            self._drawn = (state.data_version, axes)
            self._plot_handle.update(self._draw(build(state, *axes), hue=axes[2]))

    def _draw(self, data, hue):
        """A new figure of ``data`` (a :class:`~srim.ui.tui.plot.PlotData`); ``hue`` names
        the legend."""
        figure = self._figure_class(figsize=(7, 4.5))
        ax = figure.add_subplot()
        for series in data.series:
            errors = [0.0 if err is None else err for err in series.err]
            ax.errorbar(
                list(series.x),
                list(series.y),
                yerr=errors if any(errors) else None,
                marker="o",
                capsize=3,
                linestyle="none",
                label=series.label or None,
            )
        if data.categories is not None:
            ax.set_xticks(list(range(len(data.categories))))
            ax.set_xticklabels(list(data.categories))
        if data.series:
            ax.set_title(f"{data.y_label} by {data.x_label}")
            if any(series.label for series in data.series):
                ax.legend(title=hue)
        else:
            ax.set_title(data.message)
        ax.set_xlabel(data.x_label)
        ax.set_ylabel(data.y_label)
        return figure

    def _fail(self, exc: Exception) -> None:
        self._failed = True
        message = f"LiveView stopped showing the scan: {type(exc).__name__}: {exc}"
        warnings.warn(message, stacklevel=2)
        if self._status_handle is not None:
            self._status_handle.update(self._pretty(message))


__all__ = ["LiveView"]
