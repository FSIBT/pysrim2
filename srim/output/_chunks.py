"""Helpers to read large SRIM files line by line and build DataFrames in chunks.

The parsers read a file one line at a time (never the whole file), parse each line to
a row, and collect rows into preallocated numpy arrays that are handed out as
:class:`pandas.DataFrame` chunks. Memory stays proportional to the final frame (and
to one chunk while parsing), not to the size of the text file.
"""

import numpy as np


def iter_lines(path, encoding="latin-1", keepends=False):
    """Yield the lines of ``path`` one at a time, without the line terminator
    (with it if ``keepends``; a last line without terminator then lacks it, which tells
    a truncated final line).

    ``encoding`` decodes the lines (SRIM's ``COLLISON.txt`` is ``cp437``: its box-drawing
    characters are the column separators). With ``encoding=None`` the lines are
    :obj:`bytes`. The file is closed when the generator finishes or is closed.
    """
    if encoding is None:
        with open(path, "rb") as f:
            for line in f:
                yield line if keepends else line.rstrip(b"\r\n")
        return
    with open(path, encoding=encoding, newline="") as f:
        for line in f:
            yield line if keepends else line.rstrip("\r\n")


def iter_row_chunks(rows, columns, dtypes, chunk_rows=50_000):
    """Collect ``rows`` into :class:`pandas.DataFrame` chunks of ``chunk_rows`` rows.

    Parameters
    ----------
    rows : iterable of sequences with one value per column. ``None`` becomes ``NaN`` in a
        float column; integer columns cannot hold ``None``, so use float (or object) dtype
        for nullable fields
    columns : list of column names
    dtypes : list of numpy dtypes (``int``, ``float``, ``object`` ...), one per column
    chunk_rows : rows per chunk; the arrays of one chunk are preallocated

    Yields
    ------
    :class:`pandas.DataFrame` — one per ``chunk_rows`` rows, the last one possibly
    shorter; nothing is yielded if ``rows`` is empty
    (:func:`concat_chunks` then gives an empty frame).
    """
    import pandas as pd

    dtypes = [np.dtype(d) for d in dtypes]
    ncol = len(columns)

    def new():
        return [np.empty(chunk_rows, dtype=d) for d in dtypes]

    def frame(arrays, n):
        return pd.DataFrame({c: a[:n] for c, a in zip(columns, arrays, strict=True)})

    arrays = new()
    n = 0
    for row in rows:
        for j in range(ncol):
            value = row[j]
            if value is None and dtypes[j].kind == "f":
                value = np.nan
            arrays[j][n] = value
        n += 1
        if n == chunk_rows:
            yield frame(arrays, n)
            arrays = new()
            n = 0
    if n:
        yield frame(arrays, n)


def concat_chunks(chunks, columns, dtypes):
    """Concatenate the DataFrames from :func:`iter_row_chunks` into one frame.

    With no chunks, returns an empty frame with ``columns`` and ``dtypes``.
    """
    import pandas as pd

    chunks = list(chunks)
    if not chunks:
        return pd.DataFrame(
            {c: np.array([], dtype=d) for c, d in zip(columns, dtypes, strict=True)}
        )
    if len(chunks) == 1:
        return chunks[0]
    return pd.concat(chunks, ignore_index=True)
