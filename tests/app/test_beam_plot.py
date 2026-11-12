"""The distribution plot: what it draws for each kind of value, and when it stays hidden."""

import pytest

pytest.importorskip("PySide6")
pytest.importorskip("pytestqt")

from matplotlib.colors import to_hex  # noqa: E402
from PySide6.QtGui import QColor, QPalette  # noqa: E402

from srim.app.beam_plot import DistributionPlot  # noqa: E402


@pytest.fixture
def plot(qtbot):
    widget = DistributionPlot()
    qtbot.addWidget(widget)
    return widget


def dashed(plot):
    return [line for line in plot.axes.lines if line.get_linestyle() == "--"]


def test_a_gaussian_is_one_line_and_visible(plot):
    plot.show({"gaussian": {"mean": 20.0, "sigma": 1.0}}, "energy_keV", None, None)
    assert len(plot.axes.lines) == 1
    assert not plot.isHidden()


def test_a_plain_number_is_not_drawn_and_hides_the_plot(plot):
    plot.show({"gaussian": {"mean": 20.0, "sigma": 1.0}}, "energy_keV", None, None)
    plot.show(20.0, "energy_keV", None, None)
    assert plot.isHidden()


@pytest.mark.parametrize(
    "value",
    [
        {"gaussian": {"mean": 20.0, "sigma": -1.0}},  # the library refuses a negative sigma
        {"gaussian": {"mean": 20.0, "sigma": None}},  # a missing value
        {"fixed": {"value": 20.0}},  # zero-width: no density to draw
        {"function": {"ref": "python:no_such_module_for_plot:fn"}},  # cannot be imported
        None,
    ],
)
def test_a_value_the_library_refuses_or_cannot_draw_hides_the_plot(plot, value):
    plot.show(value, "energy_keV", None, None)
    assert plot.isHidden()
    assert len(plot.axes.lines) == 0


def test_bin_edges_are_dashed_vertical_lines(plot):
    plot.show(
        {"gaussian": {"mean": 20.0, "sigma": 1.0}},
        "energy_keV",
        None,
        [17.0, 18.5, 20.0, 21.5, 23.0],
    )
    assert len(dashed(plot)) == 5
    assert len(plot.axes.lines) == 6  # the density and the five edges


def test_a_uniform_is_a_step_line_over_its_support(plot):
    plot.show({"uniform": {"low": 19.0, "high": 21.0}}, "energy_keV", None, None)
    (line,) = plot.axes.lines
    xs = list(line.get_xdata())
    ys = list(line.get_ydata())
    assert xs[0] == pytest.approx(19.0) and xs[-1] == pytest.approx(21.0)
    assert set(round(y, 9) for y in ys) == {0.5}


def test_an_inline_histogram_is_a_step_line_with_its_bins(plot):
    value = {"histogram": {"edges": [10.0, 20.0, 30.0], "counts": [1.0, 3.0]}}
    plot.show(value, "energy_keV", None, None)
    (line,) = plot.axes.lines
    assert list(line.get_xdata()) == pytest.approx([10.0, 20.0, 20.0, 30.0])
    assert list(line.get_ydata()) == pytest.approx([0.025, 0.025, 0.075, 0.075])


def test_the_same_arguments_draw_nothing_the_second_time(plot, monkeypatch):
    draws = []
    original = DistributionPlot._draw
    monkeypatch.setattr(plot, "_draw", lambda: (draws.append(1), original(plot)))
    value = {"gaussian": {"mean": 20.0, "sigma": 1.0}}
    plot.show(value, "energy_keV", None, None)
    plot.show(value, "energy_keV", None, None)
    assert draws == [1]
    plot.show({"gaussian": {"mean": 21.0, "sigma": 1.0}}, "energy_keV", None, None)
    assert draws == [1, 1]


def test_the_figure_takes_the_palette_colours_and_follows_a_change(plot):
    dark = QPalette()
    dark.setColor(QPalette.ColorRole.Window, QColor("#202124"))
    dark.setColor(QPalette.ColorRole.WindowText, QColor("#e8eaed"))
    plot.setPalette(dark)
    plot.show({"gaussian": {"mean": 20.0, "sigma": 1.0}}, "energy_keV", None, None)
    assert to_hex(plot.axes.get_facecolor()) == "#202124"
    assert to_hex(plot.axes.spines["left"].get_edgecolor()) == "#e8eaed"

    light = QPalette()
    light.setColor(QPalette.ColorRole.Window, QColor("#ffffff"))
    light.setColor(QPalette.ColorRole.WindowText, QColor("#000000"))
    plot.setPalette(light)
    assert to_hex(plot.axes.get_facecolor()) == "#ffffff"


def test_a_density_that_raises_hides_the_plot_instead_of_crashing(plot, monkeypatch):
    from srim.beam.shapes import Gaussian

    def boom(self, x):
        raise RuntimeError("the user's function failed")

    monkeypatch.setattr(Gaussian, "pdf", boom)
    plot.show({"gaussian": {"mean": 20.0, "sigma": 1.0}}, "energy_keV", None, None)
    assert plot.isHidden()
