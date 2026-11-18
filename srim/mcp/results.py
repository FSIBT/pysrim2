"""Results of a run folder for the agent: the summary, the plot catalogue, tables, plots and
export (Qt-free; matplotlib is imported only by :func:`render_plot`, with the Agg canvas).

A folder is a run folder (or the folder of a scan). One point of a scan is read as a notebook
reads it (:func:`srim.notebook.load_run`), and the plots are the app's catalogue
(:mod:`srim.app.plots`), so a plot or table never reads more of a per-ion output than the
catalogue's row cap. Every failure a caller can cause is a
:class:`~srim.mcp.backend.BackendError` with a message for the agent.
"""

import math
from pathlib import Path
from typing import Any, NamedTuple

import numpy as np
import pandas as pd

from srim.analysis import implant_summary
from srim.app.plots import CATALOGUE, PlotDef, missing_reason, plot_data
from srim.mcp.backend import BackendError
from srim.notebook import load_run
from srim.output._common import _find_output
from srim.spec import RunSpec

#: the folder of a run folder that holds the plots the agent asked for
PLOTS_DIR = "plots"
#: the most rows :func:`table` returns, whatever it is asked for
MAX_TABLE_ROWS = 5000
#: the rows :func:`table` returns when it is not told how many
DEFAULT_TABLE_ROWS = 500
#: the per-ion plots: :func:`table` gives their first rows; the others are depth profiles,
#: thinned evenly over their depths
PER_ION = frozenset(
    {
        "stopping_map",
        "backscattered",
        "transmitted",
        "sputtered",
        "collision_map",
        "beam_energy",
        "beam_angle",
    }
)
#: the export formats: csv and parquet write one file per table into a folder, hdf5 one file
FORMATS = ("csv", "parquet", "hdf5")
#: the file of a point's setup, next to its outputs
SPEC_FILE = "run.yaml"
#: the pixel size of a plot: 8 x 5 in at 100 dpi
FIGSIZE = (8, 5)
DPI = 100

#: table name (as :func:`srim.export.TABLE_ATTRIBUTES`) -> attribute of the results
_TABLE_NAMES = {
    "ioniz": "ioniz",
    "vacancy": "vacancy",
    "novac": "novac",
    "e2recoil": "etorecoils",
    "phonon": "phonons",
    "range": "range",
    "range3d": "range3d",
    "exyz": "exyz",
    "backscat": "backscat",
    "transmit": "transmit",
    "sputter": "sputter",
    "collision": "collision",
}

#: the per-ion tables, and their files: :class:`~srim.output.Results` parses one on its first
#: access, so its presence is checked on disk instead (the same names as its lazy registry)
_PER_ION_FILES = {
    "range3d": "RANGE_3D.txt",
    "exyz": "EXYZ.txt",
    "backscat": "BACKSCAT.txt",
    "transmit": "TRANSMIT.txt",
    "sputter": "SPUTTER.txt",
    "collision": "COLLISON.txt",
}
_BY_KEY = {plot.key: plot for plot in CATALOGUE}


class _Opened(NamedTuple):
    """One point of a folder, opened: its results, its folder, its index and the scan's size."""

    results: Any
    point_dir: Path
    index: int
    points: int


# --- reading the point -----------------------------------------------------------------------


def _plot_of(key: Any) -> PlotDef:
    if not isinstance(key, str) or key not in _BY_KEY:
        raise BackendError(f"unknown plot {key!r} (plots: {', '.join(_BY_KEY)})")
    return _BY_KEY[key]


def _load(folder: Path):
    try:
        return load_run(folder)
    except FileNotFoundError as error:  # no scan.yaml: the message names the folder
        raise BackendError(str(error)) from error
    except Exception as error:  # an unreadable scan is refused with its message
        raise BackendError(f"cannot read {folder}: {error}") from error


