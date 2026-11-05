"""Shared helpers and base class for SRIM output parsers"""

import os
import re
import warnings
from io import BytesIO

import numpy as np

from ..core.ion import Ion
from ._chunks import concat_chunks, iter_lines, iter_row_chunks

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


def _read_numeric_table(path, header_end, row_regex, columns, name, chunk_rows=50000):
    """Read a SRIM table in one streaming pass, without holding the file in memory.

    Parameters
    ----------
    path : file to read
    header_end : compiled regex matching the (stripped) last header line
    row_regex : compiled regex matching a whole (stripped) data line
    columns : list of ``(group index, "i" or "f")``; the arrays returned, in this order
    name : used in the warning about data lines that do not match ``row_regex``

    Returns
    -------
    ``(header, arrays)``. ``header`` is the file text up to and including the last header
    line (the first lines if there is none). ``arrays`` is ``None`` when the file is empty
    or the header end was not found.
    Non-blank lines that do not match are skipped with one warning; a final line without a
    line break (SRIM may truncate it) is parsed if it matches, otherwise ignored silently.
    """
    header = []
    found = False
    bad = 0
    first_bad = None

    def rows():
        nonlocal found, bad, first_bad
        for raw in iter_lines(path, None, keepends=True):
            line = raw.rstrip(b"\r\n")
            if not found:
                header.append(raw)
                if header_end.fullmatch(line):
                    found = True
                elif len(header) >= _MAX_HEADER_LINES:
                    return
                continue
            match = row_regex.fullmatch(line)
            if match:
                groups = match.groups()
                yield [
                    int(groups[i]) if kind == "i" else _to_float(_squeeze(groups[i]))
                    for i, kind in columns
                ]
            elif line.strip() and raw.endswith(b"\n"):
                bad += 1
                first_bad = first_bad or line.strip()[:60]

    names = [str(j) for j in range(len(columns))]
    dtypes = [int if kind == "i" else float for _, kind in columns]
    frame = concat_chunks(iter_row_chunks(rows(), names, dtypes, chunk_rows), names, dtypes)
    header = b"".join(header)
    if not found:
        return header, None
    if bad:
        warnings.warn(
            f"{bad} line(s) in the {name} data section could not be parsed and were skipped "
            f"(first: {first_bad.decode('latin-1')!r})",
            stacklevel=4,
        )
    return header, [frame[n].to_numpy() for n in names]


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

    def _dataframe_columns(self):
        """Ordered ``(column name, array)`` pairs for :attr:`df`; set by subclasses."""
        raise NotImplementedError()

    @property
    def df(self):
        """:class:`pandas.DataFrame` with one row per depth bin.

        The first column is ``depth_A``; the others are documented in the
        class docstring. Values equal the array properties of the object.
        """
        import pandas as pd

        data = {}
        for name, values in self._dataframe_columns():
            values = np.asarray(values)
            if values.ndim == 2:
                for j in range(values.shape[1]):
                    data[name[j]] = values[:, j]
            else:
                data[name] = values
        return pd.DataFrame(data)

    @property
    def metadata(self):
        """:obj:`dict` with ``ion`` (symbol), ``energy_eV``, ``num_ions`` and ``source`` (file path)"""
        return {
            "ion": self.ion.symbol,
            "energy_eV": self.ion.energy,
            "num_ions": self.num_ions,
            "source": self._source,
        }

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
        match = re.search(rb"Total Ions calculated\s+=(\d+.\d+)", output)
        if match:
            # Cast string -> float -> round down to nearest int
            return int(_to_float(match.group(1)))
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
