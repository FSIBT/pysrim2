"""``LiveView`` (srim.ui.notebook): the throttled status and plot in a notebook cell. IPython is
replaced by a fake display module and the clock is a list the test moves, so nothing waits."""

import sys
import types
import warnings

import pytest
from matplotlib.figure import Figure

from srim import ui
from srim.scan import Scan
from srim.scan.events import (
    PointFinished,
    PointQueued,
    ScanFinished,
    ScanStarted,
)
from srim.spec import RunSpec
from srim.ui import LiveView, notebook

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


THICKNESS_KEY = "target[0].thickness_nm"
THICKNESS = "target.0.thickness_nm"  # how the scan names the key in its parameters


class Pretty:
    def __init__(self, text):
        self.text = text


class Handle:
    """A display handle: ``shown`` holds every object displayed or updated into it."""

    def __init__(self, obj):
        self.shown = [obj]

    def update(self, obj):
        self.shown.append(obj)


class FakeDisplay:
    """``IPython.display``: ``display_id`` gives a handle, as in a notebook."""

    def __init__(self, in_notebook=True):
        self.in_notebook = in_notebook
        self.handles = []

    def display(self, obj, display_id=None):
        if not self.in_notebook:
            return None  # outside IPython, display prints and returns nothing
        handle = Handle(obj)
        if display_id:
            self.handles.append(handle)
        return handle if display_id else None


@pytest.fixture
def fake_ipython(monkeypatch):
    display = FakeDisplay()
    module = types.ModuleType("IPython.display")
    module.display = display.display
    module.Pretty = Pretty
    monkeypatch.setitem(sys.modules, "IPython.display", module)
    return display


class Clock:
    def __init__(self, now=1000.0):
        self.now = now

    def __call__(self):
        return self.now


def make_scan_folder(folder):
    scan = Scan(
        BASE,
        grid={"ion.energy_keV": [10.0, 20.0], THICKNESS_KEY: [100.0, 200.0]},
        seed=7,
    )
    scan.to_yaml(folder / "scan.yaml")
    return scan


def started(folder):
    return ScanStarted(n_points=4, workers=1, base_seed=7, scan_dir=str(folder), time=0.0)


def feed(view, events):
    for event in events:
        view(event)


def status_texts(view):
    return [item.text for item in view._status_handle.shown]


def test_the_status_and_the_plot_are_shown_when_the_scan_starts(tmp_path, fake_ipython):
    scan = make_scan_folder(tmp_path)
    view = LiveView(clock=Clock())
    feed(view, [started(tmp_path)] + [PointQueued(p.index, p.id, time=0.0) for p in scan.points()])
    assert len(fake_ipython.handles) == 2  # the status line and the figure
    assert status_texts(view)[-1].startswith("done 0/4")
    assert isinstance(view._plot_handle.shown[-1], Figure)


def test_updates_come_at_most_every_interval_and_the_last_one_is_the_finished_scan(
    tmp_path, fake_ipython
):
    scan = make_scan_folder(tmp_path)
    clock = Clock(1000.0)
    view = LiveView(every=1.0, clock=clock)
    feed(view, [started(tmp_path)])
    first = len(view._status_handle.shown)
    for _ in range(50):  # a burst of events in the same second
        view(PointQueued(0, scan.points()[0].id, time=0.0))
    assert len(view._status_handle.shown) == first  # nothing new within the interval
    clock.now += 0.5
    view(PointQueued(0, scan.points()[0].id, time=0.0))
    assert len(view._status_handle.shown) == first
    clock.now += 0.5
    view(PointQueued(0, scan.points()[0].id, time=0.0))
    assert len(view._status_handle.shown) == first + 1  # one update a second
    view(ScanFinished(n_done=4, n_failed=0, seconds=3.0, stopped=False))
    assert len(view._status_handle.shown) == first + 2  # the final update, at once
    assert status_texts(view)[-1].startswith("finished 0/4")  # the points the events gave


def test_the_plot_has_one_series_per_hue_value_with_error_bars(tmp_path, fake_ipython):
    scan = make_scan_folder(tmp_path)
    view = LiveView(clock=Clock())
    feed(view, [started(tmp_path)])
    for point in scan.points():
        values = {"mean": float(point.params["ion.energy_keV"]), "mean_err": 0.5}
        view(PointFinished(point.index, point.id, values, 100, 1.0, time=0.0))
    view(ScanFinished(n_done=4, n_failed=0, seconds=1.0, stopped=False))
    figure = view._plot_handle.shown[-1]
    ax = figure.axes[0]
    assert ax.get_xlabel() == "ion.energy_keV"
    assert ax.get_ylabel() == "mean"
    assert ax.get_title() == "mean by ion.energy_keV"
    assert ax.get_legend().get_title().get_text() == THICKNESS
    assert len(ax.containers) == 2  # one error-bar container per thickness


def test_the_plot_option_picks_the_axes_like_the_terminal_plot(tmp_path, fake_ipython):
    scan = make_scan_folder(tmp_path)
    view = LiveView(plot=f"{THICKNESS},mean,", clock=Clock())
    feed(view, [started(tmp_path)])
    point = scan.points()[0]
    view(PointFinished(point.index, point.id, {"mean": 2.0}, 100, 1.0, time=0.0))
    view(ScanFinished(n_done=1, n_failed=0, seconds=1.0, stopped=False))
    ax = view._plot_handle.shown[-1].axes[0]
    assert ax.get_xlabel() == THICKNESS
    assert ax.get_legend() is None  # 'X,Y,' gives no series' labels


def test_a_plot_that_cannot_be_drawn_warns_and_the_scan_goes_on(tmp_path, fake_ipython):
    make_scan_folder(tmp_path)
    view = LiveView(plot="nope,mean", clock=Clock())
    with pytest.warns(UserWarning, match="LiveView stopped showing the scan"):
        view(started(tmp_path))  # the plot is checked when the scan starts
    view(ScanFinished(n_done=0, n_failed=0, seconds=0.0, stopped=False))  # no more work, no error


def test_a_scan_folder_without_its_scan_file_warns_and_the_scan_goes_on(tmp_path, fake_ipython):
    view = LiveView(clock=Clock())
    with pytest.warns(UserWarning):
        view(started(tmp_path))  # no scan.yaml here


def test_outside_a_notebook_the_view_draws_nothing_and_does_not_fail(tmp_path, monkeypatch):
    make_scan_folder(tmp_path)
    display = FakeDisplay(in_notebook=False)
    module = types.ModuleType("IPython.display")
    module.display = display.display
    module.Pretty = Pretty
    monkeypatch.setitem(sys.modules, "IPython.display", module)
    view = LiveView(clock=Clock())
    with warnings.catch_warnings():
        warnings.simplefilter("error")  # nothing may go wrong
        view(started(tmp_path))
        view(ScanFinished(n_done=0, n_failed=0, seconds=0.0, stopped=False))


def test_without_ipython_the_error_names_the_install_command(monkeypatch):
    monkeypatch.setitem(sys.modules, "IPython.display", None)  # an import of it fails
    with pytest.raises(ImportError, match='pip install "pysrim2\\[plot\\]"'):
        LiveView()


def test_every_must_not_be_negative():
    with pytest.raises(ValueError, match="every must not be negative"):
        LiveView(every=-1)


def test_live_view_is_exported_from_srim_ui():
    assert ui.LiveView is notebook.LiveView
    assert "LiveView" in ui.__all__