def _point_index(loaded, point: Any) -> int:
    """The index of the point to read: the only point of a single run, else ``point``."""
    indexes = [p.index for p in loaded.result.scan.points()]
    if point is None:
        if len(indexes) != 1:
            raise BackendError(
                f"this run has {len(indexes)} points: give point (the index of one of "
                f"{indexes}) to choose the point to read"
            )
        index = indexes[0]
    else:
        if isinstance(point, bool) or not isinstance(point, int):
            raise BackendError(f"point must be a whole number (a point index), got {point!r}")
        if point not in indexes:
            raise BackendError(f"point {point} is not a point of this run (its points: {indexes})")
        index = point
    if index not in loaded.outputs:
        have = loaded.points() or "none"
        raise BackendError(f"point {index} has no outputs (points with outputs: {have})")
    return index


def _open(folder: Path, point: Any) -> _Opened:
    folder = Path(folder)
    loaded = _load(folder)
    index = _point_index(loaded, point)
    try:
        results = loaded.point(index)
    except Exception as error:  # a point whose outputs cannot be read
        raise BackendError(f"cannot read the outputs of point {index}: {error}") from error
    return _Opened(
        results=results,
        point_dir=folder / loaded.outputs[index],
        index=index,
        points=len(loaded.result.scan.points()),
    )


def _data(results, plot: PlotDef) -> pd.DataFrame:
    """The frame behind ``plot``; a missing output is refused with what it needs."""
    reason = missing_reason(plot, results)
    if reason is not None:
        raise BackendError(f"{plot.title} is not available: {reason}")
    try:
        return plot_data(plot, results)
    except Exception as error:  # a parser's message is shown as it is
        raise BackendError(f"cannot read {plot.title}: {error}") from error


def _point_spec(point_dir: Path) -> RunSpec | None:
    path = point_dir / SPEC_FILE
    if not path.is_file():
        return None
    try:
        return RunSpec.from_yaml(path)
    except Exception as error:
        raise BackendError(f"cannot read {path}: {error}") from error


def _plain(value: Any) -> Any:
    """A value as plain JSON: numpy scalars as Python numbers, NaN and infinities as None."""
    if isinstance(value, np.generic):
        value = value.item()
    if isinstance(value, float) and not math.isfinite(value):
        return None
    return value


def _limit(max_rows: Any) -> int:
    if isinstance(max_rows, bool) or not isinstance(max_rows, int) or max_rows < 1:
        raise BackendError(f"max_rows must be a whole number of at least 1, got {max_rows!r}")
    return min(max_rows, MAX_TABLE_ROWS)


def _present(results, name: str) -> bool:
    """Whether the table ``name`` is in ``results``, without parsing a per-ion file.

    A single run's per-ion output (:data:`_PER_ION_FILES`) is looked for on disk, as
    :class:`~srim.output.Results` looks for it, so no file is parsed. A merged run builds its
    tables on first access, so asking for one parses nothing; the other tables are read when
    the results are opened.
    """
    attribute = _TABLE_NAMES[name]
    filename = _PER_ION_FILES.get(name)
    if filename is None or hasattr(results, "batches"):
        return getattr(results, attribute, None) is not None
    return _find_output(results.directory, filename) is not None


# --- the functions the backend calls -----------------------------------------------------------


def summary(folder: Path, point: int | None = None) -> list[dict]:
    """The implant summary (:func:`srim.analysis.implant_summary`) of the point's ions, as one
    row: the mean depth and straggle, the lateral and radial ranges (Å), and ``capped`` (True
    when the per-ion output was read only up to the row cap). Needs RANGE_3D.txt."""
    opened = _open(folder, point)
    frame = _data(opened.results, _BY_KEY["stopping_map"])
    row = {key: _plain(value) for key, value in implant_summary(frame).items()}
    row["capped"] = bool(frame.attrs.get("capped", False))
    return [row]


def available_plots(folder: Path, point: int | None = None) -> list[dict]:
    """Every plot of the catalogue: ``key``, ``title``, ``available`` and, when it is not, the
    ``reason`` (what it needs). Nothing is read beyond the point's small tables."""
    opened = _open(folder, point)
    rows = []
    for plot in CATALOGUE:
        reason = missing_reason(plot, opened.results)
        rows.append(
            {"key": plot.key, "title": plot.title, "available": reason is None, "reason": reason}
        )
    return rows


