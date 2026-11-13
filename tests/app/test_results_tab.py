"""The Results tab: the plots of one run's results (finished or running), the banner, pop-outs,
and the samples a finished exact run gives the Beam tab.

The tab is driven by a fake controller (the controller's signals and attributes, no run), and
the run folders are built from test_files/2 by ``run_folders`` (no SRIM).
"""

import pytest

pytest.importorskip("PySide6")
pytest.importorskip("pytestqt")

from copy import deepcopy  # noqa: E402

from PySide6.QtCore import QObject, Qt, Signal  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402
from run_folders import DONE, finished_scan  # noqa: E402

from srim.app.document import DocumentModel  # noqa: E402
from srim.app.model import to_spec  # noqa: E402
from srim.app.plot_canvas import PlotCanvas  # noqa: E402
from srim.app.plots import CATALOGUE  # noqa: E402
from srim.app.results_tab import NO_RESULTS_TEXT, ResultsTab  # noqa: E402
from srim.app.runs import MISSING_OUTPUTS_TEXT, ParallelChoice, plan_run  # noqa: E402
from srim.app.settings import AppSettings  # noqa: E402
from srim.app.window import MainWindow  # noqa: E402
from srim.beam.exact import sample_ions, write_trim_dat  # noqa: E402
from srim.scan import Scan  # noqa: E402
from srim.ui.tui.state import ScanState  # noqa: E402

BY_KEY = {plot.key: plot for plot in CATALOGUE}
RANGE3D_TEXT = "needs RANGE_3D.txt; enable 'Ranges (RANGE_3D.txt)' in Run settings"
BANNER = "From the previous setup"
DEPTH_PROFILES = ("ionization", "vacancies", "phonons", "recoil_energy", "range_profile")


class FakeRun(QObject):
    """The run controller's signals and attributes, without a run."""

    started = Signal()
    updated = Signal()
    finished = Signal(object)
    failed = Signal(str)

    def __init__(self):
        super().__init__()
        self.running = False
        self.state = None
        self.folder = None
        self.snapshot = None
        self.result = None


@pytest.fixture
def run():
    return FakeRun()


@pytest.fixture
def document(tmp_path, write_setup, boron_data):
    """The boron setup of the conftest, opened from a run.yaml (so it has a path)."""
    doc = DocumentModel()
    doc.open(write_setup(tmp_path / "setup" / "run.yaml", boron_data))
    return doc


@pytest.fixture
def tab(qtbot, run, document):
    widget = ResultsTab(run, document, AppSettings(refresh_seconds=1))
    qtbot.addWidget(widget)
    yield widget
    for window in list(widget.pop_outs):
        window.close()


def scan_of(document, grid=None):
    """The setup's scan: one point, or a grid of energies (two points)."""
    base = to_spec(document.doc, document.base_dir)
    if grid is None:
        return plan_run(document.doc, document.base_dir, ParallelChoice())
    return Scan(base, grid=grid, seed=7)


def finish(run, document, folder, scan, outcomes, values=None):
    """The run ends with a result: the controller's attributes, then its ``finished`` signal."""
    result = finished_scan(folder, scan, outcomes, values)
    run.result = result
    run.folder = folder
    run.snapshot = deepcopy(document.doc)
    run.running = False
    run.finished.emit(result)
    return result


def finish_shown(qtbot, tab, run, document, folder, scan, outcomes):
    """The run ends while the Results tab is shown: the plot it selects is drawn (waited for)."""
    tab.show()
    with qtbot.waitSignal(tab.canvas.drawn, timeout=10000):
        return finish(run, document, folder, scan, outcomes)


@pytest.fixture
def folder(tmp_path):
    return tmp_path / "setup-runs" / "20261112T000000Z"


def test_before_any_results_it_says_no_results_yet(tab):
    assert tab.empty_label.text() == NO_RESULTS_TEXT
    assert tab.empty_label.isVisibleTo(tab)
    assert not tab.canvas.isVisibleTo(tab)
    assert not tab.banner.isVisibleTo(tab)


def test_a_single_run_lists_five_depth_profiles_and_disables_stopping_map(
    qtbot, tab, run, document, folder
):
    finish(run, document, folder, scan_of(document), [DONE])
    for key in DEPTH_PROFILES:
        item = tab.item_for(key)
        assert not item.isDisabled()
        assert item.parent().text(0) == "Depth profiles"
    stopping = tab.item_for("stopping_map")
    assert stopping.isDisabled()
    assert stopping.toolTip(0) == RANGE3D_TEXT


