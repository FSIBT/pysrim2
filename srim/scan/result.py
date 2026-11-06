"""The table of a scan: one row per point, with the parameters, the extracted values and
their errors, and how the point was run; exports (Parquet, CSV, HDF5) and a quick plot.

Columns, in order (the table is a :class:`pandas.DataFrame`):

- one column per scanned parameter (dotted key, e.g. ``ion.energy_keV``)
- ``id``: the point's :func:`~srim.scan.spec_hash`
- one column per extracted value, and ``<value>_err`` next to it (``NaN`` unless an
  estimator filled it in)
- ``n_ions``: ions run for the point (``NaN`` if it did not run)
- ``seeds``: the batch seeds as text, comma-separated (``"11,12,13"``), ``NaN`` if none
- ``status``: ``done``, ``failed``, ``max_ions`` (precision not reached within the limit),
  ``cached``, ``stopped`` (the scan was stopped before the point finished) or ``missing``
  (a collected scan whose point was not run: no outputs yet)
- ``error``: the error message of a failed point, else ``NaN``
- ``path``: where the point's outputs are, relative to the scan folder
  (``base_dir``); ``NaN`` if none

Rows are built with :meth:`ScanResult.from_rows`: each row is a dict with ``index`` (the
point's index in :meth:`Scan.points`) and the values; the parameters are taken from the
scan point, not from the row.
"""

import math
import os
from collections.abc import Iterable
from dataclasses import dataclass
from numbers import Integral
from pathlib import Path

import pandas as pd
import yaml

from .. import export
from ..spec import SpecError
from .spec import Scan

#: the columns of a row that are not values or parameters
_META = ("n_ions", "seeds", "status", "error", "path")
STATUSES = ("done", "failed", "max_ions", "cached", "stopped", "missing")


def _seeds_text(value):
    if value is None or (isinstance(value, float) and math.isnan(value)):
        return None  # becomes NaN in the table
    if isinstance(value, str):
        return value
    if isinstance(value, Iterable):
        return ",".join(str(int(s)) for s in value)
    return str(int(value))


def _relative_path(value, base_dir):
    """``value`` as a POSIX path relative to ``base_dir`` (exports never hold host paths).

    Relative values are kept (they are relative to the scan folder already). An absolute
    value needs ``base_dir`` and must lie under it, else ``ValueError``.
    """
    if value is None or (isinstance(value, float) and math.isnan(value)):
        return None
    text = os.fspath(value)
    if not os.path.isabs(text):
        return Path(text).as_posix()
    if base_dir is None:
        raise ValueError(
            f"path {text!r} is absolute; pass base_dir= to store it relative to the scan folder"
        )
    base = os.path.abspath(os.fspath(base_dir))
    rel = os.path.relpath(os.path.abspath(text), base)
    if rel == os.pardir or rel.startswith(os.pardir + os.sep):
        raise ValueError(f"path {text!r} is not under base_dir {base!r}")
    return Path(rel).as_posix()


