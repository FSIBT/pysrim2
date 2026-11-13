"""Screenshots of the desktop app's window, taken offscreen (PySide6, the ``app`` extra).

The entries ``app_setup.py`` and ``app_results.py`` call these functions. Nothing here runs SRIM,
a check or an install: the window gets a checks function that returns no checks, and the run
folder is built from the SRIM outputs of ``test_files/2``, the way a finished run leaves it.
PySide6 is imported inside the functions, so this module loads without the extra. The app's
data folder (settings, recent files) is a temporary folder during a picture.
"""

import os
import shutil
import tempfile
import time
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
FIXTURE = ROOT / "test_files" / "2"
#: the size of the window in the pictures (pixels)
SIZE = (1100, 720)
#: every wait of the build is bounded by this (a broken run cannot hang the build)
WAIT_SECONDS = 20.0

#: the 3 MeV Au run of test_files/2 (its TRIM.IN): 1000 ions into 800 nm of SiC, a full cascade
AU_IN_SIC = {
    "version": 1,
    "ion": {"symbol": "Au", "energy_keV": 3000.0, "angle_deg": 0.0},
    "number_ions": 1000,
    "calculation": 2,
    "seed": 0,
    "settings": {},
    "target": [
        {
            "name": "SiC",
            "thickness_nm": 800.0,
            "density_g_cm3": 3.21,
            "phase": 0,
            "elements": [
                {
                    "symbol": "Si",
                    "stoich": 0.5,
                    "displacement_eV": 15.0,
                    "lattice_eV": 2.0,
                    "surface_eV": 4.7,
                },
                {
                    "symbol": "C",
                    "stoich": 0.5,
                    "displacement_eV": 28.0,
                    "lattice_eV": 3.0,
                    "surface_eV": 7.41,
                },
            ],
        }
    ],
}


@contextmanager
def _temporary_home() -> Iterator[Path]:
    """A temporary folder, and PYSRIM2_HOME pointed at it while the picture is made."""
    saved = os.environ.get("PYSRIM2_HOME")
    with tempfile.TemporaryDirectory() as tmp:
        os.environ["PYSRIM2_HOME"] = str(Path(tmp) / "pysrim2")
        try:
            yield Path(tmp)
        finally:
            if saved is None:
                os.environ.pop("PYSRIM2_HOME", None)
            else:
                os.environ["PYSRIM2_HOME"] = saved


def _application():
    """The Qt application, on the offscreen platform (chosen before Qt is loaded)."""
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtWidgets import QApplication

    return QApplication.instance() or QApplication([])


def _wait(app, ready: Callable[[], bool], what: str) -> None:
    """Process the events until ``ready()`` is true; raise after WAIT_SECONDS."""
    deadline = time.monotonic() + WAIT_SECONDS
    while not ready():
        if time.monotonic() > deadline:
            raise RuntimeError(f"timed out waiting for {what}")
        app.processEvents()
        time.sleep(0.01)


def _no_checks(_route):
    return []


def _show(app, window) -> None:
    """Show the window at SIZE and let it lay out (a shown tab draws its plot)."""
    window.resize(*SIZE)
    window.show()
    for _ in range(5):
        app.processEvents()


def _grab(app, window, out: Path) -> Path:
    """Show the window, and save it as a PNG to ``out``."""
    _show(app, window)
    if not window.grab().save(str(out), "PNG"):
        raise RuntimeError(f"cannot write {out}")
    return out


def grab_setup(setup: dict, out: Path) -> Path:
    """The window with the run setup ``setup`` (the layout of ``RunSpec.to_dict()``) open on the
    Geometry tab, saved to ``out``."""
    from srim.app.settings import AppSettings
    from srim.app.window import MainWindow
    from srim.spec import RunSpec

    with _temporary_home() as home:
        app = _application()
        path = home / "setup" / "run.yaml"
        path.parent.mkdir(parents=True)
        RunSpec.from_dict(setup, base_dir=path.parent).to_yaml(
            path, provenance={"source": "docs/gallery/app_setup.py"}
        )
        window = MainWindow(AppSettings(theme="light"), checks_fn=_no_checks)
        try:
            window.document.open(path)
            window.tabs.setCurrentWidget(window.editors["geometry"])
            return _grab(app, window, out)
        finally:
            window.close()


def finished_run(folder: Path) -> Path:
    """A finished run folder in ``folder``: the scan of one point (``beam_scan``), the point's
    outputs copied from test_files/2 and ``result/scan.csv``, as a run leaves them. No SRIM run."""
    from srim.beam import beam_scan
    from srim.scan.executor import MARKER_NAME
    from srim.scan.result import ScanResult
    from srim.spec import RunSpec

    spec = RunSpec.from_dict(AU_IN_SIC, base_dir=folder)
    scan = beam_scan(spec)
    point = scan.points()[0]
    rel = f"points/{point.id[:16]}"
    shutil.copytree(FIXTURE, folder / rel)
    point.spec.to_yaml(folder / rel / "run.yaml", provenance={"source": "test_files/2"})
    scan.to_yaml(folder / "scan.yaml", provenance={"source": "test_files/2"})
    rows = [{"index": point.index, "status": "done", "path": rel, "n_ions": spec.number_ions}]
    ScanResult.from_rows(scan, rows, base_dir=folder).to_csv(folder / "result")
    (folder / MARKER_NAME).write_text("pysrim2 scan folder\n", encoding="utf-8")
    return folder


def grab_results(out: Path, key: str = "ionization") -> Path:
    """The window with a finished run open on the Results tab, the plot ``key`` drawn, saved
    to ``out``."""
    from srim.app.plots import CATALOGUE
    from srim.app.settings import AppSettings
    from srim.app.window import MainWindow

    plot = next(p for p in CATALOGUE if p.key == key)
    with _temporary_home() as home:
        app = _application()
        folder = finished_run(home / "setup-runs" / "20261112T000000Z")
        window = MainWindow(AppSettings(theme="light"), checks_fn=_no_checks)
        try:
            window.run.load(folder)
            _wait(app, lambda: window.run.result is not None, "the run folder")
            _show(app, window)
            window.tabs.setCurrentWidget(window.results)
            canvas = window.results.canvas
            window.results.plot_list.setCurrentItem(window.results.item_for(key))

            def drawn() -> bool:
                axes = canvas.figure.axes
                return (
                    canvas.message.text() == "" and bool(axes) and axes[0].get_title() == plot.title
                )

            _wait(app, drawn, f"the {key} plot")
            return _grab(app, window, out)
        finally:
            window.close()