def test_a_single_run_hides_the_point_choice(qtbot, tab, run, document, folder):
    finish(run, document, folder, scan_of(document), [DONE])
    assert not tab.point_combo.isVisibleTo(tab)
    assert not tab.empty_label.isVisibleTo(tab)


def test_selecting_a_plot_draws_it_once_in_the_thread(qtbot, tab, run, document, folder):
    finish_shown(qtbot, tab, run, document, folder, scan_of(document), [DONE])
    with qtbot.waitSignal(tab.canvas.drawn, timeout=10000):
        tab.plot_list.setCurrentItem(tab.item_for("vacancies"))
    assert tab.canvas.figure.axes[0].get_title() == BY_KEY["vacancies"].title
    with qtbot.waitSignal(tab.canvas.drawn, timeout=10000):
        tab.plot_list.setCurrentItem(tab.item_for("ionization"))
    axes = tab.canvas.figure.axes
    assert len(axes) == 1
    assert len(axes[0].lines) == 2


def test_copy_data_puts_the_plot_csv_on_the_clipboard(qtbot, tab, run, document, folder):
    finish_shown(qtbot, tab, run, document, folder, scan_of(document), [DONE])
    tab.canvas.copy_data()
    header = QApplication.clipboard().text().splitlines()[0]
    assert "depth_nm" in header.split(",")


def test_log_y_on_the_tab_sets_the_scale(qtbot, tab, run, document, folder):
    finish_shown(qtbot, tab, run, document, folder, scan_of(document), [DONE])
    tab.canvas.log_y.setChecked(True)
    assert tab.canvas.figure.axes[0].get_yscale() == "log"


def test_editing_the_setup_shows_the_banner_and_undo_hides_it(qtbot, tab, run, document, folder):
    finish(run, document, folder, scan_of(document), [DONE])
    assert not tab.banner.isVisibleTo(tab)
    document.edit(lambda doc: doc.update(number_ions=300))
    assert tab.banner.isVisibleTo(tab)
    assert tab.banner.text() == BANNER
    document.undo()
    assert not tab.banner.isVisibleTo(tab)


def test_a_change_of_the_scan_section_also_shows_the_banner(qtbot, tab, run, document, folder):
    finish(run, document, folder, scan_of(document), [DONE])
    document.edit(lambda doc: doc.update(_scan={"grid": {"ion.energy_keV": [10.0, 20.0]}}))
    assert tab.banner.isVisibleTo(tab)


def test_pop_out_opens_the_plot_in_a_window_of_its_own(qtbot, tab, run, document, folder):
    finish_shown(qtbot, tab, run, document, folder, scan_of(document), [DONE])
    tab.pop_out.click()
    assert len(tab.pop_outs) == 1
    window = tab.pop_outs[0]
    assert window.windowTitle() == BY_KEY["ionization"].title
    # the pop-out draws its own copy when it opens (in a thread, so wait for the figure)
    qtbot.waitUntil(lambda: bool(window.canvas.figure.axes) and window.canvas.message.text() == "")
    window.close()
    assert tab.pop_outs == []


def live_run(run, document, folder, tab):
    """A run going with two points, point 0 done: the tab is set up as a run's start does."""
    scan = scan_of(document, grid={"ion.energy_keV": [10.0, 20.0]})
    state = ScanState(scan)
    finished_scan(folder, scan, [DONE, DONE])
    run.result = None
    run.state = state
    run.folder = folder
    run.running = True
    for point in state.points.values():
        point.status = "done"
    return state


def test_a_hidden_results_tab_does_not_redraw_on_a_tick(
    qtbot, tab, run, document, folder, monkeypatch
):
    live_run(run, document, folder, tab)
    calls = []
    original = PlotCanvas.show_plot

    def counting(self, plot, results):
        calls.append(plot.key)
        return original(self, plot, results)

    monkeypatch.setattr(PlotCanvas, "show_plot", counting)
    run.started.emit()  # the tab is not shown: the started run draws nothing either
    tab._on_tick()
    assert calls == []


def test_a_refresh_tick_redraws_only_the_visible_plot(
    qtbot, tab, run, document, folder, monkeypatch
):
    tab.show()
    live_run(run, document, folder, tab)
    with qtbot.waitSignal(tab.canvas.drawn, timeout=10000):
        run.started.emit()
    calls = []
    original = PlotCanvas.show_plot

    def counting(self, plot, results):
        calls.append(plot.key)
        return original(self, plot, results)

    monkeypatch.setattr(PlotCanvas, "show_plot", counting)
    tab._on_tick()
    assert calls == ["ionization"]
    # the real timer (one second here) redraws the same plot again and again
    qtbot.waitUntil(lambda: len(calls) >= 3, timeout=10000)
    assert set(calls) == {"ionization"}


