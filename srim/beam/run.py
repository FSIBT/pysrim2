"""Re-open a finished scan point: its batches merged into one result.

A batched point of a scan (a binned beam, or a fixed count split with ``batch_ions``, or a
precision target) keeps its folder ``points/<id>/``: ``run.yaml`` (the point's spec with the
total ions and the point seed), ``units.csv`` (one row per finished unit: ``unit``,
``beam_batch``, ``bin``, ``n_ions``, ``seed``; ``bin`` is empty for a plain batch) and, for a
binned beam, ``beam_bins.csv`` (the bins and the ions in each). Each unit's outputs are in
``batches/<unit>/``. :func:`open_results` reads those files back.

:func:`run_beam` runs a distributed beam: one TRIM run when it is an exact beam of up to
99 999 ions, else a one-point scan (binned bins, or exact units of 99 999 ions) whose point folder
:func:`open_results` merges. :func:`sampled_ions` reads the ions an exact run ran back from
their TRIM.DAT files.
"""

import shutil
import tempfile
from pathlib import Path

import pandas as pd

from ..output import Results
from ..scan.batches import (
    BATCHES_DIR,
    BINS_FILE,
    RUN_FILE,
    UNITS_FILE,
    BatchInfo,
    MergedResults,
)
from ..spec import RunSpec, SpecError
from .exact import read_trim_dat
from .spec import MAX_TRIM_DAT_IONS

#: statuses of a scan row whose point has results (the others are errors)
_RESULT_STATUSES = ("done", "cached", "max_ions")


def open_results(point_dir, cleanup=None) -> MergedResults:
    """The results of a finished batched scan point, merged as one result.

    Parameters
    ----------
    point_dir : path-like
        a point's folder, ``<scan folder>/points/<id>``
    cleanup : path-like, optional
        a folder that :meth:`close` deletes with the results (the temporary scan folder that
        :func:`run_beam` makes); nothing else is deleted. Default: none.

    Returns
    -------
    :class:`~srim.scan.MergedResults`
        with the surface of :class:`srim.output.Results` (the per-ion tables have the
        ``batch`` column, and the bin columns ``beam_batch``, ``bin``, ``bin_energy_keV``,
        ``bin_angle_deg`` of a binned beam); ``spec`` is the point's spec and ``beam_bins``
        the bins table of a binned beam (None otherwise). Use it as a context manager or call
        ``close()``.

    Raises
    ------
    FileNotFoundError
        when the folder has no ``run.yaml`` or ``units.csv`` (not a finished batched point)
    SpecError
        when ``run.yaml`` is not a valid run file
    """
    folder = Path(point_dir)
    for name in (RUN_FILE, UNITS_FILE):
        if not (folder / name).is_file():
            raise FileNotFoundError(f"{folder} has no {name}: not a finished batched point")
    spec = RunSpec.from_yaml(folder / RUN_FILE)
    units = pd.read_csv(folder / UNITS_FILE)
    bins = None
    if (folder / BINS_FILE).is_file():
        bins = pd.read_csv(folder / BINS_FILE)
    parts, infos = [], []
    try:
        for row in units.itertuples(index=False):
            directory = folder / BATCHES_DIR / str(int(row.unit))
            columns = _bin_columns(row, bins)
            parts.append(Results(str(directory)))
            infos.append(
                BatchInfo(
                    int(row.unit),
                    int(row.seed),
                    int(row.n_ions),
                    directory=str(directory),
                    columns=columns,
                )
            )
    except BaseException:
        for part in parts:
            part.close()
        raise
    return MergedResults(
        parts,
        infos,
        spec=spec.to_dict(),
        base_seed=spec.seed,
        beam_bins=bins,
        tmpdir=cleanup,
    )


def _bin_columns(row, bins):
    """The per-ion columns of a binned unit (None for a plain batch)."""
    if bins is None:
        return None
    if pd.isna(row.bin):
        raise SpecError(f"unit {row.unit} of a binned point has no bin")
    k = int(row.bin)
    match = bins.loc[bins["bin"] == k]
    if match.empty:
        raise SpecError(f"bin {k} of unit {row.unit} is not in {BINS_FILE}")
    return {
        "beam_batch": int(row.beam_batch),
        "bin": k,
        "bin_energy_keV": float(match["energy_keV"].iloc[0]),
        "bin_angle_deg": float(match["angle_deg"].iloc[0]),
    }


# --- running a beam ------------------------------------------------------------------------


def _mode(spec: RunSpec) -> str:
    return "exact" if spec.beam is None else spec.beam.mode


def _splits(spec: RunSpec, precision) -> bool:
    """True when an exact run of ``spec`` is more than one TRIM.DAT file (a scan splits it)."""
    return precision is None and _mode(spec) == "exact" and spec.number_ions > MAX_TRIM_DAT_IONS


def result_folder(spec: RunSpec, output_dir) -> Path:
    """Where :func:`run_beam` (or ``pysrim2 run``) leaves the results of ``spec`` in ``output_dir``.

    ``output_dir`` itself for one TRIM run (an exact beam of up to 99 999 ions), else the point
    folder ``output_dir/points/<id[:16]>`` of the scan that runs it (its ``run.yaml`` and
    ``units.csv``, read back by :func:`open_results`).
    """
    from ..scan.spec import spec_hash  # lazy: srim.scan imports srim.beam lazily

    folder = Path(output_dir)
    if _mode(spec) == "exact" and spec.number_ions <= MAX_TRIM_DAT_IONS:
        return folder
    return folder / "points" / spec_hash(spec)[:16]


