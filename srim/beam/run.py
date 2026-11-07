"""Re-open a finished scan point: its batches merged into one result.

A batched point of a scan (a binned beam, or a fixed count split with ``batch_ions``, or a
precision target) keeps its folder ``points/<id>/``: ``run.yaml`` (the point's spec with the
total ions and the point seed), ``units.csv`` (one row per finished unit: ``unit``,
``beam_batch``, ``bin``, ``n_ions``, ``seed``; ``bin`` is empty for a plain batch) and, for a
binned beam, ``beam_bins.csv`` (the bins and the ions in each). Each unit's outputs are in
``batches/<unit>/``. :func:`open_results` reads those files back.
"""

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


def open_results(point_dir) -> MergedResults:
    """The results of a finished batched scan point, merged as one result.

    Parameters
    ----------
    point_dir : path-like
        a point's folder, ``<scan folder>/points/<id>``

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