def test_a_scan_lists_only_its_done_points(qtbot, tab, run, document, folder):
    scan = scan_of(document, grid={"ion.energy_keV": [10.0, 20.0]})
    finish(run, document, folder, scan, [DONE, "SRIM failed"])
    assert tab.point_combo.isVisibleTo(tab)
    assert tab.point_combo.count() == 1
    assert "10.0" in tab.point_combo.itemText(0)


def test_a_scan_where_every_point_failed_says_no_results_yet_with_the_error(
    qtbot, tab, run, document, folder
):
    scan = scan_of(document, grid={"ion.energy_keV": [10.0, 20.0]})
    finish(run, document, folder, scan, ["SRIM crashed", "SRIM crashed"])
    assert tab.empty_label.isVisibleTo(tab)
    assert NO_RESULTS_TEXT in tab.empty_label.text()
    assert "SRIM crashed" in tab.empty_label.text()
    assert not tab.canvas.isVisibleTo(tab)


def test_during_a_run_the_points_done_so_far_are_listed(qtbot, tab, run, document, folder):
    scan = scan_of(document, grid={"ion.energy_keV": [10.0, 20.0]})
    finished_scan(folder, scan, [DONE, "later"])  # point 0 has its outputs, point 1 is not done
    state = ScanState(scan)
    state.points[0].status = "done"
    run.result = None
    run.state = state
    run.folder = folder
    run.running = True
    tab.show()
    with qtbot.waitSignal(tab.canvas.drawn, timeout=10000):
        run.started.emit()
    assert tab.point_combo.count() == 1
    assert not tab.empty_label.isVisibleTo(tab)


def test_the_samples_of_a_finished_exact_run_reach_the_beam_tab(
    qtbot, tmp_path, write_setup, boron_data
):
    spec = {
        **boron_data,
        "ion": {
            "symbol": "B",
            "energy_keV": {"gaussian": {"mean": 20.0, "sigma": 1.0}},
            "angle_deg": 7.0,
        },
    }
    path = write_setup(tmp_path / "setup" / "run.yaml", spec)
    window = MainWindow(AppSettings(route="wine"), checks_fn=lambda route: [])
    qtbot.addWidget(window)
    window.document.open(path)
    scan = plan_run(window.document.doc, window.document.base_dir, ParallelChoice())
    folder = tmp_path / "setup-runs" / "20261112T000000Z"
    result = finished_scan(folder, scan, [DONE])
    point = scan.points()[0]
    write_trim_dat(
        sample_ions(point.spec, n=40, seed=3),
        folder / "points" / point.id[:16] / "TRIM.DAT",
        atomic_number=5,
        description="test",
        seed=3,
    )
    beam = window.editors["beam"]
    window.run.result = result
    window.run.folder = folder
    window.run.snapshot = deepcopy(window.document.doc)
    window.run.finished.emit(result)  # the tab is hidden: it draws when it is shown
    qtbot.waitUntil(lambda: len(beam.plots["energy_keV"].axes.containers) == 1, timeout=10000)
    window.document.edit(lambda doc: doc.update(number_ions=300))
    assert len(beam.plots["energy_keV"].axes.containers) == 0


SUMMARY_KEY = "scan:summary"
OVERLAY_KEY = "scan:overlay:ionization"
CURVE_NOTE = "Showing 12 of 20 curves; use the filter to choose"


def test_a_two_parameter_scan_shows_the_scan_group_and_a_single_run_hides_it(
    qtbot, tab, run, document, folder
):
    grid = {"ion.energy_keV": [10.0, 20.0], "ion.angle_deg": [5.0, 7.0]}
    finish(run, document, folder, scan_of(document, grid=grid), [DONE] * 4)
    group = tab.item_for(SUMMARY_KEY).parent()
    assert group.text(0) == "Scan"
    assert not group.isHidden()
    assert tab.item_for(OVERLAY_KEY).parent() is group


def test_a_single_run_hides_the_scan_group(qtbot, tab, run, document, folder):
    finish(run, document, folder, scan_of(document), [DONE])
    assert tab.item_for(SUMMARY_KEY).parent().isHidden()


