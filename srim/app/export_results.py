"""Saving a run's results in the library's formats (Qt-free).

The app only chooses the names, the folder and the points; every table is written by the library
(:mod:`srim.export` for a point's outputs, :class:`~srim.scan.ScanResult` for a scan's table).
A run folder is a scan folder (``scan.yaml``, ``result/scan.csv``, ``points/<id>/``). A plain run
is a scan of one point that keeps its seed (``keep_seed``): it is exported as the point's tables
under the name itself. Any other scan exports its table and the chosen points, each under
``<name>-<id[:8]>``.

Names, for a name ``N`` (a scan point ``P`` is ``N-<id[:8]>``):

- ``N.parquet/`` (a folder of Parquet tables; a scan: ``N-scan.parquet``, one file)
- ``N.h5`` (one HDF5 file; a scan: ``N.h5`` holds the table under the key ``scan``)
- ``N-csv/`` (a folder of CSV tables with ``metadata.yaml``; a scan: ``N-scan-csv/``)
- ``N-raw/`` (the SRIM output files of the point, copied unchanged: ``*.txt``, ``TRIM.IN``,
  ``TRIM.DAT`` and ``run.yaml``; a batched point's batches are copied in the same layout)
- ``N-run.yaml`` (the run's ``scan.yaml``, always)

Nothing in the run folder is changed. :func:`export_run` checks every target first and raises
:class:`FileExistsError` before it writes anything. The collision table is only written when asked
for, and then in chunks (the library does that).
"""

import dataclasses
import importlib.util
import os
import shutil
import uuid
from dataclasses import dataclass
from pathlib import Path

from srim import export
from srim.app.runs import SHOWN_STATUSES, open_point
from srim.scan.result import read_scan_result
from srim.spec import RunSpec

#: the formats, in the order the dialog lists them
FORMATS = ("parquet", "hdf5", "csv", "raw")
#: the Python module each format needs (formats not listed need nothing extra)
_EXTRAS = {"parquet": ("pyarrow", "parquet"), "hdf5": ("tables", "hdf5")}
#: the SRIM output files of a point that the raw format copies
RAW_PATTERNS = ("*.txt", "TRIM.IN", "TRIM.DAT", "run.yaml")
#: the number of characters of a point id in its name
ID_LENGTH = 8


def format_available(fmt: str) -> str | None:
    """None when ``fmt`` can be written here, else the reason it cannot (the extra to install).

    Checks importability with :func:`importlib.util.find_spec`; pyarrow and tables are never
    imported here. Raises :class:`ValueError` for a format that is not in :data:`FORMATS`.
    """
    if fmt not in FORMATS:
        raise ValueError(f"unknown format {fmt!r}; choose from {', '.join(FORMATS)}")
    if fmt not in _EXTRAS:
        return None
    module, extra = _EXTRAS[fmt]
    if importlib.util.find_spec(module) is None:
        return f"needs the {extra} extra ({module})"
    return None


@dataclass(frozen=True)
class ExportRequest:
    """What to export: the folder, the name, the formats, and the options.

    ``points`` are the scan point indices to export (None: all of them); a plain run ignores
    them. Raises :class:`ValueError` for a name that is not a plain file name, an unknown or
    empty format list, or a bad point index.
    """

    folder: Path
    name: str
    formats: tuple[str, ...]
    collision: bool = False
    points: tuple[int, ...] | None = None

    def __post_init__(self):
        object.__setattr__(self, "folder", Path(self.folder))
        object.__setattr__(self, "formats", tuple(self.formats))
        if not isinstance(self.name, str) or not self.name.strip():
            raise ValueError("the name cannot be empty")
        if self.name in (".", "..") or any(c in self.name for c in ("/", "\\", os.sep, "\0")):
            raise ValueError(f"the name {self.name!r} must be a file name, not a path")
        if not self.formats:
            raise ValueError("choose at least one format")
        unknown = [f for f in self.formats if f not in FORMATS]
        if unknown:
            raise ValueError(f"unknown format(s) {unknown}; choose from {', '.join(FORMATS)}")
        if len(set(self.formats)) != len(self.formats):
            raise ValueError("a format is listed twice")
        if self.points is not None:
            points = tuple(self.points)
            if any(isinstance(i, bool) or not isinstance(i, int) or i < 0 for i in points):
                raise ValueError("points must be point indices (0 or more)")
            object.__setattr__(self, "points", points)


@dataclass(frozen=True)
class PointChoice:
    """A point of a scan as the dialog lists it: its index, its short id, a label, its status,
    and whether it can be exported (``reason`` says why not)."""

    index: int
    short_id: str
    label: str
    status: str
    exportable: bool
    reason: str


@dataclass(frozen=True)
class RunContents:
    """A finished run folder: whether it is a plain run, and its points (in table order)."""

    name: str
    single: bool
    points: tuple[PointChoice, ...]


def _has_outputs(point_dir: Path) -> bool:
    return (point_dir / "run.yaml").is_file() or (point_dir / "units.csv").is_file()


