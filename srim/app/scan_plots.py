"""The scan views of the Results tab (Qt-free; matplotlib is imported inside the functions only).

:func:`scan_table` gives the table of a scan from its :class:`~srim.ui.tui.state.ScanState`, with
the columns of :attr:`srim.scan.ScanResult.table`, so the summary works the same live and after
the run. Row ``i`` of a table is point ``i`` of the scan (the index labels are the point indices).

The summary (:func:`summary_data`, :func:`draw_summary`) plots an extracted value against a
parameter, one line per value of a colour parameter. The overlays (:func:`draw_overlay`) draw one
curve per point of a depth profile. Every point keeps its style (colour, line style and marker)
for the point's index, so it looks the same in each plot (see :func:`point_styles`).
"""

import math
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

import numpy as np
import pandas as pd

from srim.scan.batches import derive_seed
from srim.scan.result import _META, ScanResult

#: most curves an overlay draws (the first ones checked, in point order)
CURVE_LIMIT = 12
#: line styles, by point index (a point's style does not depend on the others)
LINESTYLES = ("-", "--", ":", "-.")
#: markers, by point index
MARKERS = ("o", "s", "^", "D", "v")
#: the columns of a depth profile that are not values
_DEPTH_COLUMNS = ("depth_nm", "depth_A", "lateral_nm")


@dataclass(frozen=True)
class Style:
    """How one point is drawn: its ``color`` (a hex name), ``linestyle`` and ``marker``."""

    color: str
    linestyle: str
    marker: str


def point_label(names, values: Mapping) -> str:
    """``ion.energy_keV=10.0, ion.angle_deg=7.0``: the label of a point by its parameters."""
    return ", ".join(f"{name}={values[name]}" for name in names)


def value_columns(table: pd.DataFrame, names) -> list[str]:
    """The extracted value columns of a scan table (not the parameters, the errors or the
    columns that describe the point)."""
    known = {*names, "id", *_META}
    return [c for c in table.columns if c not in known and not str(c).endswith("_err")]


# --- the table of a scan -----------------------------------------------------------------


def _status(state, row) -> str:
    """The table's status of a point (see :mod:`srim.scan.result`): a point not run yet is
    ``missing``; a precision point that finished without its target is ``max_ions``."""
    if row.status in ("queued", "running"):
        return "missing"
    if row.status == "done" and state.precision is not None and row.reached is False:
        return "max_ions"
    return row.status


def _seeds(state, row, status) -> str | None:
    """The seeds column of a point: as the executor writes it (the point's seed, or the seeds of
    its batches). A binned beam's batches are not known here, so its seeds may differ."""
    scan = state.scan
    if state.base_seed is None or status == "missing":
        return None
    if scan.keep_seed:
        return str(scan.seed)
    seed = derive_seed(state.base_seed, int(row.id[:15], 16))
    if state.precision is not None:
        return ",".join(str(derive_seed(seed, b)) for b in row.batches) or None
    if scan.batch_ions is not None:
        if status not in ("done", "cached"):
            return None
        batches = math.ceil(scan.points()[row.index].spec.number_ions / scan.batch_ions)
        return ",".join(str(derive_seed(seed, b)) for b in range(batches))
    return str(seed)


def _record(state, row) -> dict[str, Any]:
    """One row of the table (the input of :meth:`ScanResult.from_rows`)."""
    status = _status(state, row)
    shown = status in ("done", "cached", "max_ions") or row.started
    record: dict[str, Any] = {
        "index": row.index,
        "status": status,
        "n_ions": None if status == "missing" else (row.ions or None),
        "seeds": _seeds(state, row, status),
        "path": f"points/{row.id[:16]}" if shown and status != "missing" else None,
        "error": row.error if status in ("failed", "stopped") else None,
    }
    point_values = row.values  # noqa: PD011 (the point's own dict, not a pandas frame)
    for name in state.value_names:
        if name in point_values:
            record[name] = point_values[name]
        if f"{name}_err" in point_values:
            record[f"{name}_err"] = point_values[f"{name}_err"]
    return record


