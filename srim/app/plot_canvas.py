"""A plot of one run's results on a canvas: its toolbar, log scales, and the data as CSV.

:class:`PlotCanvas` draws a :class:`~srim.app.plots.PlotDef` for a result. The data is read in a
thread (:func:`srim.app.workers.run_in_thread`) and the figure is drawn in the GUI thread, since
matplotlib figures are not thread-safe with a Qt canvas. Only the latest request is drawn: a late
result for a plot that is no longer asked for is dropped. The colours come from the window
palette, as in :mod:`srim.app.beam_plot`, and the palette is taken again when it changes.

:class:`PlotWindow` is a plot in a window of its own (the Results tab's pop-out).

matplotlib is imported here only; this is a Qt module and the library never imports it.
"""

from collections.abc import Callable
from pathlib import Path
from typing import Any

from matplotlib.backends.backend_qtagg import FigureCanvasQTAgg, NavigationToolbar2QT
from matplotlib.figure import Figure
from PySide6.QtCore import QEvent, Signal
from PySide6.QtGui import QPalette
from PySide6.QtWidgets import (
    QApplication,
    QCheckBox,
    QFileDialog,
    QHBoxLayout,
    QLabel,
    QMessageBox,
    QPushButton,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)

from srim.app.plots import PlotDef, plot_data
from srim.app.workers import run_in_thread

#: the text while the data is read and drawn
DRAWING_TEXT = "Drawing…"
#: the file types of "Save data…"
SAVE_FILTER = "CSV (*.csv)"


def palette_colours(widget: QWidget) -> tuple[str, str, str]:
    """The foreground, background and accent colours of ``widget``'s palette, as hex names."""
    palette = widget.palette()
    foreground = palette.color(QPalette.ColorRole.WindowText).name()
    background = palette.color(QPalette.ColorRole.Window).name()
    accent = palette.color(QPalette.ColorRole.Highlight).name()
    return foreground, background, accent


