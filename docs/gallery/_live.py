"""Screenshots of the live scan views (Textual), taken from a scripted event stream.

A 6 x 2 grid (energy x angle) of boron ions into 1000 nm of silicon is run in the app
without any SRIM run: the events are put in the queue by hand, nine points are finished,
two are running and one is queued. The values are illustrative (``33`` Angstrom per keV of
depth, scaled by the cosine of the angle, roughly the depth of boron in silicon); they are
not the output of a SRIM run.

Entry scripts import :func:`screenshot` from here (see the entries ``tui_plot.py`` and
``tui_dashboard.py``). Textual is imported inside the function, so the module loads without it.
"""

import asyncio
import math
import queue
import threading
from pathlib import Path

from srim.scan import Scan
from srim.scan.events import (
    PointFinished,
    PointProgress,
    PointQueued,
    PointStarted,
    ScanStarted,
    WorkerStats,
)
from srim.spec import RunSpec

WAIT_SECONDS = 15.0  # every wait of the script is bounded by this (a broken run cannot hang)
SIZE = (120, 36)  # terminal size of the pictures (the SVG stays under 300 KB)
WORKERS = 2
DONE = 9  # points finished; 2 are running and 1 is queued (12 points in all)

BASE = RunSpec.from_dict(
    {
        "ion": {"symbol": "B", "energy_keV": 20.0, "angle_deg": 0.0},
        "number_ions": 100,
        "seed": 1,
        "target": [
            {
                "name": "Si",
                "thickness_nm": 1000.0,
                "density_g_cm3": 2.33,
                "elements": [{"symbol": "Si", "stoich": 1.0}],
            }
        ],
    }
)


def scan():
    """The scan of the pictures: energy x angle, the implantation summary of each point."""
    return Scan(
        BASE,
        grid={"ion.energy_keV": [10.0, 20.0, 30.0, 40.0, 50.0, 60.0], "ion.angle_deg": [0.0, 30.0]},
        extract=["implant_summary"],
        seed=7,
    )


def script(scan_obj, folder):
    """The events of the scan so far, in the order the scan emits them (illustrative values)."""
    points = scan_obj.points()
    events = [ScanStarted(len(points), WORKERS, 7, str(folder))]
    events += [PointQueued(p.index, p.id) for p in points]
    for p in points[:DONE]:
        worker = p.index % WORKERS
        energy, angle = p.params["ion.energy_keV"], p.params["ion.angle_deg"]
        depth = 33.0 * energy * math.cos(math.radians(angle))  # illustrative, Angstrom
        events.append(PointStarted(p.index, p.id, worker, 1))
        values = {"mean_depth_A": depth, "mean_depth_A_err": 0.03 * depth}
        events.append(PointFinished(p.index, p.id, values, 100, 12.0 + p.index))
        events.append(WorkerStats(worker, 8.0 + 0.5 * worker))
    for p, ions in zip(points[DONE : DONE + 2], (60, 35), strict=True):
        events.append(PointStarted(p.index, p.id, p.index % WORKERS, 1))
        events.append(PointProgress(p.index, ions, 100))
    return events


async def _wait(predicate):
    async def poll():
        while not predicate():
            await asyncio.sleep(0.05)

    await asyncio.wait_for(poll(), WAIT_SECONDS)


def screenshot(view: str, outdir: Path, filename: str) -> Path:
    """Run the scan's events through the ``view`` (``"plot"`` or ``"dashboard"``) of the app,
    save the screen as ``outdir / filename`` (an SVG) and return that path."""
    from srim.ui.tui.app import REFRESH_SECONDS, DashboardScreen, PlotScreen, ScanApp

    obj = scan()
    events = queue.Queue()
    for event in script(obj, outdir):
        events.put(event)
    app = ScanApp(
        obj,
        events,
        stop_event=threading.Event(),
        pause_event=threading.Event(),
        view=view,
        wait=True,
    )

    def drawn():
        """The scan has been applied to the state, and the view has been redrawn since."""
        counts = app.state.counts()
        if counts["done"] != DONE or counts["running"] != 2:
            return False
        if view == "plot":
            return isinstance(app.screen, PlotScreen)
        return isinstance(app.screen, DashboardScreen) and app.screen.table.row_count == len(
            obj.points()
        )

    async def run():
        async with app.run_test(size=SIZE) as pilot:
            await _wait(drawn)
            await pilot.pause(2 * REFRESH_SECONDS)  # the next tick redraws the view
            app.save_screenshot(filename=filename, path=str(outdir))
            events.put(None)  # the scan thread is over: the app exits
            await pilot.pause()

    asyncio.run(asyncio.wait_for(run(), 2 * WAIT_SECONDS + 30))
    return Path(outdir) / filename