def _rows(folder: Path, result):
    """One (point, row dict, exportable, reason) per table row, in table order."""
    by_id = {point.id: point for point in result.scan.points()}
    out = []
    for row in result.table.to_dict("records"):
        point = by_id.get(row["id"])
        if point is None:
            raise ValueError(f"the table of {folder} has a point that scan.yaml does not")
        status = row.get("status")
        path = row.get("path")
        if status not in SHOWN_STATUSES:
            reason = f"not finished ({status})"
        elif not isinstance(path, str) or not _has_outputs(folder / path):
            reason = "no outputs"
        else:
            reason = ""
        out.append((point, row, reason == "", reason))
    return out


def _is_single(result) -> bool:
    return len(result.scan.points()) == 1 and bool(result.scan.keep_seed)


def read_contents(run_folder) -> RunContents:
    """The points of the finished run folder, for the dialog (reads ``scan.yaml`` and the table).

    Raises :class:`FileNotFoundError` for a folder that is not a finished run.
    """
    folder = Path(run_folder)
    result = read_scan_result(folder)
    points = []
    for point, row, exportable, reason in _rows(folder, result):
        params = ", ".join(f"{key}={value}" for key, value in point.params.items())
        points.append(
            PointChoice(
                index=point.index,
                short_id=point.id[:ID_LENGTH],
                label=params or "the run",
                status=str(row.get("status")),
                exportable=exportable,
                reason=reason,
            )
        )
    return RunContents(folder.name, _is_single(result), tuple(points))


@dataclass(frozen=True)
class _Job:
    """One point to export: its base name for the files, and its folder in the run folder."""

    base: str
    rel: str
    point_index: int


@dataclass(frozen=True)
class _Plan:
    folder: Path
    result: object
    scan_table: bool
    jobs: tuple[_Job, ...]
    skipped: tuple[str, ...]


def _plan(run_folder: Path, request: ExportRequest) -> _Plan:
    result = read_scan_result(run_folder)
    rows = _rows(run_folder, result)
    if _is_single(result):
        point, row, exportable, reason = rows[0]
        if not exportable:
            raise ValueError(f"the run has nothing to export: it is {reason}")
        job = _Job(request.name, str(row["path"]), point.index)
        return _Plan(run_folder, result, False, (job,), ())
    if request.points is not None:
        known = {point.index for point, *_ in rows}
        missing = [i for i in request.points if i not in known]
        if missing:
            raise ValueError(f"no point {missing} in this run")
        rows = [r for r in rows if r[0].index in request.points]
    jobs, skipped = [], []
    for point, row, exportable, _reason in rows:
        short = point.id[:ID_LENGTH]
        if exportable:
            jobs.append(_Job(f"{request.name}-{short}", str(row["path"]), point.index))
        else:
            skipped.append(short)
    return _Plan(run_folder, result, True, tuple(jobs), tuple(skipped))


def _writes(request: ExportRequest, plan: _Plan) -> list[tuple[str, Path]]:
    """The (kind, path) of everything the export writes, in the order it writes them."""
    folder, name = request.folder, request.name
    writes = []
    if plan.scan_table:
        for fmt in request.formats:
            if fmt == "parquet":
                writes.append((fmt, folder / f"{name}-scan.parquet"))
            elif fmt == "hdf5":
                writes.append((fmt, folder / f"{name}.h5"))
            elif fmt == "csv":
                writes.append((fmt, folder / f"{name}-scan-csv"))
    for job in plan.jobs:
        for fmt in request.formats:
            suffix = {"parquet": ".parquet", "hdf5": ".h5", "csv": "-csv", "raw": "-raw"}[fmt]
            writes.append((fmt, folder / f"{job.base}{suffix}"))
    writes.append(("run", folder / f"{name}-run.yaml"))
    return writes


def validate(run_folder, request: ExportRequest) -> _Plan:
    """Check ``request`` against the run folder, without writing or removing anything.

    Refuses a folder inside the run folder (ValueError), a format whose extra is missing
    (ImportError), and a run with nothing to export. Returns the plan that :func:`export_run`
    writes. Every caller that is about to remove or write goes through here first.
    """
    run_folder = Path(run_folder)
    if _is_inside(request.folder, run_folder):
        raise ValueError("choose a folder outside the run folder")
    for fmt in request.formats:
        reason = format_available(fmt)
        if reason is not None:
            raise ImportError(f"{fmt} export {reason}")
    return _plan(run_folder, request)


def targets(run_folder, request: ExportRequest) -> list[Path]:
    """The paths :func:`export_run` would write for ``request`` (none is checked or written)."""
    plan = validate(run_folder, request)
    return [path for _kind, path in _writes(request, plan)]


def skipped_points(run_folder, request: ExportRequest) -> list[str]:
    """The short ids of the chosen points that are not exported (not finished, or no outputs)."""
    return list(_plan(Path(run_folder), request).skipped)


