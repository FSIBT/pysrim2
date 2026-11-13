"""The density of one beam quantity, drawn under its editor on the Beam tab.

:class:`DistributionPlot` shows what :func:`srim.beam.spec.quantity_from_value` makes of a value:
a smooth density over its support (400 points), or a step line for a histogram or a uniform
distribution. A dashed vertical line marks each bin edge when the setup is binned. A number, a
value the library refuses and a zero-width shape are not drawn: the widget is hidden instead.

The figure takes its colours from the window palette, so dark mode reads well. matplotlib is
imported here only; this module is a Qt module and the library never imports it.
"""

from copy import deepcopy
from pathlib import Path
from typing import Any

import numpy as np
from matplotlib.backends.backend_qtagg import FigureCanvasQTAgg
from matplotlib.figure import Figure
from PySide6.QtCore import QEvent
from PySide6.QtGui import QPalette
from PySide6.QtWidgets import QVBoxLayout, QWidget

from srim.beam.shapes import Histogram, Shape, Uniform
from srim.beam.spec import quantity_from_value
from srim.spec import SpecError

#: points of a smooth density over its support
POINTS = 400
#: bins of the histogram of sampled values
SAMPLE_BINS = 40

_AXIS_LABELS = {
    "energy_keV": "Energy (keV)",
    "angle_deg": "Angle (degrees)",
    "azimuth_deg": "Azimuth (degrees)",
    "y_nm": "Position y (nm)",
    "z_nm": "Position z (nm)",
    "depth_nm": "Depth (nm)",
}


class DistributionPlot(QWidget):
    """The density of one beam quantity, with the bin edges of a binned setup.

    :meth:`set_distribution` takes the value as the document holds it. Calling it again with the
    same arguments draws nothing; the figure is redrawn when the palette changes.
    """

    def __init__(self, parent=None):
        super().__init__(parent)
        self.figure = Figure(figsize=(4.5, 1.9), layout="constrained")
        self.axes = self.figure.add_subplot()
        self.canvas = FigureCanvasQTAgg(self.figure)
        self.canvas.setMinimumHeight(150)
        self.canvas.setMaximumHeight(210)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(self.canvas)
        self._last: tuple | None = None
        self._samples: np.ndarray | None = None
        self.setVisible(False)

    # --- showing -----------------------------------------------------------------------

    def set_distribution(
        self, value: Any, key: str, base_dir: Any, bin_edges: list[float] | None
    ) -> None:
        """Draw the density of ``value`` for the quantity ``key``, or hide the plot.

        ``bin_edges`` are drawn as dashed lines; None (or an empty list) draws none.
        """
        edges = list(bin_edges) if bin_edges else None
        args = (deepcopy(value), key, None if base_dir is None else Path(base_dir), edges)
        if args == self._last:
            return
        self._last = args
        self._draw()

    def set_samples(self, values) -> None:
        """Draw the sampled ``values`` as a normalised histogram over the density (None removes
        them). The histogram of a finished exact run is set here, and cleared when the setup
        changes."""
        self._samples = None if values is None else np.asarray(values, dtype=float).copy()
        if self._last is not None:
            self._draw()

    def changeEvent(self, event):  # noqa: N802 (Qt's name)
        super().changeEvent(event)
        if event.type() == QEvent.Type.PaletteChange and getattr(self, "_last", None):
            self._draw()

    # --- drawing -----------------------------------------------------------------------

    def _draw(self) -> None:
        value, key, base_dir, edges = self._last
        shape = _shape_of(value, key, base_dir)
        if shape is None:
            self.setVisible(False)
            return
        self.axes.clear()
        foreground, background, accent = self._colours()
        self.figure.set_facecolor(background)
        self.axes.set_facecolor(background)
        for spine in self.axes.spines.values():
            spine.set_color(foreground)
        self.axes.tick_params(colors=foreground, labelsize=8)
        self.axes.set_xlabel(_AXIS_LABELS.get(key, key), color=foreground, fontsize=9)
        self.axes.set_ylabel("Density", color=foreground, fontsize=9)

        if isinstance(shape, (Histogram, Uniform)):
            xs, ys = _steps(shape)
        else:
            low, high = shape.support()
            xs = np.linspace(low, high, POINTS)
            ys = np.asarray(shape.pdf(xs), dtype=float)
        if self._samples is not None and self._samples.size:
            self.axes.hist(self._samples, bins=SAMPLE_BINS, density=True, color=accent, alpha=0.35)
        self.axes.plot(xs, ys, color=accent, linewidth=1.5)
        for edge in edges or ():
            self.axes.axvline(edge, color=foreground, linestyle="--", linewidth=0.8)
        self.canvas.draw_idle()
        self.setVisible(True)

    def _colours(self) -> tuple[str, str, str]:
        palette = self.palette()
        foreground = palette.color(QPalette.ColorRole.WindowText).name()
        background = palette.color(QPalette.ColorRole.Window).name()
        accent = palette.color(QPalette.ColorRole.Highlight).name()
        return foreground, background, accent


def _shape_of(value: Any, key: str, base_dir: Any) -> Shape | None:
    """The shape of a distribution value that has a density to draw, else None.

    A number, a missing value, a value the library refuses and a zero-width shape give None. A
    user function may raise anything when it is evaluated; the preview must not crash the app,
    so any error there also gives None.
    """
    if value is None:
        return None
    try:
        shape = quantity_from_value(value, key, base_dir)
    except SpecError:
        return None
    if not isinstance(shape, Shape):
        return None
    try:
        low, high = shape.support()
        if not low < high:
            return None
        shape.pdf(np.array([low, high]))
    except Exception:  # a user density function can raise anything; see the docstring
        return None
    return shape


def _steps(shape: Shape) -> tuple[np.ndarray, np.ndarray]:
    """The step line of a histogram or uniform: each bin's density over its width.

    The edges are the histogram's (or the uniform's two ends), cut to the support.
    """
    low, high = shape.support()
    if isinstance(shape, Histogram):
        raw = np.asarray(shape.edges, dtype=float)
    else:
        raw = np.array([low, high], dtype=float)
    edges = np.clip(raw, low, high)
    edges = np.unique(edges)
    middles = (edges[:-1] + edges[1:]) / 2
    densities = np.asarray(shape.pdf(middles), dtype=float)
    xs = np.concatenate([[edges[0]], np.repeat(edges[1:-1], 2), [edges[-1]]])
    ys = np.repeat(densities, 2)
    return xs, ys
