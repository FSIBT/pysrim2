"""The plot canvas: drawing in a thread, the stale-result rule, log scales and data export."""

import shutil
from pathlib import Path

import pytest

pytest.importorskip("PySide6")
pytest.importorskip("pytestqt")

from matplotlib.colors import to_hex  # noqa: E402
from PySide6.QtGui import QPalette  # noqa: E402
from PySide6.QtWidgets import QApplication, QFileDialog  # noqa: E402

from srim.app import plot_canvas as canvas_module  # noqa: E402
from srim.app.plot_canvas import PlotCanvas, PlotWindow  # noqa: E402
from srim.app.plots import CATALOGUE  # noqa: E402
from srim.output import Results  # noqa: E402

FIXTURE = Path(__file__).resolve().parents[2] / "test_files" / "2"
BY_KEY = {plot.key: plot for plot in CATALOGUE}


@pytest.fixture
def results(tmp_path):
    """The outputs of test_files/2 (ionization, vacancies, phonons, energy and range)."""
    folder = tmp_path / "point"
    shutil.copytree(FIXTURE, folder)
    return Results(str(folder))


@pytest.fixture
def canvas(qtbot):
    widget = PlotCanvas()
    qtbot.addWidget(widget)
    return widget


@pytest.fixture
def captured(monkeypatch):
    """Replace the thread: the callbacks are kept, and the test calls them itself."""
    calls = []

    def fake_run_in_thread(fn, done, failed):
        calls.append((fn, done, failed))

    monkeypatch.setattr(canvas_module, "run_in_thread", fake_run_in_thread)
    return calls


def test_showing_a_plot_says_drawing_until_the_thread_is_done(canvas, results, captured):
    canvas.show_plot(BY_KEY["ionization"], results)
    assert canvas.message.text() == "Drawing…"
    fn, done, _failed = captured[-1]
    done(fn())
    assert canvas.message.text() == ""
    assert len(canvas.figure.axes) == 1


def test_a_late_result_for_a_plot_no_longer_shown_is_dropped(qtbot, canvas, results, captured):
    canvas.show_plot(BY_KEY["ionization"], results)
    canvas.show_plot(BY_KEY["vacancies"], results)
    old_fn, old_done, _ = captured[0]
    new_fn, new_done, _ = captured[1]
    with qtbot.assertNotEmitted(canvas.drawn):
        old_done(old_fn())
    assert canvas.message.text() == "Drawing…"
    with qtbot.waitSignal(canvas.drawn):
        new_done(new_fn())
    assert canvas.figure.axes[0].get_title() == BY_KEY["vacancies"].title


def test_a_real_draw_happens_in_a_thread_and_emits_drawn(qtbot, canvas, results):
    with qtbot.waitSignal(canvas.drawn, timeout=10000):
        canvas.show_plot(BY_KEY["ionization"], results)
    assert len(canvas.figure.axes[0].lines) == 2


def test_a_plot_that_cannot_be_drawn_says_what_it_needs(qtbot, canvas, results):
    with qtbot.waitSignal(canvas.failed, timeout=10000):
        canvas.show_plot(BY_KEY["stopping_map"], results)
    assert canvas.message.text().startswith("needs RANGE_3D.txt")
    assert not canvas.copy_button.isEnabled()


def test_copy_data_puts_the_csv_of_the_plot_on_the_clipboard(qtbot, canvas, results):
    with qtbot.waitSignal(canvas.drawn, timeout=10000):
        canvas.show_plot(BY_KEY["ionization"], results)
    canvas.copy_data()
    header = QApplication.clipboard().text().splitlines()[0]
    assert "depth_nm" in header.split(",")


def test_save_data_writes_the_csv_through_the_file_dialog(
    qtbot, canvas, results, tmp_path, monkeypatch
):
    target = tmp_path / "ionization"  # no suffix: the CSV suffix is added
    monkeypatch.setattr(
        QFileDialog, "getSaveFileName", staticmethod(lambda *a, **k: (str(target), ""))
    )
    with qtbot.waitSignal(canvas.drawn, timeout=10000):
        canvas.show_plot(BY_KEY["ionization"], results)
    canvas.save_data()
    saved = target.with_suffix(".csv")
    assert "depth_nm" in saved.read_text().splitlines()[0].split(",")