def scan_table(state) -> pd.DataFrame:
    """The table of a scan as :attr:`ScanResult.table` has it, from a
    :class:`~srim.ui.tui.state.ScanState` (live, or finished). One row per point, in point order.

    A point that has not run yet has status ``missing`` and no values (``NaN``).
    """
    rows = [_record(state, row) for row in state.points.values()]
    return ScanResult.from_rows(state.scan, rows).table


# --- styles --------------------------------------------------------------------------------


def _numeric(column: pd.Series) -> bool:
    return pd.api.types.is_numeric_dtype(column) and not pd.api.types.is_bool_dtype(column)


def point_styles(table: pd.DataFrame, hue: str | None) -> dict[int, Style]:
    """The style of each point of ``table``, by point index (the table's index labels).

    The colour follows ``hue``: a numeric hue maps through ``viridis`` over its range; a text
    hue takes ``tab10`` in the order its values first appear. Without a hue (or for a point
    without a hue value) the colour is ``tab10`` by index. The line style and the marker follow
    the index only, so every curve differs from the others and a point keeps its style.
    """
    import matplotlib
    from matplotlib.colors import to_hex

    tab10 = matplotlib.colormaps["tab10"].colors
    viridis = matplotlib.colormaps["viridis"]
    colours: dict[int, str] = {}
    if hue is not None and hue in table.columns:
        column = table[hue]
        present = column.notna()
        if _numeric(column) and present.any():
            low = float(column[present].min())
            high = float(column[present].max())
            for index, value in zip(table.index, column, strict=True):
                if pd.notna(value):
                    position = 0.5 if high == low else (float(value) - low) / (high - low)
                    colours[int(index)] = to_hex(viridis(position))
        elif not _numeric(column):
            order = list(dict.fromkeys(str(v) for v in column[present]))
            for index, value in zip(table.index, column, strict=True):
                if pd.notna(value):
                    colours[int(index)] = to_hex(tab10[order.index(str(value)) % len(tab10)])
    styles = {}
    for index in table.index:
        point = int(index)
        colour = colours.get(point) or to_hex(tab10[point % len(tab10)])
        styles[point] = Style(colour, LINESTYLES[point % len(LINESTYLES)], MARKERS[point % 5])
    return styles


# --- the summary ---------------------------------------------------------------------------


def summary_data(table: pd.DataFrame, x: str, y: str, hue: str | None) -> pd.DataFrame:
    """The rows of ``table`` with a finite ``y``, sorted by the colour (``hue``) and then ``x``.

    ``y`` and its ``<y>_err`` are numbers; the index labels are the point indices.
    """
    values = pd.to_numeric(table[y], errors="coerce").to_numpy(dtype=float)
    keep = np.isfinite(values)
    data = table.loc[keep].copy()
    data[y] = values[keep]
    err = f"{y}_err"
    if err in data.columns:
        data[err] = pd.to_numeric(data[err], errors="coerce").to_numpy(dtype=float)
    keys = [column for column in dict.fromkeys([hue, x]) if column is not None]
    return data.sort_values(by=keys, kind="stable")


def draw_summary(data: pd.DataFrame, x: str, y: str, hue: str | None, styles, ax) -> None:
    """Draw ``y`` against ``x`` for the rows of ``data`` (see :func:`summary_data`).

    Each point is drawn with its style and its error bar (from ``<y>_err`` when the table has
    one). A line joins the points of each value of ``hue``. A numeric hue gets a colour bar; a
    text hue a legend. ``styles`` maps point indices to :class:`Style`.
    """
    import matplotlib
    from matplotlib import cm
    from matplotlib.colors import Normalize

    err = f"{y}_err" if f"{y}_err" in data.columns else None
    hued = hue is not None and hue in data.columns
    if not hued:
        groups = [(None, data)]
    else:
        groups = list(data.groupby(hue, sort=False, dropna=False))
    labelled = False
    for value, group in groups:
        first = styles.get(int(group.index[0]))
        line_colour = first.color if (hued and first is not None) else "0.6"
        label = None
        if hued and not _numeric(data[hue]):
            label = f"{hue}={value}"
            labelled = True
        if len(group) > 1:
            ax.plot(group[x], group[y], color=line_colour, linewidth=1, label=label)
        for index, row in group.iterrows():
            style = styles.get(int(index))
            yerr = None
            if err is not None and math.isfinite(row[err]):
                yerr = row[err]
            ax.errorbar(
                row[x],
                row[y],
                yerr=yerr,
                fmt="o" if style is None else style.marker,
                color="0.4" if style is None else style.color,
                capsize=3,
            )
    ax.set_xlabel(x)
    ax.set_ylabel(y)
    ax.set_title(f"{y} by {x}")
    if hued and _numeric(data[hue]):
        values = data[hue].to_numpy(dtype=float)
        low, high = float(np.nanmin(values)), float(np.nanmax(values))
        if high == low:
            high = low + 1.0
        mappable = cm.ScalarMappable(
            norm=Normalize(low, high), cmap=matplotlib.colormaps["viridis"]
        )
        mappable.set_array([])
        ax.figure.colorbar(mappable, ax=ax, label=hue)
    elif labelled:
        ax.legend(title=hue)


