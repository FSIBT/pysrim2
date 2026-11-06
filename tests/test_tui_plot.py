"""The plot view's data (series, axes, error bars) and its drawing, with a fake plotting
object (no terminal, no textual)."""

import pytest

from srim.scan import Scan
from srim.scan.events import PointFinished, PointQueued, PointStarted, ScanStarted
from srim.spec import RunSpec
from srim.ui.tui.plot import build, check_plot, choose_axes, draw
from srim.ui.tui.state import ScanState

BASE = RunSpec.from_dict(
    {
        "ion": {"symbol": "B", "energy_keV": 20.0},
        "number_ions": 100,
        "seed": 1,
        "target": [
            {
                "name": "Si",
                "thickness_nm": 100.0,
                "density_g_cm3": 2.33,
                "elements": [{"symbol": "Si", "stoich": 1.0}],
            }
        ],
    }
)


class FakePlt:
    """Records the calls the drawing makes."""

    def __init__(self):
        self.calls = []

    def __getattr__(self, name):
        def record(*args, **kwargs):
            self.calls.append((name, args, kwargs))

        return record

    def named(self, name):
        return [(args, kwargs) for call, args, kwargs in self.calls if call == name]


def state_for(scan, finished=()):
    """A state of ``scan`` with the points in ``finished`` (index, values) done."""
    state = ScanState(scan)
    state.apply(ScanStarted(len(scan.points()), 2, 7, "/scan", time=0.0))
    points = scan.points()
    for point in points:
        state.apply(PointQueued(point.index, point.id))
    for index, values in finished:
        state.apply(PointStarted(index, points[index].id, 0, 1))
        state.apply(PointFinished(index, points[index].id, values, 100, 1.0))
    return state


def two_by_two():
    """Energy (x) by target thickness (hue): four points."""
    return Scan(
        BASE,
        grid={"ion.energy_keV": [10.0, 20.0], "target.0.thickness_nm": [100.0, 200.0]},
        extract=["implant_summary"],
        seed=7,
    )


def test_the_default_axes_are_the_first_parameter_the_second_and_the_first_value():
    scan = two_by_two()
    state = state_for(scan, [(0, {"mean": 1.0})])
    assert choose_axes(state) == ("ion.energy_keV", "mean", "target.0.thickness_nm")


def test_without_a_value_yet_the_y_axis_is_unknown_and_the_plot_waits():
    state = state_for(two_by_two())
    x, y, hue = choose_axes(state)
    assert y is None
    data = build(state, x, y, hue)
    assert data.series == () and "waiting" in data.message


def test_one_series_per_hue_value_with_points_sorted_by_x():
    scan = two_by_two()
    state = state_for(
        scan,
        [(3, {"mean": 4.0}), (0, {"mean": 1.0}), (1, {"mean": 2.0}), (2, {"mean": 3.0})],
    )
    data = build(state, "ion.energy_keV", "mean", "target.0.thickness_nm")
    labels = [s.label for s in data.series]
    assert labels == ["100.0", "200.0"]
    first, second = data.series
    assert first.x == (10.0, 20.0) and first.y == (1.0, 3.0)
    assert second.x == (10.0, 20.0) and second.y == (2.0, 4.0)
    assert data.categories is None and data.message == ""


def test_error_bars_come_from_the_error_column_and_are_none_without_one():
    scan = two_by_two()
    state = state_for(scan, [(0, {"mean": 1.0, "mean_err": 0.25}), (1, {"mean": 2.0})])
    data = build(state, "ion.energy_keV", "mean", None)
    assert len(data.series) == 1
    assert data.series[0].err == (0.25, None)


def test_text_values_of_x_become_categories_at_their_positions():
    scan = Scan(
        BASE,
        grid={"ion.symbol": ["B", "P"]},
        extract=["implant_summary"],
        seed=7,
    )
    state = state_for(scan, [(0, {"mean": 1.0}), (1, {"mean": 2.0})])
    data = build(state, "ion.symbol", "mean", None)
    assert data.categories == ("B", "P")
    assert data.series[0].x == (0, 1)


def test_an_unknown_y_column_says_what_the_columns_are():
    state = state_for(two_by_two(), [(0, {"mean": 1.0})])
    data = build(state, "ion.energy_keV", "nope", None)
    assert data.series == ()
    assert "'nope' is not an extracted column" in data.message and "mean" in data.message


def test_check_plot_rejects_unknown_scan_keys_and_bad_shapes():
    names = ["ion.energy_keV", "target.0.thickness_nm"]
    assert check_plot(names, None) == (None, None, None)
    assert check_plot(names, ("ion.energy_keV", "mean")) == ("ion.energy_keV", "mean", None)
    assert check_plot(names, "ion.energy_keV,mean") == ("ion.energy_keV", "mean", None)
    with pytest.raises(ValueError, match="not a scanned parameter"):
        check_plot(names, ("ion.energy", "mean"))
    with pytest.raises(ValueError, match="hue"):
        check_plot(names, ("ion.energy_keV", "mean", "ion.symbol"))
    with pytest.raises(ValueError, match="X,Y"):
        check_plot(names, ("ion.energy_keV",))


def test_drawing_puts_series_error_bars_and_labels_on_the_figure():
    scan = two_by_two()
    state = state_for(scan, [(0, {"mean": 1.0, "mean_err": 0.5}), (1, {"mean": 2.0})])
    data = build(state, "ion.energy_keV", "mean", "target.0.thickness_nm")
    fake = FakePlt()
    draw(fake, data)
    assert fake.named("clear_figure")
    assert len(fake.named("scatter")) == 2  # one per series
    segments = fake.named("plot")
    assert len(segments) == 1  # only the point with an error gets a vertical segment
    (xs, ys), kwargs = segments[0]
    assert list(xs) == [10.0, 10.0] and list(ys) == [0.5, 1.5]
    assert fake.named("xlabel") and fake.named("ylabel")


def test_drawing_an_empty_plot_shows_the_message_as_its_title():
    fake = FakePlt()
    draw(fake, build(state_for(two_by_two()), "ion.energy_keV", "mean", None))
    assert fake.named("title") and "waiting" in fake.named("title")[0][0][0]
