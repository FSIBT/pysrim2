"""The scan views' data and drawing (Qt-free): the table of a scan state, the point styles, the
summary, the overlays and the point filter.

No Qt here: the figures are drawn with matplotlib directly. The table of a finished run is made
by :func:`srim.scan.run_scan` with the fake runner of ``runners.py`` (no SRIM).
"""

import math
from copy import deepcopy

import matplotlib
import numpy as np
import pandas as pd
import pytest
from matplotlib.colors import to_rgba
from matplotlib.container import ErrorbarContainer
from matplotlib.figure import Figure
from runners import fixture_runner

from srim.app.plots import CATALOGUE
from srim.app.scan_plots import (
    CURVE_LIMIT,
    Style,
    draw_overlay,
    draw_summary,
    point_styles,
    scan_table,
    select_points,
    split_frames,
    stack_frames,
    summary_data,
    value_columns,
)
from srim.scan import Scan, run_scan
from srim.scan.events import PointFinished, PointStarted
from srim.spec import RunSpec
from srim.ui.tui.state import ScanState

BY_KEY = {plot.key: plot for plot in CATALOGUE}


@pytest.fixture
def base(tmp_path, boron_data):
    return RunSpec.from_dict(deepcopy(boron_data), base_dir=tmp_path)


def two_parameter_scan(base):
    """Six points: three energies and two angles."""
    return Scan(base, grid={"ion.energy_keV": [10.0, 20.0, 30.0], "ion.angle_deg": [5.0, 7.0]})


def scripted_state(scan, values_by_index):
    """A state with every point finished, given the values of each point (a dict)."""
    state = ScanState(scan)
    for point in scan.points():
        state.apply(PointStarted(point.index, point.id, 0, 1))
        if point.index in values_by_index:
            values = values_by_index[point.index]
            state.apply(PointFinished(point.index, point.id, values, 200, 1.0))
    return state


def test_scan_table_has_the_parameters_values_and_statuses(base):
    scan = two_parameter_scan(base)
    state = scripted_state(
        scan,
        {
            0: {"depth_nm": 3.0, "depth_nm_err": 0.1},
            1: {"depth_nm": 4.0},
        },
    )
    table = scan_table(state)
    assert list(table.columns) == [
        "ion.energy_keV",
        "ion.angle_deg",
        "id",
        "depth_nm",
        "depth_nm_err",
        "n_ions",
        "seeds",
        "status",
        "error",
        "path",
    ]
    assert len(table) == 6
    assert table.loc[0, "depth_nm"] == 3.0
    assert table.loc[0, "depth_nm_err"] == 0.1
    assert math.isnan(table.loc[2, "depth_nm"])
    assert table.loc[0, "status"] == "done"
    assert table.loc[2, "status"] == "missing"  # no run yet
    assert table.loc[0, "path"] == f"points/{scan.points()[0].id[:16]}"
    assert math.isnan(table.loc[2, "path"])


def test_scan_table_has_the_columns_of_the_result_of_a_finished_run(base, tmp_path):
    scan = two_parameter_scan(base)
    state = ScanState(scan)
    result = run_scan(
        scan,
        workers=1,
        scan_dir=tmp_path / "scan",
        on_event=state.apply,
        runner=fixture_runner,
    )
    table = scan_table(state)
    assert list(table.columns) == list(result.table.columns)
    assert list(table["status"]) == list(result.table["status"])
    assert list(table["path"]) == list(result.table["path"])
    assert list(table["ion.energy_keV"]) == list(result.table["ion.energy_keV"])


def test_point_styles_are_distinct_for_fifteen_points():
    table = pd.DataFrame({"x": range(15)})
    styles = point_styles(table, None)
    triples = {(s.color, s.linestyle, s.marker) for s in styles.values()}
    assert len(styles) == 15
    assert len(triples) == 15


def test_a_point_keeps_its_style_in_every_plot():
    table = pd.DataFrame({"x": range(15), "hue": [float(i % 3) for i in range(15)]})
    first = point_styles(table, None)
    again = point_styles(table, None)
    subset = point_styles(table.iloc[[3, 7]], None)
    assert first == again
    assert subset[3] == first[3]
    assert subset[7] == first[7]


def test_a_numeric_hue_maps_through_viridis_over_its_range():
    viridis = matplotlib.colormaps["viridis"]
    table = pd.DataFrame({"hue": [10.0, 20.0, 30.0]})
    styles = point_styles(table, "hue")
    # hex colours are 8 bits a channel, so the comparison allows one step
    assert to_rgba(styles[0].color) == pytest.approx(viridis(0.0), abs=1 / 255)
    assert to_rgba(styles[1].color) == pytest.approx(viridis(0.5), abs=1 / 255)
    assert to_rgba(styles[2].color) == pytest.approx(viridis(1.0), abs=1 / 255)


