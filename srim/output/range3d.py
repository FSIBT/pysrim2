"""Parser for ``RANGE_3D.txt``, the per-ion final positions"""

import os
import re

import numpy as np

from ..core.ion import Ion
from ._common import (
    SRIM_Output,
    SRIMOutputParseError,
    _to_float,
    _warn_unparsed_rows,
    double_regex,
    symbol_regex,
)


class Range3D(SRIM_Output):
    """``RANGE_3D.txt`` Final position of every ion (needs ``ranges=1`` in TRIM)

    Parameters
    ----------
    directory : :obj:`str`
         directory of calculation
    filename : :obj:`str`, optional
         filename for Range3D. Default ``RANGE_3D.txt``

    Attributes
    ----------
    df : :class:`pandas.DataFrame`
        One row per ion. Columns:
          - ``ion``: ion number (int)
          - ``depth_A``: final depth along the surface normal [Ang]
          - ``y_A``: lateral position Y [Ang]
          - ``z_A``: lateral position Z [Ang]
    metadata : :obj:`dict`
        ``ion``, ``energy_eV``, ``num_ions`` (number of ion positions in the
        file), ``source``

    Notes
    -----
    SRIM can leave the last line of the file incomplete; such a line is ignored.
    """

    def __init__(self, directory, filename="RANGE_3D.txt"):
        with open(os.path.join(directory, filename), "rb") as f:
            output = f.read()

        match = re.search(
            rb"Ion\s*=\s*(" + symbol_regex.encode() + rb")\s*\(\d+\).*?"
            rb"Energy\s*=\s*(" + double_regex.encode() + rb")\s*keV",
            output,
            re.DOTALL,
        )
        if not match:
            raise SRIMOutputParseError("unable to extract ion from file")
        symbol = match.group(1).decode("ascii")
        energy = _to_float(match.group(2))  # keV

        number = double_regex.encode()
        rows = re.findall(
            rb"^[ \t]*(\d{7})[ \t]+("
            + number
            + rb")[ \t]+("
            + number
            + rb")[ \t]+("
            + number
            + rb")[ \t]*\r?$",
            output,
            re.MULTILINE,
        )
        if not rows:
            raise SRIMOutputParseError("unable to extract ion positions from file")
        dashes = re.search(rb"^-+(?:[ \t]+-+)+[ \t]*\r?$", output, re.MULTILINE)
        if dashes:
            row_regex = rb"[ \t]*\d{7}(?:[ \t]+" + number + rb"){3}[ \t]*\r?"
            _warn_unparsed_rows(output, dashes.end(), row_regex, "RANGE_3D")

        self._source = os.path.join(directory, filename)
        self._ion = Ion(symbol, 1000.0 * energy)
        self._ion_numbers = np.array([int(r[0]) for r in rows], dtype=int)
        self._positions = np.array([[_to_float(t) for t in r[1:]] for r in rows])

    @property
    def ion(self):
        """Ion used in SRIM calculation

        **mass** could be wrong
        """
        return self._ion

    @property
    def num_ions(self):
        """Number of ion positions in the file"""
        return len(self._ion_numbers)

    @property
    def depth(self):
        """Final depth [Ang] of each ion"""
        return self._positions[:, 0]

    @property
    def y(self):
        """Final lateral Y position [Ang] of each ion"""
        return self._positions[:, 1]

    @property
    def z(self):
        """Final lateral Z position [Ang] of each ion"""
        return self._positions[:, 2]

    def _dataframe_columns(self):
        return [
            ("ion", self._ion_numbers),
            ("depth_A", self.depth),
            ("y_A", self.y),
            ("z_A", self.z),
        ]
