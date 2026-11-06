"""The browser viewer (``srim.ui.tui.view``): replay and follow of the events file, the keys as
requests in the control file, and the start-up checks. A pilot drives the app on a synthetic
events file (no SRIM, no server). Every wait has a timeout."""

import asyncio
import json
import queue
import threading

import pytest

pytest.importorskip("textual")
pytest.importorskip("textual_plotext")

from srim.scan import Scan  # noqa: E402
from srim.scan.events import (  # noqa: E402
    PointFinished,
    PointQueued,
    ScanPaused,
    ScanStarted,
)
from srim.spec import RunSpec  # noqa: E402
from srim.ui import web  # noqa: E402
from srim.ui.tui import view  # noqa: E402
from srim.ui.tui.app import PlotScreen  # noqa: E402

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
TIMEOUT = 15.0


def make_scan_folder(folder):
    """A scan folder with a scan of three points (energies 10, 20, 30 keV)."""
    scan = Scan(BASE, grid={"ion.energy_keV": [10.0, 20.0, 30.0]}, extract=["implant_summary"])
    scan.to_yaml(folder / "scan.yaml")
    return scan


def write_events(folder, *events, newline=True):
    text = "".join(event.to_json() + "\n" for event in events)
    if not newline:
        text = text.rstrip("\n")
    with (folder / web.EVENTS_FILE).open("a", encoding="utf-8") as file:
        file.write(text)


def start_events(scan, folder):
    yield ScanStarted(n_points=3, workers=2, base_seed=7, scan_dir=str(folder), time=0.0)
    for point in scan.points():
        yield PointQueued(point.index, point.id, time=0.0)


def finished(scan, index, value):
    point = scan.points()[index]
    return PointFinished(index, point.id, {"mean": value}, 100, 1.0, time=0.0)


async def until(predicate, timeout=TIMEOUT):
    async def poll():
        while not predicate():
            await asyncio.sleep(0.02)

    await asyncio.wait_for(poll(), timeout)


def run(coro):
    return asyncio.run(asyncio.wait_for(coro, 120))


@pytest.fixture
def folder(tmp_path):
    return tmp_path


def make_viewer(folder, scan, view_name="plot", plot=None):
    """The app and its follower (not started: the test starts and stops them)."""
    events = queue.Queue()
    pause, stop = threading.Event(), threading.Event()
    follower = view.EventFollower(folder / web.EVENTS_FILE, events, pause, poll=0.01)
    app = view.WebScanApp(
        scan,
        events,
        folder=folder,
        stop_event=stop,
        pause_event=pause,
        view=view_name,
        plot=plot,
    )
    return app, follower


def stop_follower(follower):
    follower.stopped.set()
    follower.join(TIMEOUT)


def test_a_late_viewer_shows_every_point_that_has_finished(folder):
    scan = make_scan_folder(folder)
    write_events(
        folder, *start_events(scan, folder), finished(scan, 0, 1.0), finished(scan, 2, 3.0)
    )
    app, follower = make_viewer(folder, scan)
    follower.start()

    async def scenario():
        async with app.run_test(size=(120, 40)):
            await until(lambda: isinstance(app.screen, PlotScreen) and app.screen.status_text)
            await until(lambda: "done 2/3" in app.screen.status_text)
            assert "queued 1" in app.screen.status_text

    try:
        run(scenario())
    finally:
        stop_follower(follower)


def test_lines_appended_to_the_events_file_appear_in_the_view(folder):
    scan = make_scan_folder(folder)
    write_events(folder, *start_events(scan, folder))
    app, follower = make_viewer(folder, scan)
    follower.start()

    async def scenario():
        async with app.run_test(size=(120, 40)):
            await until(lambda: "done 0/3" in app.screen.status_text)
            write_events(folder, finished(scan, 1, 2.0))
            await until(lambda: "done 1/3" in app.screen.status_text)
            write_events(folder, finished(scan, 0, 1.0), newline=False)  # still being written
            await asyncio.sleep(0.1)
            assert "done 1/3" in app.screen.status_text
            with (folder / web.EVENTS_FILE).open("a", encoding="utf-8") as file:
                file.write("\n")
            await until(lambda: "done 2/3" in app.screen.status_text)

    try:
        run(scenario())
    finally:
        stop_follower(follower)


def test_the_follower_takes_complete_lines_only_and_skips_what_it_does_not_know(folder):
    scan = make_scan_folder(folder)
    path = folder / web.EVENTS_FILE
    path.write_text("", encoding="utf-8")
    events = queue.Queue()
    pause = threading.Event()
    follower = view.EventFollower(path, events, pause, poll=0.01)
    write_events(folder, *start_events(scan, folder))
    with path.open("a", encoding="utf-8") as file:
        file.write('{"kind": "from_the_future"}\nnot json\n\n')
    assert follower.pump() == 4  # scan_started, three queued; the unknown lines are skipped
    assert events.qsize() == 4
    with path.open("a", encoding="utf-8") as file:
        file.write('{"kind": "scan_paused", "paused": true, "time": 1.0}')
    assert follower.pump() == 0  # no newline yet
    assert not pause.is_set()
    with path.open("a", encoding="utf-8") as file:
        file.write("\n")
    assert follower.pump() == 1
    assert pause.is_set()  # the scan's pause state comes back in the events


