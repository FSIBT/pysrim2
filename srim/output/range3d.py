"""Parser for ``RANGE_3D.txt``, the per-ion final positions"""

import os
import re

import numpy as np

from ..core.ion import Ion
from ._common import (
    SRIM_Output,
    SRIMOutputParseError,
    _read_table,
    _to_float,
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
        path = os.path.join(directory, filename)
        number = double_regex.encode()
        row = re.compile(rb"[ \t]*(\d{7})" + (rb"[ \t]+(" + number + rb")") * 3 + rb"[ \t]*")
        header, arrays = _read_table(
            path,
            re.compile(rb"-+(?:[ \t]+-+)+[ \t]*"),
            row,
            [(0, "i"), (1, "f"), (2, "f"), (3, "f")],
            "RANGE_3D",
        )
        match = re.search(
            rb"Ion\s*=\s*(" + symbol_regex.encode() + rb")\s*\(\d+\).*?"
            rb"Energy\s*=\s*(" + double_regex.encode() + rb")\s*keV",
            header,
            re.DOTALL,
        )
        if not match:
            raise SRIMOutputParseError("unable to extract ion from file")
        if arrays is None or len(arrays[0]) == 0:
            raise SRIMOutputParseError("unable to extract ion positions from file")

        self._source = path
        self._ion = Ion(match.group(1).decode("ascii"), 1000.0 * _to_float(match.group(2)))
        self._ion_numbers = arrays[0]
        self._positions = np.column_stack(arrays[1:])

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
