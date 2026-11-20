"""Shared helpers and base class for SRIM output parsers"""

import os
import re
import warnings
from io import BytesIO

import numpy as np

from ..core.ion import Ion
from ._chunks import iter_lines, iter_row_chunks

# Valid double_regex 4, 4.0, .4, 4.0e100, and comma decimals (4,0 / 4,0E+02)
# as written by SRIM under locales that use a comma as decimal separator
double_regex = r"[-+]?(?:\d+(?:[.,]\d*)?|[.,]\d+)(?:[eE][-+]?\d+)?"
symbol_regex = r"[A-Z][a-z]?"
int_regex = r"[+-]?\d+"


def _to_float(token):
    """Convert a SRIM numeric token to float, accepting ``,`` as decimal mark.

    SRIM running under a locale with a decimal comma writes ``1,234E+02``.
    This avoids touching the process-wide locale.
    """
    if isinstance(token, (bytes, bytearray)):
        token = bytes(token).decode("latin-1")
    return float(token.replace(",", "."))


energy_prefixes = {"eV": 1e-3, "keV": 1, "MeV": 1e3, "GeV": 1e6}
length_prefixes = {"A": 1e-4, "um": 1, "mm": 1e3, "cm": 1e4, "m": 1e6, "km": 1e9}


def _prefix_factor(prefixes, unit):
    """Return the conversion factor for ``unit``, or raise a parse error."""
    try:
        return prefixes[unit]
    except KeyError:
        raise SRIMOutputParseError(f"unknown unit {unit!r}") from None


class SRIMOutputParseError(Exception):
    """SRIM error reading output file"""

    pass


def _header_symbols(output, pattern):
    """Element symbols (as in the file, repeats included) from a table header."""
    match = re.search(pattern, output, re.MULTILINE)
    if not match:
        return []
    return [raw.decode("ascii") for raw in re.findall(rb"[A-Z][a-z]?", match.group(1))]


def _target_symbols(output):
    """Distinct element symbols of the target material section of a table file, in order."""
    found = re.findall(rb"Layer #\s*\d+-\s*([A-Z][a-z]?)\s*=\s*[-+.\dE]+\s+Atomic Percent", output)
    return list(dict.fromkeys(raw.decode("ascii") for raw in found))


def _element_names(names, count, output=None):
    """Column names for target elements: header ``names`` made unique
    (``Si``, ``Si_2``) if they match the number of columns, else ``1, 2, ...``.

    Kinchin-Pease tables have a single column and no element symbols in the header; for a
    target with a single element (listed in ``output``, the file text) that element names it.
    """
    if not names and count == 1 and output is not None:
        target = _target_symbols(output)
        if len(target) == 1:
            return target
    if len(names) != count:
        # Kinchin-Pease tables have a single column and no element names: no warning
        if names or count > 1:
            warnings.warn(
                f"found {len(names)} element names in the table header for {count} columns; "
                "naming the element columns 1, 2, ...",
                stacklevel=2,
            )
        return [str(i + 1) for i in range(count)]
    seen = {}
    unique = []
    for name in names:
        seen[name] = seen.get(name, 0) + 1
        unique.append(name if seen[name] == 1 else f"{name}_{seen[name]}")
    return unique


_MAX_HEADER_LINES = 200


class _TableScan:
    """One streaming pass over a SRIM table in a text file.

    ``columns`` is a list of ``(group index, "i" or "f")``: the regex group of each column
    and its kind. No rows are kept here: :meth:`chunks` yields them chunk by chunk. The
    state of the pass (``header``, ``found``, ``truncated``, ``bad``, ``first_bad``) is
    complete once :meth:`chunks` has run to the end; each pass starts it afresh.
    """

    def __init__(self, path, header_end, row_regex, columns, name, max_rows=None):
        self.path = path
        self.header_end = header_end
        self.row_regex = row_regex
        self.columns = columns
        self.name = name
        self.max_rows = max_rows
        self.dtypes = [int if kind == "i" else float for _, kind in columns]
        self.header = b""
        self.found = False
        self.truncated = False
        self.bad = 0
        self.first_bad = None

    def _rows(self):
        """The parsed data rows of one pass, a list of values per row"""
        header = []
        self.found = False
        self.truncated = False
        self.bad = 0
        self.first_bad = None
        count = 0
        for raw in iter_lines(self.path, None, keepends=True):
            line = raw.rstrip(b"\r\n")
            if not self.found:
                header.append(raw)
                if self.header_end.fullmatch(line):
                    self.found = True
                elif len(header) >= _MAX_HEADER_LINES:
                    break
                continue
            match = self.row_regex.fullmatch(line)
            if match:
                if self.max_rows is not None and count >= self.max_rows:
                    self.truncated = True
                    break
                count += 1
                groups = match.groups()
                yield [
                    int(groups[i]) if kind == "i" else _to_float(_squeeze(groups[i]))
                    for i, kind in self.columns
                ]
            elif line.strip() and raw.endswith(b"\n"):
                self.bad += 1
                self.first_bad = self.first_bad or line.strip()[:60]
        self.header = b"".join(header)

    def chunks(self, chunk_rows=50_000):
        """Yield the rows in chunks of up to ``chunk_rows``: a list with one array per column
        (in the order of ``columns``). A table without rows yields one chunk of empty arrays,
        so a caller always gets the columns and dtypes."""
        names = [str(j) for j in range(len(self.columns))]
        given = False
        for frame in iter_row_chunks(self._rows(), names, self.dtypes, chunk_rows):
            given = True
            yield [frame[n].to_numpy() for n in names]
        if not given:
            yield _empty_arrays(self.columns)

    def arrays(self, chunk_rows=50_000):
        """Every row, one array per column: the whole table in memory"""
        pieces = list(self.chunks(chunk_rows))
        return [np.concatenate([piece[j] for piece in pieces]) for j in range(len(self.columns))]

    def warn_bad(self, stacklevel=2):
        """Warn once about the data lines that did not match the row pattern"""
        if self.bad:
            warnings.warn(
                f"{self.bad} line(s) in the {self.name} data section could not be parsed and "
                f"were skipped (first: {self.first_bad.decode('latin-1')!r})",
                stacklevel=stacklevel + 1,
            )