def test_a_text_hue_takes_tab10_in_order_of_first_appearance():
    tab10 = matplotlib.colormaps["tab10"].colors
    table = pd.DataFrame({"hue": ["Au", "Si", "Au", "C"]})
    styles = point_styles(table, "hue")
    assert styles[0].color == styles[2].color
    assert to_rgba(styles[0].color) == pytest.approx(to_rgba(tab10[0]))
    assert to_rgba(styles[1].color) == pytest.approx(to_rgba(tab10[1]))
    assert to_rgba(styles[3].color) == pytest.approx(to_rgba(tab10[2]))


def test_summary_data_drops_points_without_a_value_and_sorts_by_x():
    table = pd.DataFrame(
        {
            "x": [3.0, 1.0, 2.0, 4.0],
            "y": [30.0, 10.0, np.nan, 40.0],
            "y_err": [3.0, 1.0, np.nan, 4.0],
        }
    )
    data = summary_data(table, "x", "y", None)
    assert list(data["x"]) == [1.0, 3.0, 4.0]
    assert list(data["y"]) == [10.0, 30.0, 40.0]


def test_draw_summary_draws_error_bars_and_a_colour_bar_for_a_numeric_hue():
    table = pd.DataFrame(
        {
            "energy": [10.0, 20.0, 10.0, 20.0],
            "angle": [5.0, 5.0, 7.0, 7.0],
            "y": [1.0, 2.0, 3.0, 4.0],
            "y_err": [0.1, 0.2, 0.3, 0.4],
        }
    )
    data = summary_data(table, "energy", "y", "angle")
    fig = Figure()
    ax = fig.add_subplot()
    draw_summary(data, "energy", "y", "angle", point_styles(data, "angle"), ax)
    assert any(isinstance(c, ErrorbarContainer) for c in ax.containers)
    assert len(fig.axes) == 2  # the colour bar's own axes


def test_draw_summary_with_a_text_hue_has_a_legend_and_no_colour_bar():
    table = pd.DataFrame({"x": [1.0, 2.0, 1.0, 2.0], "y": [1.0, 2.0, 3.0, 4.0], "el": list("AABB")})
    data = summary_data(table, "x", "y", "el")
    fig = Figure()
    ax = fig.add_subplot()
    draw_summary(data, "x", "y", "el", point_styles(data, "el"), ax)
    assert len(fig.axes) == 1
    assert ax.get_legend() is not None


def test_select_points_takes_dotted_names_in_backticks():
    table = pd.DataFrame({"ion.energy_keV": [5.0, 10.0, 20.0, 30.0], "y": [1, 2, 3, 4]})
    assert select_points(table, "`ion.energy_keV` > 10") == [2, 3]


def test_a_bad_filter_raises_value_error_with_the_pandas_message():
    table = pd.DataFrame({"x": [1.0, 2.0]})
    with pytest.raises(ValueError):
        select_points(table, "`no such column` >")
    with pytest.raises(ValueError):
        select_points(table, "")


def test_draw_overlay_draws_one_curve_per_point_with_its_style():
    plot = BY_KEY["ionization"]
    frames = {}
    for index in (0, 1):
        depth = np.linspace(0.0, 100.0, 5)
        frames[index] = pd.DataFrame(
            {
                "depth_nm": depth,
                "depth_A": depth * 10.0,
                "ions": depth + index,
                "recoils": depth,
            }
        )
    styles = {0: Style("#111111", "-", "o"), 1: Style("#222222", "--", "s")}
    labels = {0: "energy=10", 1: "energy=20"}
    fig = Figure()
    ax = fig.add_subplot()
    draw_overlay(plot, frames, styles, labels, ax)
    assert len(ax.lines) == 2
    assert ax.lines[1].get_linestyle() == "--"
    assert [t.get_text() for t in ax.get_legend().get_texts()] == ["energy=10", "energy=20"]
    assert ax.get_title() == plot.title


def test_stacked_frames_split_back_to_the_same_points():
    frames = {
        2: pd.DataFrame({"depth_nm": [1.0, 2.0], "ions": [3.0, 4.0]}),
        5: pd.DataFrame({"depth_nm": [1.0, 2.0], "ions": [5.0, 6.0]}),
    }
    back = split_frames(stack_frames(frames))
    assert list(back) == [2, 5]
    assert list(back[5]["ions"]) == [5.0, 6.0]


def test_the_curve_limit_is_twelve():
    assert CURVE_LIMIT == 12


def test_value_columns_are_the_extracted_values_without_errors_or_metadata(base):
    table = pd.DataFrame(
        {
            "ion.energy_keV": [10.0],
            "id": ["abc"],
            "depth_nm": [1.0],
            "depth_nm_err": [0.1],
            "n_ions": [200],
            "seeds": ["7"],
            "status": ["done"],
            "error": [None],
            "path": ["points/x"],
        }
    )
    assert value_columns(table, ["ion.energy_keV"]) == ["depth_nm"]