def remove_targets(paths) -> None:
    """Remove the given paths (files and folders). The caller passes only what it may replace."""
    for path in paths:
        path = Path(path)
        if path.is_dir() and not path.is_symlink():
            shutil.rmtree(path)
        elif os.path.lexists(path):
            path.unlink()


def _copy_raw(point_dir: Path, dest: Path) -> list[Path]:
    """Copy the SRIM outputs of a point (and of its batches) to ``dest``, byte for byte."""
    sources = []
    for pattern in RAW_PATTERNS:
        sources += [(p, Path(p.name)) for p in point_dir.glob(pattern) if p.is_file()]
    batches = point_dir / "batches"
    if batches.is_dir():
        for batch in sorted(batches.iterdir()):
            if batch.is_dir():
                for pattern in RAW_PATTERNS:
                    sources += [
                        (p, p.relative_to(point_dir)) for p in batch.glob(pattern) if p.is_file()
                    ]
    dest.mkdir(parents=True, exist_ok=True)
    copied = []
    for source, rel in sorted(sources, key=lambda item: item[1].as_posix()):
        target = dest / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, target)
        copied.append(target)
    return copied


def _point_spec(point_dir: Path):
    run_file = point_dir / "run.yaml"
    return RunSpec.from_yaml(run_file) if run_file.is_file() else None


def _write_point(run_folder: Path, job: _Job, request: ExportRequest) -> list[Path]:
    point_dir = run_folder / job.rel
    out = request.folder
    include = ["collision"] if request.collision else None
    spec = _point_spec(point_dir)
    written = []
    results = open_point(run_folder, job.rel)
    try:
        if "parquet" in request.formats:
            path = out / f"{job.base}.parquet"
            export.results_to_parquet(results, path, spec=spec, include=include)
            written.append(path)
        if "hdf5" in request.formats:
            path = out / f"{job.base}.h5"
            export.results_to_hdf5(results, path, spec=spec, include=include)
            written.append(path)
        if "csv" in request.formats:
            path = out / f"{job.base}-csv"
            export.results_to_csv(results, path, spec=spec, include=include)
            written.append(path)
    finally:
        close = getattr(results, "close", None)
        if close is not None:
            close()
    if "raw" in request.formats:
        written.append(out / f"{job.base}-raw")
        _copy_raw(point_dir, out / f"{job.base}-raw")
    return written


def export_run(run_folder, request: ExportRequest, replace: bool = False) -> list[Path]:
    """Write the export of ``request`` for the finished run folder; returns the paths written.

    Everything is first written to a hidden staging folder in the target folder. Only when every
    write has succeeded are the old targets removed (with ``replace``) and the new files moved into
    place; any exception removes the staging folder and is raised again, so a failed export leaves
    the folder as it was.

    Raises :class:`FileExistsError` (naming the first path) before anything is written when a
    target exists and ``replace`` is not set. :func:`validate` errors (a folder inside the run
    folder, a missing extra, nothing to export) are raised before anything is removed or written.
    """
    run_folder = Path(run_folder)
    plan = validate(run_folder, request)
    existing = [p for _kind, p in _writes(request, plan) if os.path.lexists(p)]
    if existing and not replace:
        raise FileExistsError(f"{existing[0]} already exists; choose another name or remove it")
    request.folder.mkdir(parents=True, exist_ok=True)
    staging = request.folder / f".{request.name}.staging-{uuid.uuid4().hex[:8]}"
    staging.mkdir()
    try:
        staged = _write_all(run_folder, plan, dataclasses.replace(request, folder=staging))
        remove_targets(existing)
        moved = []
        for item in staged:
            target = request.folder / item.name
            shutil.move(os.fspath(item), os.fspath(target))
            moved.append(target)
    finally:
        shutil.rmtree(staging, ignore_errors=True)
    return moved


def _write_all(run_folder: Path, plan: _Plan, request: ExportRequest) -> list[Path]:
    """Every file and folder of the export, written into ``request.folder`` (the staging folder)."""
    written = []
    if plan.scan_table:
        written += _write_scan_table(plan, request)
    for job in plan.jobs:
        written += _write_point(run_folder, job, request)
    target = request.folder / f"{request.name}-run.yaml"
    shutil.copy2(run_folder / "scan.yaml", target)
    written.append(target)
    return written


def _write_scan_table(plan: _Plan, request: ExportRequest) -> list[Path]:
    folder, name, table = request.folder, request.name, plan.result
    written = []
    if "parquet" in request.formats:
        written.append(Path(table.to_parquet(folder / f"{name}-scan.parquet")))
    if "hdf5" in request.formats:
        written.append(Path(table.to_hdf5(folder / f"{name}.h5", key="scan")))
    if "csv" in request.formats:
        table.to_csv(folder / f"{name}-scan-csv")
        written.append(folder / f"{name}-scan-csv")
    return written


def _is_inside(path: Path, folder: Path) -> bool:
    path, folder = Path(path).resolve(), Path(folder).resolve()
    return path == folder or folder in path.parents