def _read_numeric_table(
    path, header_end, row_regex, columns, name, chunk_rows=50000, max_rows=None
):
    """Read a SRIM table in one streaming pass, without holding the file in memory.

    Parameters
    ----------
    path : file to read
    header_end : compiled regex matching the (stripped) last header line
    row_regex : compiled regex matching a whole (stripped) data line
    columns : list of ``(group index, "i" or "f")``; the arrays returned, in this order
    name : used in the warning about data lines that do not match ``row_regex``
    max_rows : read at most this many data rows (the first ones); ``None`` reads all

    Returns
    -------
    ``(header, arrays, truncated)``. ``header`` is the file text up to and including the last
    header line (the first lines if there is none). ``arrays`` is ``None`` when the file is
    empty or the header end was not found. ``truncated`` is True only when ``max_rows`` was
    reached and a further data row existed (so rows were left out).
    Non-blank lines that do not match are skipped with one warning; a final line without a
    line break (SRIM may truncate it) is parsed if it matches, otherwise ignored silently.
    """
    scan = _TableScan(path, header_end, row_regex, columns, name, max_rows)
    arrays = scan.arrays(chunk_rows)
    if not scan.found:
        return scan.header, None, False
    scan.warn_bad(stacklevel=2)
    return scan.header, arrays, scan.truncated


def _squeeze(token):
    """Remove whitespace inside a numeric token (SRIM writes ``-  6202433E-06``)"""
    return re.sub(rb"\s+", b"", token) if (b" " in token or b"\t" in token) else token


def _empty_arrays(columns):
    return [np.array([], dtype=int if kind == "i" else float) for _, kind in columns]


def _find_output(directory, filename):
    """Directory holding ``filename``: ``directory`` first, then its ``SRIM Outputs``
    subfolder (where some SRIM installs write it); ``None`` if it is in neither."""
    for folder in (directory, os.path.join(directory, "SRIM Outputs")):
        if os.path.isfile(os.path.join(folder, filename)):
            return folder
    return None