def run_beam(
    spec,
    *,
    output_dir=None,
    precision=None,
    extract=None,
    workers=None,
    cache=None,
    runner=None,
    on_event=None,
):
    """Run a run spec, distributed beam or not, and return its results.

    An exact spec with up to 99 999 ions (and no ``precision``) is one TRIM run:
    ``TRIM.from_spec(spec).run(output_dir=output_dir)``, or ``runner(spec)`` when a runner is
    given (``output_dir`` is then not written). Otherwise the spec is a one-point scan
    (:func:`srim.scan.run_scan`): an exact beam of more ions is split into units of 99 999
    ions, a binned beam runs one SRIM run per bin, and a ``precision`` target runs batches
    until it is met.

    Parameters
    ----------
    spec : :class:`srim.spec.RunSpec`
        the run; validated first (:meth:`RunSpec.validate`)
    output_dir : path-like, optional
        where the results go. Default: a temporary folder that the returned results delete
        on ``close()``.
    precision : :class:`srim.scan.Precision`, optional
        run batches until a precision goal is reached instead of ``spec.number_ions`` ions
    extract : list, optional
        extractors of the scan (see :class:`srim.scan.Scan`); default none (the results are
        read with :func:`open_results`)
    workers, cache, on_event :
        as for :func:`srim.scan.run_scan`
    runner : callable, optional
        ``runner(spec) -> Results`` in place of SRIM (tests and custom back ends; see
        :func:`srim.scan.run_scan`)

    Returns
    -------
    :class:`srim.output.Results` or :class:`srim.scan.MergedResults`
        ``Results`` for one TRIM run, ``MergedResults`` for a scan point. Use ``close()`` or
        ``with``: for a temporary folder that deletes it.

    Raises
    ------
    SpecError
        for an invalid spec (naming the dotted key)
    RuntimeError
        when a run of the scan failed or was stopped (the message has the row's error)
    """
    from ..scan import Scan, run_scan  # lazy: srim.scan imports srim.beam lazily
    from ..srim import TRIM  # lazy: srim.srim imports srim.beam

    spec.validate()
    if precision is None and _mode(spec) == "exact" and spec.number_ions <= MAX_TRIM_DAT_IONS:
        if runner is not None:
            return runner(spec)
        return TRIM.from_spec(spec).run(output_dir=output_dir)

    scan = Scan(
        spec,
        ions=precision if precision is not None else spec.number_ions,
        batch_ions=MAX_TRIM_DAT_IONS if _splits(spec, precision) else None,
        seed=spec.seed,
        extract=extract if extract is not None else [],
    )
    temporary = output_dir is None
    folder = Path(tempfile.mkdtemp(prefix="pysrim2-beam-")) if temporary else Path(output_dir)
    try:
        result = run_scan(
            scan,
            workers=workers,
            scan_dir=folder,
            on_event=on_event,
            runner=runner,
            cache=cache,
        )
        row = result.table.iloc[0]
        if row["status"] not in _RESULT_STATUSES:
            error = row["error"]
            detail = error if isinstance(error, str) else "no error was recorded"
            raise RuntimeError(f"the beam run {row['status']}: {detail}")
        return open_results(folder / row["path"], cleanup=folder if temporary else None)
    except BaseException:
        if temporary:
            shutil.rmtree(folder, ignore_errors=True)
        raise


# --- the sampled beam -------------------------------------------------------------------------


_NOT_EXACT = "not an exact-mode run; binned runs record their bins in results.beam_bins"


def _trim_dat_ions(directory) -> pd.DataFrame:
    path = Path(directory or "") / "TRIM.DAT"
    if directory is None or not path.is_file():
        raise ValueError(f"{directory}: no TRIM.DAT, so {_NOT_EXACT}")
    return read_trim_dat(path)


def sampled_ions(results) -> pd.DataFrame:
    """The ions an exact run ran, one row each, read from its TRIM.DAT file(s).

    The columns of :func:`srim.beam.exact.sample_ions` (``ion``, the parameters in SRIM's
    units and the direction cosines). For results of several exact units (a split run,
    :class:`srim.scan.MergedResults`) the units are concatenated: ``ion`` is the global ion
    number and ``batch`` the unit's index.

    Raises
    ------
    ValueError
        for binned results (they have ``beam_bins``) or any run without a TRIM.DAT file
    """
    if getattr(results, "beam_bins", None) is not None:
        raise ValueError(_NOT_EXACT)
    if not isinstance(results, MergedResults):
        return _trim_dat_ions(getattr(results, "directory", None))
    frames = []
    offset = 0
    for info in results.batches:
        frame = _trim_dat_ions(info.directory)
        frame["ion"] = frame["ion"] + offset
        frame.insert(1, "batch", info.index)
        offset += len(frame)
        frames.append(frame)
    return pd.concat(frames, ignore_index=True)