def test_the_summary_redraws_when_the_colour_changes(qtbot, tab, run, document, folder):
    grid = {"ion.energy_keV": [10.0, 20.0], "ion.angle_deg": [5.0, 7.0]}
    scan = scan_of(document, grid=grid)
    values = [
        {"depth_nm": p.params["ion.energy_keV"] / 10 + p.params["ion.angle_deg"]}
        for p in scan.points()
    ]
    finish(run, document, folder, scan, [DONE] * 4, values)
    tab.show()
    with qtbot.waitSignal(tab.canvas.drawn, timeout=10000):
        tab.plot_list.setCurrentItem(tab.item_for(SUMMARY_KEY))
    assert tab.summary_bar.isVisibleTo(tab)
    assert tab.summary_x.currentText() == "ion.energy_keV"
    assert tab.summary_colour.currentText() == "ion.angle_deg"
    assert len(tab.canvas.figure.axes) == 2  # the colour bar of the numeric colour
    with qtbot.waitSignal(tab.canvas.drawn, timeout=10000):
        tab.summary_colour.setCurrentText("none")
    assert len(tab.canvas.figure.axes) == 1
    assert tab.summary_y.currentText() == "depth_nm"


def test_twenty_checked_points_draw_twelve_curves_and_say_so(qtbot, tab, run, document, folder):
    grid = {"ion.energy_keV": [float(e) for e in range(10, 30)]}
    finish(run, document, folder, scan_of(document, grid=grid), [DONE] * 20)
    tab.show()
    with qtbot.waitSignal(tab.canvas.drawn, timeout=10000):
        tab.plot_list.setCurrentItem(tab.item_for(OVERLAY_KEY))
    assert tab.overlay_points.count() == 20
    assert tab.overlay_note.text() == ""  # the first twelve are checked by default
    for row in range(tab.overlay_points.count()):
        tab.overlay_points.item(row).setCheckState(Qt.CheckState.Checked)
    assert tab.overlay_note.text() == CURVE_NOTE
    qtbot.waitUntil(
        lambda: (
            len(tab.canvas.figure.axes) == 1
            and len(tab.canvas.figure.axes[0].lines) == 12
            and tab.canvas.message.text() == ""
        ),
        timeout=10000,
    )


def test_the_filter_checks_the_matching_points_and_a_bad_one_says_why(
    qtbot, tab, run, document, folder
):
    grid = {"ion.energy_keV": [10.0, 20.0, 30.0]}
    finish(run, document, folder, scan_of(document, grid=grid), [DONE] * 3)
    tab.show()
    with qtbot.waitSignal(tab.canvas.drawn, timeout=10000):
        tab.plot_list.setCurrentItem(tab.item_for(OVERLAY_KEY))
    tab.overlay_filter.setText("`ion.energy_keV` > 15")
    tab.overlay_filter.returnPressed.emit()
    checked = [
        tab.overlay_points.item(row).text()
        for row in range(tab.overlay_points.count())
        if tab.overlay_points.item(row).checkState() == Qt.CheckState.Checked
    ]
    assert checked == ["ion.energy_keV=20.0", "ion.energy_keV=30.0"]
    tab.overlay_filter.setText("`no such column` >")
    tab.overlay_filter.returnPressed.emit()
    assert tab.overlay_status.isVisibleTo(tab)
    assert tab.overlay_status.text()


def test_an_overlay_says_how_many_points_had_no_data(qtbot, tab, run, document, folder):
    grid = {"ion.energy_keV": [10.0, 20.0, 30.0]}
    scan = scan_of(document, grid=grid)
    finish(run, document, folder, scan, [DONE] * 3)
    (folder / f"points/{scan.points()[0].id[:16]}" / "IONIZ.txt").unlink()
    tab.show()
    with qtbot.waitSignal(tab.canvas.drawn, timeout=10000):
        tab.plot_list.setCurrentItem(tab.item_for(OVERLAY_KEY))
    title = tab.canvas.figure.axes[0].get_title()
    assert title == f"{BY_KEY['ionization'].title} (1 of 3 points had no data)"


def test_a_finished_run_whose_point_folders_are_gone_says_its_results_are_missing(
    qtbot, tab, run, document, folder
):
    import shutil

    finish(run, document, folder, scan_of(document), [DONE])
    shutil.rmtree(folder / "points")
    run.finished.emit(run.result)
    assert tab.empty_label.text() == MISSING_OUTPUTS_TEXT
    assert tab.empty_label.isVisibleTo(tab)
    assert not tab.canvas.isVisibleTo(tab)


def test_a_scan_whose_point_folders_are_gone_says_so_on_its_scan_group(
    qtbot, tab, run, document, folder
):
    import shutil

    scan = scan_of(document, grid={"ion.energy_keV": [10.0, 20.0]})
    finish(run, document, folder, scan, [DONE, DONE])
    shutil.rmtree(folder / "points")
    run.finished.emit(run.result)
    assert tab.item_for("scan:summary").isDisabled()
    assert tab.item_for("scan:summary").toolTip(0) == MISSING_OUTPUTS_TEXT
