"""Run folders for notebooks: read a run or scan folder back, finished or not (Qt-free).

A notebook opens a folder with :func:`load_run`. A finished folder (``result/scan.csv`` is there)
is read as it was written. An unfinished one (a run that stopped or crashed) gives the points
whose folders have outputs. :meth:`LoadedRun.point` reads a point's results, a plain
:class:`~srim.output.Results` or the merged results of a batched point.

:meth:`LoadedRun.mark_loaded` records in the folder (``.pysrim2-notebook/loaded.json``) how many
points the notebook read, so a later reader can tell whether the folder has changed since.
"""

import json
import os
import tempfile
from dataclasses import dataclass
from pathlib import Path

from srim.beam import open_results
from srim.output import Results
from srim.scan import Scan
from srim.scan.result import ScanResult, read_scan_result

#: the folder inside a run folder where notebooks keep their own files
NOTEBOOK_DIR = ".pysrim2-notebook"
#: the name of the file that asks for a reload (only the name is defined here)
RELOAD_FILE = "reload"
#: the file in :data:`NOTEBOOK_DIR` that :meth:`LoadedRun.mark_loaded` writes
LOADED_FILE = "loaded.json"


def open_point(folder: Path, rel_path: str):
    """The results of a point of a scan folder: merged when the point was batched (it has a
    ``units.csv``), else the plain :class:`~srim.output.Results` of its folder."""
    point = Path(folder) / rel_path
    if (point / "units.csv").is_file():
        return open_results(point)
    return Results(point)


def has_outputs(point_dir: Path) -> bool:
    """A point's folder holds its run.yaml (or units.csv, when it was batched)."""
    return (point_dir / "run.yaml").is_file() or (point_dir / "units.csv").is_file()


def read_run_folder(folder) -> tuple[ScanResult, bool]:
    """The scan result of a run folder, and whether the run finished.

    A finished run (``result/scan.csv`` is there) is read as it was written. Otherwise the table
    is built from the scan's points: ``done`` for a point whose folder has outputs, else
    ``missing``. Raises :class:`FileNotFoundError` for a folder without ``scan.yaml``.
    """
    folder = Path(folder)
    if (folder / "result" / "scan.csv").is_file():
        return read_scan_result(folder), True
    scan = Scan.from_yaml(folder / "scan.yaml")
    rows = []
    for point in scan.points():
        rel = f"points/{point.id[:16]}"
        if has_outputs(folder / rel):
            rows.append({"index": point.index, "status": "done", "path": rel})
        else:
            rows.append({"index": point.index, "status": "missing"})
    return ScanResult.from_rows(scan, rows, base_dir=folder), False


def results_count(folder) -> int:
    """The number of points of a run or scan folder whose folder has outputs.

    Counts the point folders under ``points/`` (no ``scan.yaml`` is read). 0 for a folder that
    is missing or cannot be listed; never raises :class:`OSError`.
    """
    try:
        with os.scandir(Path(folder) / "points") as entries:
            return sum(1 for entry in entries if has_outputs(Path(entry.path)))
    except OSError:
        return 0


def _points_with_outputs(folder: Path, result: ScanResult) -> dict[int, str]:
    """The index of each point of ``result`` whose folder has outputs, with its relative path."""
    ids = {point.id: point.index for point in result.scan.points()}
    found = {}
    for _, row in result.table.iterrows():
        index = ids.get(row.get("id"))
        path = row.get("path")
        if index is None or not isinstance(path, str):
            continue
        if has_outputs(folder / path):
            found[index] = path
    return found


@dataclass
class LoadedRun:
    """A run or scan folder read for a notebook.

    ``folder`` is the folder, ``result`` its :class:`~srim.scan.ScanResult`, ``finished``
    whether the run finished, and ``count`` the number of points with outputs.
    """

    folder: Path
    result: ScanResult
    finished: bool
    count: int

    def points(self) -> list[int]:
        """The indexes of the points that have outputs, in order."""
        return sorted(_points_with_outputs(self.folder, self.result))

    def point(self, index: int) -> Results:
        """The results of point ``index``. Raises :class:`KeyError` for a point without outputs."""
        found = _points_with_outputs(self.folder, self.result)
        if index not in found:
            raise KeyError(f"point {index} has no outputs in {self.folder}")
        return open_point(self.folder, found[index])

    def mark_loaded(self) -> None:
        """Write ``.pysrim2-notebook/loaded.json`` (the count and whether the run finished).

        The file is written to a temporary file in the same folder and then moved into place, so
        a reader never sees a part-written file.
        """
        notebook = self.folder / NOTEBOOK_DIR
        notebook.mkdir(parents=True, exist_ok=True)
        text = json.dumps({"points": self.count, "finished": self.finished})
        fd, tmp = tempfile.mkstemp(dir=notebook, prefix=LOADED_FILE, suffix=".tmp")
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as handle:
                handle.write(text)
            os.replace(tmp, notebook / LOADED_FILE)
        except BaseException:
            Path(tmp).unlink(missing_ok=True)
            raise


def load_run(folder) -> LoadedRun:
    """Read a run or scan folder for a notebook, finished or not.

    Raises :class:`FileNotFoundError` naming the folder when it has no ``scan.yaml``.
    """
    folder = Path(folder)
    if not (folder / "scan.yaml").is_file():
        raise FileNotFoundError(f"{folder} is not a run folder: it has no scan.yaml")
    result, finished = read_run_folder(folder)
    count = len(_points_with_outputs(folder, result))
    return LoadedRun(folder=folder, result=result, finished=finished, count=count)


def loaded_count(folder) -> int | None:
    """The ``points`` of ``.pysrim2-notebook/loaded.json``, or None when the file is absent or
    cannot be read as a count."""
    try:
        data = json.loads((Path(folder) / NOTEBOOK_DIR / LOADED_FILE).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    value = data.get("points") if isinstance(data, dict) else None
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        return None
    return value