@dataclass(eq=False)
class ScanResult:
    """The result of a scan: ``scan`` (the definition) and ``table`` (one row per point)."""

    scan: Scan
    table: pd.DataFrame

    @classmethod
    def from_rows(cls, scan: Scan, rows, base_dir=None) -> "ScanResult":
        """Build the table from one dict per point (see the module docstring).

        ``base_dir`` is the scan folder: an absolute ``path`` is stored relative to it (POSIX
        style), so exports hold no host paths. Raises :class:`SpecError` for a row without
        a valid ``index`` or ``status``, or an ``<x>_err`` column with no ``x``, and
        :class:`ValueError` for an absolute path that is not under ``base_dir``.
        """
        points = scan.points()
        names = scan.param_names
        records = []
        for row in rows:
            index = row.get("index")
            if (
                isinstance(index, bool)
                or not isinstance(index, Integral)
                or not (0 <= index < len(points))
            ):
                raise SpecError(
                    f"a result row needs 'index' of a scan point (0..{len(points) - 1}), "
                    f"got {index!r}"
                )
            status = row.get("status")
            if status not in STATUSES:
                raise SpecError(
                    f"row {index}: status must be one of {', '.join(STATUSES)}, got {status!r}"
                )
            point = points[index]
            rest = {k: v for k, v in row.items() if k not in ("index", "id", *names)}
            rest["seeds"] = _seeds_text(rest.get("seeds"))
            rest["path"] = _relative_path(rest.get("path"), base_dir)
            records.append({**{n: point.params[n] for n in names}, "id": point.id, **rest})
        reserved = {*names, "id", *_META}
        values = list(
            dict.fromkeys(
                k
                for record in records
                for k in record
                if k not in reserved and not k.endswith("_err")
            )
        )
        for record in records:
            for key in record:
                if key.endswith("_err") and key[:-4] not in values:
                    raise SpecError(f"{key!r} is an error column with no value {key[:-4]!r}")
        columns = [
            *names,
            "id",
            *values,
            *(f"{v}_err" for v in values),
            *_META,
        ]
        return cls(scan, pd.DataFrame(records, columns=columns))

    # -- helpers -------------------------------------------------------------------------

    def _value_columns(self) -> list[str]:
        known = {*self.scan.param_names, "id", *_META}
        return [c for c in self.table.columns if c not in known and not c.endswith("_err")]

    def _metadata(self) -> dict:
        return {"scan": self.scan.to_dict()}

    # -- exports -----------------------------------------------------------------------

    def to_parquet(self, path) -> str:
        """Write the table to one Parquet file at ``path`` (needs pyarrow)."""
        path = os.fspath(path)
        export.write_parquet(
            self.table, path, name="scan", metadata=self._metadata(), spec=self.scan.base
        )
        return path

    def to_csv(self, directory) -> str:
        """Write ``scan.csv`` and ``metadata.yaml`` (the scan and the base spec) to
        ``directory``; returns the path of the CSV file."""
        directory = os.fspath(directory)
        os.makedirs(directory, exist_ok=True)
        path = os.path.join(directory, "scan.csv")
        self.table.to_csv(path, index=False, lineterminator="\n")
        document = {
            "pysrim2_version": export._version(),
            "exported_utc": export._now(),
            "spec": export._spec_json(self.scan.base),
            "tables": {"scan": export._jsonable(export._public(self._metadata()))},
        }
        with open(os.path.join(directory, "metadata.yaml"), "w", encoding="utf-8") as f:
            yaml.safe_dump(document, f, sort_keys=False)
        return path

    def to_hdf5(self, path, key="scan") -> str:
        """Write the table under ``key`` in an HDF5 file at ``path`` (needs pytables)."""
        export._require("tables", "hdf5")
        path = os.fspath(path)
        document = export._document("scan", self._metadata(), self.scan.base, export._now())
        with pd.HDFStore(path, mode="w", complevel=4, complib="zlib") as store:
            export._hdf_write(store.put, key, self.table, format="table")
            export._hdf_set_metadata(store, key, document)
        return path

    # -- plot ----------------------------------------------------------------------------

    def plot(self, x=None, y=None, hue=True, ax=None):
        """Plot the extracted value ``y`` against the parameter ``x`` with error bars.

        Defaults: ``x`` is the first scanned parameter, ``y`` the first extracted value and
        ``hue`` (one line per value) the first other scanned parameter; ``hue=False`` draws
        one line. Error bars come from ``<y>_err``. ``ax`` is a matplotlib Axes (a new one
        is made if omitted). Needs matplotlib, which is imported here only.
        """
        try:
            import matplotlib.pyplot as plt
        except ImportError as exc:
            raise ImportError(
                'ScanResult.plot needs matplotlib: pip install "pysrim2[plot]"'
            ) from exc
        params = self.scan.param_names
        if x is None:
            if not params:
                raise ValueError("the scan has no scanned parameter to plot against; pass x=")
            x = params[0]
        if y is None:
            values = self._value_columns()
            if not values:
                raise ValueError("the scan has no extracted value to plot; pass y=")
            y = values[0]
        if hue is True:
            hue = next((p for p in params if p != x), None)
        elif hue is False:
            hue = None
        for column in (x, y, hue):
            if column is not None and column not in self.table.columns:
                raise ValueError(
                    f"no column {column!r} in the scan table; "
                    f"columns are {', '.join(map(str, self.table.columns))}"
                )
        if ax is None:
            _, ax = plt.subplots()
        err = f"{y}_err" if f"{y}_err" in self.table.columns else None
        if hue is None:
            groups = [(None, self.table)]
        else:
            groups = [
                (f"{hue}={value}", group) for value, group in self.table.groupby(hue, sort=False)
            ]
        for label, group in groups:
            group = group.sort_values(x)
            ax.errorbar(
                group[x],
                group[y],
                yerr=group[err] if err else None,
                marker="o",
                capsize=3,
                label=label,
            )
        ax.set_xlabel(x)
        ax.set_ylabel(y)
        if hue is not None:
            ax.legend(title=hue)
        return ax