def test_a_new_run_that_truncates_the_file_is_read_from_its_start(folder):
    scan = make_scan_folder(folder)
    write_events(folder, *start_events(scan, folder))
    follower = view.EventFollower(folder / web.EVENTS_FILE, queue.Queue(), threading.Event())
    assert follower.pump() == 4
    (folder / web.EVENTS_FILE).write_text("", encoding="utf-8")
    write_events(folder, ScanPaused(paused=False, time=0.0))
    assert follower.pump() == 1


def test_p_and_q_write_requests_and_do_not_change_the_flags_alone(folder):
    scan = make_scan_folder(folder)
    write_events(folder, *start_events(scan, folder))
    app, follower = make_viewer(folder, scan)
    follower.start()
    control = folder / web.CONTROL_FILE

    async def scenario():
        async with app.run_test(size=(120, 40)) as pilot:
            await until(lambda: "done 0/3" in app.screen.status_text)
            await pilot.press("p")
            await until(lambda: control.exists() and control.read_text() == "pause\n")
            write_events(folder, ScanPaused(paused=True, time=0.0))
            await until(lambda: app.pause_event.is_set())
            await pilot.press("p")
            await until(lambda: control.read_text() == "pause\nresume\n")
            await pilot.press("q")
            await until(lambda: control.read_text() == "pause\nresume\nstop\n")
            await pilot.press("q")  # a second q writes nothing more
            await pilot.pause()
            assert control.read_text() == "pause\nresume\nstop\n"
            assert app.stop_event.is_set()

    try:
        run(scenario())
    finally:
        stop_follower(follower)


def test_several_viewers_of_one_scan_show_the_same_state(folder):
    scan = make_scan_folder(folder)
    write_events(folder, *start_events(scan, folder), finished(scan, 1, 2.0))
    first, follower_one = make_viewer(folder, scan)
    second, follower_two = make_viewer(folder, scan)
    follower_one.start()
    follower_two.start()

    async def scenario():
        async with first.run_test(size=(120, 40)):
            async with second.run_test(size=(120, 40)):
                await until(lambda: "done 1/3" in first.screen.status_text)
                await until(lambda: "done 1/3" in second.screen.status_text)
                write_events(folder, finished(scan, 2, 3.0))
                await until(lambda: "done 2/3" in first.screen.status_text)
                await until(lambda: "done 2/3" in second.screen.status_text)

    try:
        run(scenario())
    finally:
        stop_follower(follower_one)
        stop_follower(follower_two)


def test_a_folder_that_is_not_a_scan_folder_exits_2(folder, capsys):
    assert view.main([str(folder)]) == 2
    assert "not a scan folder" in capsys.readouterr().err


def test_a_bad_plot_option_exits_2_before_the_view_starts(folder, capsys):
    make_scan_folder(folder)
    assert view.main([str(folder), "--plot", "nope,mean"]) == 2
    assert "not a scanned parameter" in capsys.readouterr().err


def write_run_header(folder, run_id):
    with (folder / web.EVENTS_FILE).open("a", encoding="utf-8") as file:
        file.write(json.dumps({"kind": web.RUN_KIND, "run_id": run_id}) + "\n")


def test_a_run_header_is_not_an_event_and_a_new_run_id_starts_a_new_run(folder):
    scan = make_scan_folder(folder)
    path = folder / web.EVENTS_FILE
    write_run_header(folder, "a")
    write_events(folder, *start_events(scan, folder))
    events = queue.Queue()
    follower = view.EventFollower(path, events, threading.Event(), poll=0.01)
    assert follower.pump() == 4  # the header is not counted
    assert events.get_nowait() == view.NewRun("a")
    events.queue.clear()  # the four events
    write_run_header(folder, "a")  # the same run again: nothing new
    assert follower.pump() == 0 and events.empty()
    write_run_header(folder, "b")
    assert follower.pump() == 0
    assert events.get_nowait() == view.NewRun("b")


def test_a_truncated_events_file_starts_a_new_run(folder):
    scan = make_scan_folder(folder)
    write_events(folder, *start_events(scan, folder))
    events = queue.Queue()
    follower = view.EventFollower(folder / web.EVENTS_FILE, events, threading.Event())
    follower.pump()
    events.queue.clear()
    (folder / web.EVENTS_FILE).write_text("", encoding="utf-8")
    follower.pump()
    assert events.get_nowait() == view.NewRun(None)


def test_a_new_run_resets_what_the_viewer_shows(folder):
    scan = make_scan_folder(folder)
    write_run_header(folder, "a")
    write_events(
        folder, *start_events(scan, folder), finished(scan, 0, 1.0), finished(scan, 2, 3.0)
    )
    app, follower = make_viewer(folder, scan)
    follower.start()

    async def scenario():
        async with app.run_test(size=(120, 40)):
            await until(lambda: "done 2/3" in app.screen.status_text)
            # a new run in the same folder: the file is started again
            (folder / web.EVENTS_FILE).write_text("", encoding="utf-8")
            write_run_header(folder, "b")
            write_events(folder, *start_events(scan, folder))
            await until(lambda: "done 0/3" in app.screen.status_text)
            assert "queued 3" in app.screen.status_text

    try:
        run(scenario())
    finally:
        stop_follower(follower)