class PlotCanvas(QWidget):
    """One plot with its toolbar, its log-scale boxes and "Copy data" and "Save data…".

    :meth:`show_plot` takes the plot and the results; ``drawn`` (with no argument) follows each
    drawing on the GUI thread, and ``failed`` (the message) each plot that cannot be drawn.
    """

    drawn = Signal()
    failed = Signal(str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.figure = Figure(figsize=(6.0, 4.0), layout="constrained")
        self.canvas = FigureCanvasQTAgg(self.figure)
        self.canvas.setMinimumHeight(260)
        self.toolbar = NavigationToolbar2QT(self.canvas, self)
        self.message = QLabel(self)
        self.message.setWordWrap(True)
        # An empty or one-line message takes its height only: the spare height goes to the canvas.
        self.message.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Maximum)

        self.log_x = QCheckBox("Log x", self)
        self.log_y = QCheckBox("Log y", self)
        self.log_x.toggled.connect(self._apply_scales)
        self.log_y.toggled.connect(self._apply_scales)
        self.copy_button = QPushButton("Copy data", self)
        self.copy_button.setToolTip("Copy the data of the plot as CSV")
        self.copy_button.clicked.connect(self.copy_data)
        self.save_button = QPushButton("Save data…", self)
        self.save_button.clicked.connect(self.save_data)

        controls = QHBoxLayout()
        controls.addWidget(self.log_x)
        controls.addWidget(self.log_y)
        controls.addStretch(1)
        controls.addWidget(self.copy_button)
        controls.addWidget(self.save_button)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(self.message)
        layout.addWidget(self.toolbar)
        layout.addWidget(self.canvas, 1)
        layout.addLayout(controls)

        self._generation = 0
        self._busy = False
        self._disposed = False
        #: the key of the view on the canvas (a plot's key, or a scan view's key)
        self._key: str | None = None
        self._draw_fn: Callable[[Any, Any], None] | None = None
        self._main_axes = None
        self._frame = None
        self._set_data_actions(False)

    # --- drawing ---------------------------------------------------------------------

    def show_plot(self, plot: PlotDef, results) -> None:
        """Draw ``plot`` for ``results``: the data is read in a thread, then drawn here.

        A plot other than the one drawn now says "Drawing…" and disables Copy and Save until its
        data arrives. The same plot (a new point, say) keeps the figure and the buttons until the
        new data arrives. A request made before the last one has finished is dropped when it ends.
        """
        self.show_job(plot.key, lambda: plot_data(plot, results), plot.draw)

    def show_job(
        self,
        key: str,
        load: Callable[[], Any],
        draw: Callable[[Any, Any], None],
    ) -> None:
        """Draw the frame that ``load()`` returns (read in a thread) with ``draw(frame, ax)``.

        ``key`` names the view (a plot's key, or a scan view's): the same key keeps the figure and
        the buttons until the new frame arrives. ``load`` raising ``ValueError`` shows its message
        instead of a figure. The same rules as :meth:`show_plot` hold.
        """
        keep = self._frame is not None and self._key == key
        self._generation += 1
        generation = self._generation
        self._key = key
        self._draw_fn = draw
        self._busy = True
        if not keep:
            self._frame = None
            self._set_data_actions(False)
            self.message.setText(DRAWING_TEXT)
        run_in_thread(
            load,
            lambda frame: self._drawn(generation, frame),
            lambda error: self._failed(generation, error),
        )

    def refresh(self, plot: PlotDef, results) -> None:
        """Draw ``plot`` again, as a running run's data grows. Nothing starts while a request is
        outstanding, so slow reads do not pile up in threads."""
        if self._busy:
            return
        self.show_plot(plot, results)

    def refresh_job(
        self,
        key: str,
        load: Callable[[], Any],
        draw: Callable[[Any, Any], None],
    ) -> None:
        """:meth:`show_job` again, unless a request is outstanding (see :meth:`refresh`)."""
        if self._busy:
            return
        self.show_job(key, load, draw)

    def dispose(self) -> None:
        """The canvas's window has closed: a late result is ignored from now on."""
        self._disposed = True

    def _drawn(self, generation: int, frame) -> None:
        if self._disposed or generation != self._generation:
            return
        self._busy = False
        self._frame = frame
        self._draw(frame)
        self.message.setText("")
        self._set_data_actions(True)
        self.drawn.emit()

    def _failed(self, generation: int, error: BaseException) -> None:
        if self._disposed or generation != self._generation:
            return
        self._busy = False
        text = str(error) or type(error).__name__
        self.figure.clear()
        self.canvas.draw_idle()
        self.message.setText(text)
        self._set_data_actions(False)
        self.failed.emit(text)

    def _draw(self, frame) -> None:
        foreground, background, accent = palette_colours(self)
        self.figure.clear()
        self.figure.set_facecolor(background)
        ax = self.figure.add_subplot()
        self._main_axes = ax
        ax.set_prop_cycle(color=[accent, foreground])
        self._draw_fn(frame, ax)
        ax.set_facecolor(background)
        for spine in ax.spines.values():
            spine.set_color(foreground)
        ax.tick_params(colors=foreground)
        ax.title.set_color(foreground)
        ax.xaxis.label.set_color(foreground)
        ax.yaxis.label.set_color(foreground)
        legend = ax.get_legend()
        if legend is not None:
            legend.set_frame_on(False)
            for text in legend.get_texts():
                text.set_color(foreground)
        self._apply_scales()
        self.canvas.draw_idle()

    def _apply_scales(self, *_checked) -> None:
        """The log or linear scales of the boxes, on the drawn axes (not a colour bar's)."""
        if self._main_axes is not None:
            ax = self._main_axes
            ax.set_xscale("log" if self.log_x.isChecked() else "linear")
            ax.set_yscale("log" if self.log_y.isChecked() else "linear")
        self.canvas.draw_idle()

    def _set_data_actions(self, enabled: bool) -> None:
        self.copy_button.setEnabled(enabled)
        self.save_button.setEnabled(enabled)

    def changeEvent(self, event):  # noqa: N802 (Qt's name)
        super().changeEvent(event)
        if event.type() == QEvent.Type.PaletteChange and self._frame is not None:
            self._draw(self._frame)

    # --- data ------------------------------------------------------------------------

    def copy_data(self) -> None:
        """Copy the data of the plot on the screen to the clipboard, as CSV."""
        if self._frame is None:
            return
        QApplication.clipboard().setText(self._frame.to_csv(index=False))

    def save_data(self) -> None:
        """Save the data of the plot on the screen as a CSV file, chosen in a dialog."""
        if self._frame is None:
            return
        default = f"{self._key}.csv"
        path, _ = QFileDialog.getSaveFileName(self, "Save plot data", default, SAVE_FILTER)
        if not path:
            return
        target = Path(path)
        if not target.suffix:
            target = target.with_suffix(".csv")
        try:
            self._frame.to_csv(target, index=False)
        except OSError as error:
            QMessageBox.warning(self, "Save data", str(error))


class PlotWindow(QWidget):
    """A plot in a window of its own. ``closed`` (with this window) follows its close."""

    closed = Signal(object)

    def __init__(self, plot: PlotDef, parent=None):
        super().__init__(parent)
        self.plot = plot
        self.setWindowTitle(plot.title)
        self.resize(760, 560)
        self.canvas = PlotCanvas(self)
        layout = QVBoxLayout(self)
        layout.addWidget(self.canvas)

    def closeEvent(self, event):  # noqa: N802 (Qt's name)
        self.canvas.dispose()
        super().closeEvent(event)
        self.closed.emit(self)