def table(folder: Path, point: int | None, key: str, max_rows: int = DEFAULT_TABLE_ROWS) -> dict:
    """The data of a plot as ``{"columns", "rows", "total_rows", "capped"}``.

    A depth profile is thinned evenly over its depths to ``max_rows`` rows; a per-ion plot gives
    its first rows. ``max_rows`` is at most :data:`MAX_TABLE_ROWS`. ``total_rows`` is the number
    of rows, or None when the table was capped (by ``max_rows`` or by the plot's row cap).
    """
    limit = _limit(max_rows)
    plot = _plot_of(key)
    opened = _open(folder, point)
    frame = _data(opened.results, plot)
    capped = bool(frame.attrs.get("capped", False)) or len(frame) > limit
    if len(frame) > limit:
        if key in PER_ION:
            frame = frame.iloc[:limit]
        else:
            frame = frame.iloc[np.linspace(0, len(frame) - 1, limit).round().astype(int)]
    rows = [[_plain(value) for value in row] for row in frame.itertuples(index=False, name=None)]
    return {
        "columns": [str(column) for column in frame.columns],
        "rows": rows,
        "total_rows": None if capped else len(rows),
        "capped": capped,
    }


def render_plot(folder: Path, point: int | None, key: str, out_dir: Path) -> Path:
    """Draw the plot ``key`` and save it as ``out_dir/<key>.png`` (100 dpi, 8 x 5 in), returning
    the path. A scan with several points names the file ``<key>-point<index>.png``, so the
    points do not replace each other. Needs matplotlib (``pysrim2[plot]``)."""
    plot = _plot_of(key)
    try:
        from matplotlib.backends.backend_agg import FigureCanvasAgg
        from matplotlib.figure import Figure
    except ImportError as error:
        raise BackendError("plots need matplotlib: install pysrim2[plot]") from error
    opened = _open(folder, point)
    frame = _data(opened.results, plot)
    figure = Figure(figsize=FIGSIZE, dpi=DPI)
    FigureCanvasAgg(figure)  # the Agg canvas: no window, no display
    try:
        plot.draw(frame, figure.add_subplot())
    except Exception as error:  # a plot that cannot be drawn is refused with its message
        raise BackendError(f"cannot draw {plot.title}: {error}") from error
    stem = key if opened.points == 1 else f"{key}-point{opened.index}"
    path = Path(out_dir) / f"{stem}.png"
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        figure.savefig(path, dpi=DPI)
    except OSError as error:
        raise BackendError(f"cannot write {path}: {error}") from error
    return path


def export(
    folder: Path,
    fmt: str,
    dest: Path,
    include_collision: bool = False,
    point: int | None = None,
) -> dict:
    """Export the point's tables through :mod:`srim.export`: ``csv`` and ``parquet`` write one file
    per table into the folder ``dest`` (csv adds ``metadata.yaml``), ``hdf5`` one file at ``dest``.
    The collision events are written only when ``include_collision`` is set (in chunks).
    Returns ``{"written": [paths]}``."""
    if fmt not in FORMATS:
        raise BackendError(f"unknown format {fmt!r} (formats: {', '.join(FORMATS)})")
    opened = _open(folder, point)
    results = opened.results
    include = None
    if include_collision:
        include = [name for name in _TABLE_NAMES if _present(results, name)]
        if "collision" not in include:
            raise BackendError("this run has no collision events (COLLISON.txt) to export")
    spec = _point_spec(opened.point_dir)
    dest = Path(dest)
    try:
        if fmt == "hdf5":
            written = [results.to_hdf5(dest, spec=spec, include=include)]
        else:
            method = results.to_csv if fmt == "csv" else results.to_parquet
            written = list(method(dest, spec=spec, include=include).values())
            if fmt == "csv":
                written.append(str(dest / "metadata.yaml"))
    except ImportError as error:  # the message names the extra to install
        raise BackendError(str(error)) from error
    except (ValueError, OSError) as error:
        raise BackendError(f"cannot export to {dest}: {error}") from error
    return {"written": [str(path) for path in written]}
