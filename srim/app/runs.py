"""Planning a run of the app's setup, the parallel advice for it, and the run folder (Qt-free).

:func:`plan_run` gives the library's :class:`~srim.scan.Scan` of a setup: the scan of its
``_scan`` key, or the one-point scan of a plain run (:func:`srim.beam.beam_scan`, so a plain
run keeps its seed and its ``TRIM.IN`` is the one of the saved ``run.yaml``). The workers and the
back end are not part of the scan; :class:`ParallelChoice` only carries them to the executor.

:func:`parallel_advice` says when a run is long enough to be worth running in parallel, from the
ions per second remembered for the setup's calculation (the default rates otherwise). It is
advice only: nothing here changes what runs.
"""

import math
import warnings
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

from srim.app.model import to_spec
from srim.app.scan_model import to_scan
from srim.app.settings import RATE_KEYS, AppSettings
from srim.beam import beam_scan, open_results
from srim.beam.binned import make_bins
from srim.output import Results
from srim.scan import Precision, Scan
from srim.scan.spec import is_binned
from srim.spec import SpecError

#: the ions per second of the calculations when none was measured: a quick calculation (1)
#: runs fast, the others are full cascades
DEFAULT_IONS_PER_S = {1: 2000.0, 2: 100.0}
#: the rate of a calculation without one of its own in :data:`DEFAULT_IONS_PER_S`
_FALLBACK_RATE = 100.0
#: a run shorter than this (seconds) gets no parallel advice
LONG_RUN_SECONDS = 120
#: a run at least this long (seconds) is offered a container when there is a runtime
CONTAINER_SECONDS = 3600
#: a single point with more ions than this can be split into batches
_SPLIT_IONS = 1000


@dataclass(frozen=True)
class ParallelChoice:
    """How a run is run: worker processes, the ions per batch, and the container back end."""

    workers: int = 1
    batch_ions: int | None = None
    container: bool = False


@dataclass(frozen=True)
class Advice:
    """What the app suggests for a run that is long enough to be worth running in parallel.

    ``seconds`` is the estimated run time, ``units`` the number of SRIM runs (points, and the
    bins of a binned beam), ``can_split`` whether one point could be split into batches,
    ``offer_container`` whether a container runtime is worth offering, and ``default_workers``
    the worker count that leaves one core free.
    """

    seconds: float
    units: int
    can_split: bool
    offer_container: bool
    default_workers: int


def run_folder(setup_path: Path, now: datetime) -> Path:
    """The folder a run of the setup at ``setup_path`` writes to, named by ``now`` (UTC).

    ``<setup folder>/<setup stem>-runs/<%Y%m%dT%H%M%SZ>``; when that name is taken, ``-2``,
    ``-3``… is added. The folder is not created.
    """
    setup_path = Path(setup_path)
    base = setup_path.parent / f"{setup_path.stem}-runs"
    stamp = now.strftime("%Y%m%dT%H%M%SZ")
    folder = base / stamp
    number = 2
    while folder.exists():
        folder = base / f"{stamp}-{number}"
        number += 1
    return folder


def plan_run(doc: dict, base_dir: Any, choice: ParallelChoice) -> Scan:
    """The library's scan of the setup ``doc``: its ``_scan`` when it has one, else a plain run.

    The batch size of ``choice`` applies only where the scan has fixed ions and no batch size or
    precision target of its own, and is not a one-point scan that keeps its seed (the library
    refuses to split that). Raises :class:`~srim.spec.SpecError` for a value the library refuses.
    """
    scan = doc.get("_scan")
    if scan is None:
        return beam_scan(to_spec(doc, base_dir), batch_ions=choice.batch_ions)
    if (
        choice.batch_ions is not None
        and scan.get("precision") is None
        and scan.get("batch_ions") is None
        and not scan.get("keep_seed", False)
    ):
        doc = {**doc, "_scan": {**scan, "batch_ions": choice.batch_ions}}
    return to_scan(doc, base_dir)


def parallel_advice(
    scan: Scan, cores: int, ions_per_s: dict[str, float], container_available: bool
) -> Advice | None:
    """The advice for ``scan`` on a machine with ``cores`` cores, or None when none is due.

    No advice on one core, or when the estimated run time (the ions of all points over the
    remembered or default rate of the calculation) is under :data:`LONG_RUN_SECONDS`.
    """
    if cores < 2:
        return None
    points = scan.points()
    precision = isinstance(scan.ions, Precision)
    per_point = scan.ions.max_ions if precision else scan.ions
    seconds = per_point * len(points) / _rate(scan.base.calculation, ions_per_s)
    if seconds < LONG_RUN_SECONDS:
        return None
    units = sum(_units(point.spec) for point in points)
    single = (
        len(points) == 1
        and scan.batch_ions is None
        and not precision
        and not is_binned(points[0].spec)
    )
    return Advice(
        seconds=seconds,
        units=units,
        can_split=single and per_point > _SPLIT_IONS,
        offer_container=container_available and seconds >= CONTAINER_SECONDS,
        default_workers=max(1, cores - 1),
    )


def remember_rate(settings: AppSettings, calculation: int, ions_per_s: float) -> None:
    """Keep ``ions_per_s`` as the rate of ``calculation`` (the latest one measured).

    A value that is not a finite number above zero, or a calculation that is not 1..7, is
    ignored. The caller saves the settings.
    """
    key = str(calculation)
    if key not in RATE_KEYS:
        return
    if isinstance(ions_per_s, bool) or not isinstance(ions_per_s, (int, float)):
        return
    if not (math.isfinite(ions_per_s) and ions_per_s > 0):
        return
    settings.ions_per_s[key] = float(ions_per_s)


def open_point(folder: Path, rel_path: str):
    """The results of a point of a scan folder: merged when the point was batched (it has a
    ``units.csv``), else the plain :class:`~srim.output.Results` of its folder."""
    point = Path(folder) / rel_path
    if (point / "units.csv").is_file():
        return open_results(point)
    return Results(point)


def _rate(calculation: int, ions_per_s: dict[str, float]) -> float:
    remembered = ions_per_s.get(str(calculation))
    if isinstance(remembered, (int, float)) and not isinstance(remembered, bool):
        if math.isfinite(remembered) and remembered > 0:
            return float(remembered)
    return DEFAULT_IONS_PER_S.get(calculation, _FALLBACK_RATE)


def _units(spec) -> int:
    """The SRIM runs of one point: its bins for a binned beam, else one."""
    if not is_binned(spec):
        return 1
    try:
        with warnings.catch_warnings():
            warnings.simplefilter("ignore")  # a long bin list warns; the advice only counts
            return len(make_bins(spec))
    except SpecError:
        return 1
