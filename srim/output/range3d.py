"""Parser for ``RANGE_3D.txt``, the per-ion final positions"""

import os
import re
import warnings

import numpy as np

from ..core.ion import Ion
from ._common import (
    SRIM_Output,
    SRIMOutputParseError,
    _read_numeric_table,
    _to_float,
    double_regex,
    symbol_regex,
)

#: the rule above the column names of RANGE_3D.txt (its last header line)
_HEADER_END = re.compile(rb"-+(?:[ \t]+-+)+[ \t]*")
#: an ion line: the 7-digit ion number, then depth, Y and Z
_ROW = re.compile(
    rb"[ \t]*(\d{7})" + (rb"[ \t]+(" + double_regex.encode() + rb")") * 3 + rb"[ \t]*"
)


class Range3D(SRIM_Output):
    """``RANGE_3D.txt`` Final position of every ion (needs ``ranges=1`` in TRIM)

    Parameters
    ----------
    directory : :obj:`str`
         directory of calculation
    filename : :obj:`str`, optional
         filename for Range3D. Default ``RANGE_3D.txt``
    max_rows : :obj:`int`, optional
         read at most this many ions (the first ones). Default ``None``: all of them. Check
         :attr:`truncated` to see whether ions were left out.

    Attributes
    ----------
    df : :class:`pandas.DataFrame`
        One row per ion. Columns:
          - ``ion``: ion number (int)
          - ``depth_A``: final depth along the surface normal [Ang]
          - ``y_A``: lateral position Y [Ang]
          - ``z_A``: lateral position Z [Ang]

    Notes
    -----
    SRIM can leave the last line of the file incomplete; such a line is ignored.
    A zero-byte file gives an empty frame (same columns) and a warning, as for the event
    files.
    """

    def __init__(self, directory, filename="RANGE_3D.txt", max_rows=None):
        path = os.path.join(directory, filename)
        header, arrays, truncated = _read_numeric_table(
            path,
            _HEADER_END,
            _ROW,
            [(0, "i"), (1, "f"), (2, "f"), (3, "f")],
            "RANGE_3D",
            max_rows=max_rows,
        )
        self._source = path
        self._ion = None
        self._truncated = truncated
        if arrays is None and not header:
            warnings.warn(f"{filename} is empty", stacklevel=2)
            self._ion_numbers = np.array([], dtype=int)
            self._positions = np.empty((0, 3))
            return
        match = re.search(
            rb"Ion\s*=\s*(" + symbol_regex.encode() + rb")\s*\(\d+\).*?"
            rb"Energy\s*=\s*(" + double_regex.encode() + rb")\s*keV",
            header,
            re.DOTALL,
        )
        if not match:
            raise SRIMOutputParseError("unable to extract ion from file")
        # a capped read of no rows is a valid parse: the header is there
        if arrays is None or (len(arrays[0]) == 0 and max_rows is None):
            raise SRIMOutputParseError("unable to extract ion positions from file")

        self._ion = Ion(match.group(1).decode("ascii"), 1000.0 * _to_float(match.group(2)))
        self._ion_numbers = arrays[0]
        self._positions = np.column_stack(arrays[1:])

    @property
    def metadata(self):
        """:obj:`dict` with ``ion`` (``None`` for an empty file), ``energy_eV``, ``num_ions``
        and ``source`` (file path)"""
        return {
            "ion": self._ion.symbol if self._ion is not None else None,
            "energy_eV": self._ion.energy if self._ion is not None else None,
            "num_ions": self.num_ions,
            "source": self._source,
        }

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