# --- the overlays --------------------------------------------------------------------------


def _value_names(frame: pd.DataFrame) -> list[str]:
    return [c for c in frame.columns if c not in _DEPTH_COLUMNS]


def stack_frames(frames: dict[int, pd.DataFrame]) -> pd.DataFrame:
    """The frames of the points as one frame, with a ``point`` column (the point index)."""
    parts = []
    for index, frame in frames.items():
        part = frame.copy()
        part.insert(0, "point", int(index))
        parts.append(part)
    if not parts:
        return pd.DataFrame({"point": pd.Series(dtype=int)})
    return pd.concat(parts, ignore_index=True)


def split_frames(stacked: pd.DataFrame) -> dict[int, pd.DataFrame]:
    """The frames of :func:`stack_frames`, by point index."""
    return {
        int(index): part.drop(columns="point").reset_index(drop=True)
        for index, part in stacked.groupby("point", sort=True)
    }


def draw_overlay(plot, frames: dict[int, pd.DataFrame], styles, labels, ax) -> None:
    """One curve per point: the sum of the depth profile's value columns (``ions`` and
    ``recoils``, say) against depth, in the point's style, labelled by ``labels``.

    ``frames`` maps point indices to their frames (``plot.data`` of each point, with
    ``depth_nm``); ``styles`` and ``labels`` map the same indices to a :class:`Style` and a
    text. The title is the plot's title.
    """
    if not frames:
        ax.text(0.5, 0.5, "no point to draw", transform=ax.transAxes, ha="center")
    names: list[str] = []
    for index, frame in frames.items():
        if len(frame) == 0:
            continue
        columns = _value_names(frame)
        names = names or columns
        depth = frame["depth_nm"].to_numpy(dtype=float)
        values = frame[columns].sum(axis=1).to_numpy(dtype=float)
        style = styles[index]
        ax.plot(
            depth,
            values,
            color=style.color,
            linestyle=style.linestyle,
            marker=style.marker,
            markevery=max(1, len(depth) // 10),
            label=labels.get(index),
        )
    ax.set_xlabel("depth (nm)")
    ax.set_ylabel(", ".join(names))
    ax.set_title(plot.title)
    if any(labels.get(index) for index in frames):
        ax.legend()


# --- the filter ----------------------------------------------------------------------------


def select_points(table: pd.DataFrame, expression: str) -> list[int]:
    """The point indices of the rows of ``table`` that match ``expression``.

    The expression is a :meth:`pandas.DataFrame.query` one: a column with a dotted name is
    written in backticks (``` `ion.energy_keV` > 10 ```). Raises :class:`ValueError` with the
    message of pandas for an expression it cannot evaluate, and for an empty one.
    """
    if not isinstance(expression, str) or not expression.strip():
        raise ValueError("enter a condition, for example `ion.energy_keV` > 10")
    try:
        matched = table.query(expression)
    except Exception as error:  # pandas raises several kinds of error for a bad expression
        raise ValueError(str(error) or type(error).__name__) from error
    return [int(index) for index in matched.index]