def test_a_cancelled_save_writes_nothing(qtbot, canvas, results, tmp_path, monkeypatch):
    monkeypatch.setattr(QFileDialog, "getSaveFileName", staticmethod(lambda *a, **k: ("", "")))
    with qtbot.waitSignal(canvas.drawn, timeout=10000):
        canvas.show_plot(BY_KEY["ionization"], results)
    canvas.save_data()
    assert list(tmp_path.glob("*.csv")) == []


def test_log_y_sets_the_y_scale_and_unchecking_restores_it(qtbot, canvas, results):
    with qtbot.waitSignal(canvas.drawn, timeout=10000):
        canvas.show_plot(BY_KEY["ionization"], results)
    ax = canvas.figure.axes[0]
    canvas.log_y.setChecked(True)
    assert ax.get_yscale() == "log"
    canvas.log_y.setChecked(False)
    assert ax.get_yscale() == "linear"


def test_the_drawing_uses_the_window_palette(qtbot, canvas, results):
    with qtbot.waitSignal(canvas.drawn, timeout=10000):
        canvas.show_plot(BY_KEY["ionization"], results)
    palette = canvas.palette()
    foreground = palette.color(QPalette.ColorRole.WindowText).name()
    background = palette.color(QPalette.ColorRole.Window).name()
    ax = canvas.figure.axes[0]
    assert to_hex(ax.title.get_color()) == to_hex(foreground)
    assert to_hex(ax.get_facecolor()) == to_hex(background)


def test_a_pop_out_window_has_the_plot_title_and_draws_it(qtbot, results):
    window = PlotWindow(BY_KEY["vacancies"])
    qtbot.addWidget(window)
    assert window.windowTitle() == BY_KEY["vacancies"].title
    with qtbot.waitSignal(window.canvas.drawn, timeout=10000):
        window.canvas.show_plot(window.plot, results)
    assert window.canvas.figure.axes[0].get_title() == BY_KEY["vacancies"].title


def test_a_closed_pop_out_says_so(qtbot):
    window = PlotWindow(BY_KEY["vacancies"])
    qtbot.addWidget(window)
    window.show()
    with qtbot.waitSignal(window.closed, timeout=2000):
        window.close()


def test_a_refresh_is_skipped_while_a_request_is_outstanding(canvas, results, captured):
    canvas.show_plot(BY_KEY["ionization"], results)
    assert len(captured) == 1
    canvas.refresh(BY_KEY["ionization"], results)  # still outstanding: nothing new starts
    assert len(captured) == 1
    fn, done, _ = captured[0]
    done(fn())
    canvas.refresh(BY_KEY["ionization"], results)  # nothing outstanding: the refresh starts
    assert len(captured) == 2


def test_a_refresh_keeps_the_figure_and_buttons_until_the_new_data_arrives(
    qtbot, canvas, results, captured
):
    canvas.show_plot(BY_KEY["ionization"], results)
    fn, done, _ = captured[0]
    done(fn())
    assert canvas.copy_button.isEnabled()
    canvas.refresh(BY_KEY["ionization"], results)
    assert canvas.message.text() == ""  # no "Drawing…" flicker on a refresh
    assert canvas.copy_button.isEnabled()
    assert len(canvas.figure.axes) == 1
    fn, done, _ = captured[-1]
    with qtbot.waitSignal(canvas.drawn):
        done(fn())


def test_a_newly_chosen_plot_says_drawing_and_disables_the_buttons(canvas, results, captured):
    canvas.show_plot(BY_KEY["ionization"], results)
    fn, done, _ = captured[0]
    done(fn())
    canvas.show_plot(BY_KEY["vacancies"], results)
    assert canvas.message.text() == "Drawing…"
    assert not canvas.copy_button.isEnabled()


def test_a_late_result_after_the_window_closed_is_ignored(qtbot, results, captured):
    window = PlotWindow(BY_KEY["vacancies"])
    qtbot.addWidget(window)
    window.show()
    window.canvas.show_plot(BY_KEY["vacancies"], results)
    fn, done, failed = captured[-1]
    window.close()
    with qtbot.assertNotEmitted(window.canvas.drawn):
        done(fn())
    failed(RuntimeError("late"))  # a late failure is ignored too, without an error
