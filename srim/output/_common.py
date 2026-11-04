"""Shared helpers and base class for SRIM output parsers"""

import re
import warnings
from io import BytesIO

import numpy as np

from ..core.ion import Ion

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


def _element_names(names, count):
    """Column names for target elements: header ``names`` made unique
    (``Si``, ``Si_2``) if they match the number of columns, else ``1, 2, ...``."""
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
