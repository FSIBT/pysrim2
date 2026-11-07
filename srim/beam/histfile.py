"""Reading measured histograms from text files.

A file has one bin per line, with either two columns (``centre count``) or three
(``low high count``). The columns are separated by whitespace, tabs, ``;`` or ``,``, and the
separator decides how a comma inside a number is read: when the separator is ``,``, commas
separate fields; otherwise a comma is a decimal mark (``1,5;10`` is 1.5 and 10). ``#`` starts
a comment, blank lines are skipped, CRLF line ends are accepted, and a first row with no digits
(a header) is skipped. Trailing empty fields (a final separator) are ignored.

Every error is a :class:`~srim.spec.SpecError` naming the file and, for a bad row, its 1-based
line number.
"""

import itertools
import math
import re
from pathlib import Path

import numpy as np

from ..output._common import _to_float
from ..spec import SpecError

#: a decimal number as written by SRIM or a spreadsheet: ``4``, ``4.0``, ``.4``, ``4,0``, ``4E+02``
_NUMBER = re.compile(r"[-+]?(?:[0-9]+(?:[.,][0-9]+)?|[0-9]+\.|[.,][0-9]+)(?:[eE][-+]?[0-9]+)?")


def edges_from_centres(centres) -> np.ndarray:
    """Bin edges for bins centred on ``centres`` (strictly increasing, at least two).

    Inner edges sit at the midpoints; the outer edges are half a neighbouring width beyond the
    first and last centre.
    """
    c = np.asarray(centres, dtype=float)
    mids = (c[:-1] + c[1:]) / 2.0
    first = c[0] - (c[1] - c[0]) / 2.0
    last = c[-1] + (c[-1] - c[-2]) / 2.0
    return np.concatenate([[first], mids, [last]])


def _separator(lines: list[str]) -> str | None:
    """The field separator of the file: ``;``, a tab, ``,`` or None for whitespace."""
    if any(";" in line for line in lines):
        return ";"
    if any("\t" in line for line in lines):
        return "\t"
    commas = [line for line in lines if "," in line]
    # ``,`` is the separator when every line with a comma has no space inside its fields
    # (``1,5 10`` is whitespace-separated with a decimal comma)
    if commas and all(
        not re.search(r"\s", re.sub(r"\s*,\s*", ",", line.strip())) for line in commas
    ):
        return ","
    return None


def _fields(content: str, sep: str | None) -> list[str]:
    parts = content.split() if sep is None else [p.strip() for p in content.split(sep)]
    while parts and parts[-1] == "":
        parts.pop()
    return parts


def _number(token: str, name: str, lineno: int) -> float:
    if not _NUMBER.fullmatch(token):
        raise SpecError(f"{name}: line {lineno}: {token!r} is not a number")
    value = _to_float(token)
    if not math.isfinite(value):
        raise SpecError(f"{name}: line {lineno}: {token!r} is not finite")
    return value


def read_histogram(path) -> tuple[np.ndarray, np.ndarray]:
    """The bin ``edges`` (length n+1) and ``counts`` (length n) of a histogram file.

    A two-column file gives centres, converted with :func:`edges_from_centres`. A three-column
    file gives ``low high count`` per bin; a gap between bins becomes a bin with zero count.

    Raises
    ------
    SpecError
        naming the file, and the line for a bad row
    """
    name = str(path)
    try:
        raw = Path(path).read_bytes()
    except OSError as exc:
        raise SpecError(f"{name}: cannot read the histogram file ({exc.strerror})") from None
    text = raw.decode("utf-8-sig", errors="replace")

    lines = []
    for lineno, line in enumerate(text.splitlines(), start=1):
        content = line.split("#", 1)[0].strip()
        if content:
            lines.append((lineno, content))
    # a first row with no digits is a header: it is skipped and takes no part in choosing
    # the separator
    header = bool(lines) and not re.search(r"[0-9]", lines[0][1])
    data = lines[1:] if header else lines
    sep = _separator([content for _, content in data])

    rows: list[tuple[int, list[float]]] = []
    ncol: int | None = None
    first_line = 0
    for lineno, content in data:
        fields = _fields(content, sep)
        if not fields:
            continue  # an empty row, as spreadsheets export them (";" or ",,")
        if ncol is None:
            if len(fields) not in (2, 3):
                raise SpecError(
                    f"{name}: line {lineno}: expected 2 columns (centre count) or 3 columns "
                    f"(low high count), got {len(fields)}"
                )
            ncol, first_line = len(fields), lineno
        elif len(fields) != ncol:
            raise SpecError(
                f"{name}: line {lineno}: {len(fields)} columns, but line {first_line} has "
                f"{ncol}; all rows need the same number of columns"
            )
        values = [_number(token, name, lineno) for token in fields]
        rows.append((lineno, values))

    if not rows:
        raise SpecError(f"{name}: no data rows")

    for lineno, values in rows:
        if values[-1] < 0:
            raise SpecError(
                f"{name}: line {lineno}: count must not be negative (got {values[-1]:g})"
            )
    counts = [values[-1] for _, values in rows]
    if not sum(counts) > 0:
        raise SpecError(f"{name}: all counts are zero; a histogram needs a positive total")

    if ncol == 2:
        if len(rows) < 2:
            raise SpecError(
                f"{name}: a two-column histogram (centre count) needs at least two rows, "
                f"got {len(rows)}"
            )
        centres = [values[0] for _, values in rows]
        for (_, prev), (lineno, values) in itertools.pairwise(rows):
            if not values[0] > prev[0]:
                raise SpecError(
                    f"{name}: line {lineno}: centres must be strictly increasing "
                    f"({values[0]:g} does not follow {prev[0]:g})"
                )
        return edges_from_centres(centres), np.asarray(counts, dtype=float)

    for lineno, values in rows:
        low, high, _ = values
        if not high > low:
            raise SpecError(f"{name}: line {lineno}: low must be below high ({low:g} to {high:g})")

    # bins in order; a gap between two bins becomes a bin with zero count
    edges = [rows[0][1][0]]
    bin_counts: list[float] = []
    for lineno, (low, high, count) in rows:
        if low < edges[-1]:
            raise SpecError(
                f"{name}: line {lineno}: bin {low:g} to {high:g} overlaps the previous bin "
                f"(ends at {edges[-1]:g})"
            )
        if low > edges[-1]:
            edges.append(low)
            bin_counts.append(0.0)
        edges.append(high)
        bin_counts.append(count)
    return np.asarray(edges, dtype=float), np.asarray(bin_counts, dtype=float)