class SRIM_Output:
    _source = None
    _truncated = False

    @property
    def truncated(self):
        """True when the parser was given a ``max_rows`` and left rows of the file out"""
        return self._truncated

    def _dataframe_columns(self):
        """Ordered ``(column name, array)`` pairs for :attr:`df`; set by subclasses."""
        raise NotImplementedError()

    @property
    def df(self):
        """:class:`pandas.DataFrame` with one row per depth bin.

        The first column is ``depth_A``; the others are documented in the
        class docstring. Values equal the array properties of the object.
        """
        return _frame(self._dataframe_columns())

    @property
    def metadata(self):
        """:obj:`dict` with ``ion`` (symbol), ``energy_eV``, ``num_ions`` and ``source`` (file path).

        ``ions_calculated`` (the raw ion count of SRIM, a float) is included when the file
        has it; ``num_ions`` is that count rounded to the nearest integer.
        """
        meta = {
            "ion": self.ion.symbol,
            "energy_eV": self.ion.energy,
            "num_ions": self.num_ions,
            "source": self._source,
        }
        if getattr(self, "_ions_calculated", None) is not None:
            meta["ions_calculated"] = self._ions_calculated
        return meta

    def _read_name(self, output):
        raise NotImplementedError()

    def _read_ion(self, output):
        ion_regex = rf"Ion\s+=\s+({symbol_regex})\s+Energy\s+=\s+({double_regex})\s+keV"
        match = re.search(ion_regex.encode("utf-8"), output)
        if match:
            symbol = str(match.group(1).decode("utf-8"))
            energy = _to_float(match.group(2))  # keV
            return Ion(symbol, 1000.0 * energy)
        raise SRIMOutputParseError("unable to extract ion from file")

    def _read_target(self, output):
        match_target = re.search(rb"(?<=====\r\n)Layer\s+\d+\s+:.*?(?=====)", output, re.DOTALL)
        if match_target:
            layer_regex = (
                r"Layer\s+(?P<i>\d+)\s+:\s+(.+)\r\n"
                rf"Layer Width\s+=\s+({double_regex})\s+A\s+;\r\n"
                rf"\s+Layer #\s+(?P=i)- Density = ({double_regex}) atoms/cm3 = ({double_regex}) g/cm3\r\n"
                rf"((?:\s+Layer #\s+(?P=i)-\s+{symbol_regex}\s+=\s+{double_regex}\s+Atomic Percent = {double_regex}\s+Mass Percent\r\n)+)"
            )
            layers = re.findall(layer_regex.encode("utf-8"), match_target.group(0))
            if layers:
                element_regex = rf"\s+Layer #\s+(\d+)-\s+({symbol_regex})\s+=\s+({double_regex})\s+Atomic Percent = ({double_regex})\s+Mass Percent\r\n"
                element_regex = element_regex.encode()

                layers_elements = []
                for layer in layers:
                    # We know that elements will match
                    layers_elements.append(re.findall(element_regex, layer[5]))

                raise NotImplementedError()

        raise SRIMOutputParseError("unable to extract total target from file")

    def _read_num_ions(self, output):
        """Number of ions, rounded to the nearest integer.

        SRIM writes "Total Ions calculated" as an effective number, e.g. ``59.93`` for a run
        of 60 ions, so truncating would give 59. The raw value is kept as
        ``self._ions_calculated`` (in :attr:`metadata` as ``ions_calculated``).
        """
        match = re.search(rb"Total Ions calculated\s+=(\d+.\d+)", output)
        if match:
            self._ions_calculated = _to_float(match.group(1))
            return int(self._ions_calculated + 0.5)
        raise SRIMOutputParseError("unable to extract total ions from file")

    def _read_table(self, output):
        match = re.search(
            (
                b"=+(.*)"
                rb"-+(?:\s+-+)+"
            ),
            output,
            re.DOTALL,
        )
        # Read Data from table

        if match:
            # Headers TODO: name the columns in table
            header = None

            # Data
            data = np.genfromtxt(BytesIO(output[match.end() :].replace(b",", b".")), max_rows=100)
            return data
        raise SRIMOutputParseError("unable to extract table from file")


def _frame(columns):
    """DataFrame from ``(name, values)`` pairs (as :attr:`SRIM_Output.df`); a 2-D ``values``
    gives one column per name in the list ``name``"""
    import pandas as pd

    data = {}
    for name, values in columns:
        values = np.asarray(values)
        if values.ndim == 2:
            for j in range(values.shape[1]):
                data[name[j]] = values[:, j]
        else:
            data[name] = values
    return pd.DataFrame(data)


class _StreamedTable(SRIM_Output):
    """A per-ion table (``RANGE_3D.txt``, ``EXYZ.txt``, the event files): its rows stay in the file.

    The constructor makes one pass over the file (:meth:`_count`) that counts the rows and
    keeps none of them, so holding the object costs no table memory. :attr:`df` and the
    array properties read the whole table on first use and cache it. :meth:`iter_chunks`
    reads it in chunks and keeps nothing.

    Subclasses set ``self._scan`` (a :class:`_TableScan`) and implement
    :meth:`_table_columns`.
    """

    _scan = None
    _num_rows = 0
    _distinct = 0
    _cache = None

    def _count(self, distinct_ions=False):
        """One pass that counts the rows (and, with ``distinct_ions``, the distinct ion numbers
        of column 0, one integer each)"""
        rows = 0
        ions = set()
        for arrays in self._scan.chunks():
            rows += len(arrays[0])
            if distinct_ions:
                ions.update(np.unique(arrays[0]).tolist())
        self._num_rows = rows
        self._distinct = len(ions)
        self._truncated = self._scan.truncated

    def _table_arrays(self):
        """Every row, one array per table column: the whole table (cached)"""
        if self._cache is None:
            self._cache = self._scan.arrays()
        return self._cache

    def _table_columns(self, arrays):
        """Ordered ``(column name, array)`` pairs for the table columns ``arrays``"""
        raise NotImplementedError()

    def _dataframe_columns(self):
        return self._table_columns(self._table_arrays())

    def iter_chunks(self, chunk_rows=50_000):
        """Iterate over the rows as :class:`pandas.DataFrame` chunks of ``chunk_rows`` rows

        The chunks, concatenated, are :attr:`df`: the same columns, dtypes and values, with
        the index the row position in :attr:`df`. The file is read in one pass and nothing
        is kept, so memory use is one chunk. A table without rows gives one empty chunk
        with the same columns and dtypes.

        Raises
        ------
        ValueError
            if ``chunk_rows`` is less than 1
        """
        chunk_rows = int(chunk_rows)
        if chunk_rows < 1:
            raise ValueError("chunk_rows must be a positive integer")
        return self._chunks(chunk_rows)

    def _chunks(self, chunk_rows):
        import pandas as pd

        offset = 0
        for arrays in self._scan.chunks(chunk_rows):
            frame = _frame(self._table_columns(arrays))
            frame.index = pd.RangeIndex(offset, offset + len(frame))
            offset += len(frame)
            yield frame
