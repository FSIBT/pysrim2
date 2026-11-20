"""Parsers for the event files: ``EXYZ.txt``, ``BACKSCAT.txt``, ``TRANSMIT.txt``, ``SPUTTER.txt``"""

import os
import re
import warnings

from ..core.ion import Ion
from ._common import (
    SRIMOutputParseError,
    _prefix_factor,
    _StreamedTable,
    _TableScan,
    _to_float,
    double_regex,
    energy_prefixes,
    symbol_regex,
)

_NUMBER = double_regex.encode()
_WS = rb"[ \t]+"


def _row_regex(*fields):
    """Compiled regex (one group per field) for a line of whitespace-separated fields."""
    return re.compile(rb"[ \t]*" + _WS.join(b"(" + f + b")" for f in fields) + rb"[ \t]*")


def _scan_table(table, name):
    """The construction pass of an EXYZ or event file: counts the rows and their distinct ions,
    warns about bad lines, and returns the header. An empty file warns; a file without the
    table header raises :class:`SRIMOutputParseError`."""
    table._count(distinct_ions=True)
    scan = table._scan
    if scan.found:
        scan.warn_bad(stacklevel=3)
    else:
        if scan.header:
            raise SRIMOutputParseError(f"unable to extract table from {name}")
        warnings.warn(f"{name} is empty", stacklevel=3)
    return scan.header


#: the rule above the column names of EXYZ.txt (its last header line)
_EXYZ_HEADER_END = re.compile(rb"-+(?:[ \t]+-+)+[ \t]*")
#: an EXYZ.txt line: ion number, energy [keV], depth, Y, Z, stopping, recoil energy loss
_EXYZ_ROW = _row_regex(rb"\d+", *[_NUMBER] * 6)
_EXYZ_COLUMNS = [(0, "i")] + [(j, "f") for j in range(1, 7)]
#: the last header line of the event files (TRIM.DAT layout: ``Numb  Numb  ...``)
_EVENT_HEADER_END = re.compile(rb"[ \t]*Numb[ \t]+Numb.*")
# SRIM writes the depth with an integer mantissa and an exponent (``5020011E-04``) and
# separates a minus sign from the digits (``-  6202433E-06``)
_EVENT_FIELDS = [rb"[SBT]", rb"\d+", rb"\d+", _NUMBER, rb"-?[ \t]*" + _NUMBER] + [_NUMBER] * 5
#: an event line: type letter, ion number, Z, energy, depth, Y, Z and the direction cosines
_EVENT_ROW = _row_regex(*_EVENT_FIELDS)
_EVENT_COLUMNS = [(1, "i"), (2, "i")] + [(j, "f") for j in range(3, 10)]


def _ion_energy_eV(value, unit):
    return 1000.0 * _to_float(value) * _prefix_factor(energy_prefixes, unit.decode("ascii"))


class EXYZ(_StreamedTable):
    """``EXYZ.txt`` Energy of every ion along its path (needs ``exyz`` > 0 in TRIM)

    The ``exyz`` setting is the energy increment [eV]: SRIM writes a line each
    time an ion has lost that much energy. Based on the parser by philis-johan.

    Parameters
    ----------
    directory : :obj:`str`
         directory of calculation
    filename : :obj:`str`, optional
         filename for EXYZ. Default ``EXYZ.txt``
    max_rows : :obj:`int`, optional
         read at most this many lines (the first ones). Default ``None``: all of them.

    Attributes
    ----------
    df : :class:`pandas.DataFrame`
        One row per sampled point of an ion path. Columns:
          - ``ion``: ion number (int)
          - ``energy_keV``: ion energy [keV]
          - ``depth_A``: depth X into the target [Ang]
          - ``y_A``, ``z_A``: lateral position [Ang]
          - ``electronic_stopping_eV_per_A``: electronic stopping [eV/Ang]
          - ``last_recoil_energy_eV``: energy lost to the last recoil [eV]

    Notes
    -----
    Creating the object reads the file once and keeps no rows; :attr:`df` reads them
    all on first use, and :meth:`iter_chunks` streams them in chunks.
    """

    iter_chunks = _StreamedTable.iter_chunks  # listed on the class page by autodoc

    def __init__(self, directory, filename="EXYZ.txt", max_rows=None):
        path = os.path.join(directory, filename)
        self._scan = _TableScan(
            path, _EXYZ_HEADER_END, _EXYZ_ROW, _EXYZ_COLUMNS, filename, max_rows
        )
        self._source = path
        self._ion = None
        self._interval = float("nan")
        header = _scan_table(self, filename)
        if header:
            match = re.search(
                rb"Energy Interval[^\n]*\n\s*("
                + symbol_regex.encode()
                + rb")\s+"
                + _NUMBER
                + rb"\s+"
                rb"(" + _NUMBER + rb")\s*(eV|keV|MeV|GeV)\s+(" + _NUMBER + rb")\s*(eV|keV|MeV|GeV)",
                header,
            )
            if not match:
                raise SRIMOutputParseError("unable to extract ion from file")
            self._ion = Ion(match.group(1).decode("ascii"), _ion_energy_eV(*match.group(2, 3)))
            self._interval = _ion_energy_eV(*match.group(4, 5))

    @property
    def ion(self):
        """Ion used in SRIM calculation

        **mass** could be wrong
        """
        return self._ion

    @property
    def num_ions(self):
        """Number of different ions in the file"""
        return self._distinct

    @property
    def energy_interval(self):
        """Energy increment [eV] between lines (the ``exyz`` setting)"""
        return self._interval

    @property
    def metadata(self):
        """:obj:`dict`: ``ion``, ``energy_eV``, ``energy_interval_eV``, ``num_ions``, ``source``"""
        return {
            "ion": self._ion.symbol if self._ion is not None else None,
            "energy_eV": self._ion.energy if self._ion is not None else None,
            "energy_interval_eV": self._interval,
            "num_ions": self.num_ions,
            "source": self._source,
        }

    @property
    def ion_number(self):
        """Ion number of each line"""
        return self._table_arrays()[0]

    @property
    def energy(self):
        """Energy [keV] of the ion"""
        return self._table_arrays()[1]

    @property
    def depth(self):
        """Depth [Ang] of the ion"""
        return self._table_arrays()[2]

    @property
    def y(self):
        """Y position [Ang] of the ion"""
        return self._table_arrays()[3]

    @property
    def z(self):
        """Z position [Ang] of the ion"""
        return self._table_arrays()[4]

    @property
    def electronic_stop(self):
        """Electronic stopping [eV/Ang] of the ion"""
        return self._table_arrays()[5]

    @property
    def recoil_energy_loss(self):
        """Energy [eV] lost to the last recoil"""
        return self._table_arrays()[6]

    def _table_columns(self, arrays):
        return [
            ("ion", arrays[0]),
            ("energy_keV", arrays[1]),
            ("depth_A", arrays[2]),
            ("y_A", arrays[3]),
            ("z_A", arrays[4]),
            ("electronic_stopping_eV_per_A", arrays[5]),
            ("last_recoil_energy_eV", arrays[6]),
        ]


class _EventFile(_StreamedTable):
    """Atoms leaving the target, one line each (TRIM.DAT format). Shared by the subclasses."""

    _filename = None

    def __init__(self, directory, filename=None, max_rows=None):
        filename = filename or self._filename
        path = os.path.join(directory, filename)
        self._scan = _TableScan(
            path, _EVENT_HEADER_END, _EVENT_ROW, _EVENT_COLUMNS, filename, max_rows
        )
        self._source = path
        self._ion = None
        header = _scan_table(self, filename)
        if header:
            match = re.search(
                rb"TRIM Calc\.=\s*("
                + symbol_regex.encode()
                + rb")\(\s*("
                + _NUMBER
                + rb")\s*(\w+)\s*\)",
                header,
            )
            if not match:
                raise SRIMOutputParseError(f"unable to extract header from {filename}")
            self._ion = Ion(match.group(1).decode("ascii"), _ion_energy_eV(*match.group(2, 3)))

    @property
    def ion(self):
        """Ion used in SRIM calculation

        **mass** could be wrong
        """
        return self._ion

    @property
    def num_events(self):
        """Number of atoms in the file"""
        return self._num_rows

    @property
    def num_ions(self):
        """Number of different ions that produced an event"""
        return self._distinct

    @property
    def metadata(self):
        """:obj:`dict` with ``ion``, ``energy_eV``, ``num_events`` (lines in the file),
        ``source``"""
        return {
            "ion": self.ion.symbol if self.ion is not None else None,
            "energy_eV": self.ion.energy if self.ion is not None else None,
            "num_events": self.num_events,
            "source": self._source,
        }

    @property
    def ion_number(self):
        """Number of the ion that caused the event"""
        return self._table_arrays()[0]

    @property
    def atomic_number(self):
        """Atomic number Z of each atom"""
        return self._table_arrays()[1]

    @property
    def energy(self):
        """Energy [eV] of each atom"""
        return self._table_arrays()[2]

    @property
    def depth(self):
        """Last depth X [Ang] of each atom"""
        return self._table_arrays()[3]

    @property
    def y(self):
        """Last lateral Y position [Ang]"""
        return self._table_arrays()[4]

    @property
    def z(self):
        """Last lateral Z position [Ang]"""
        return self._table_arrays()[5]

    @property
    def cos_x(self):
        """Direction cosine along X (depth)"""
        return self._table_arrays()[6]

    @property
    def cos_y(self):
        """Direction cosine along Y"""
        return self._table_arrays()[7]

    @property
    def cos_z(self):
        """Direction cosine along Z"""
        return self._table_arrays()[8]

    def _table_columns(self, arrays):
        return [
            ("ion", arrays[0]),
            ("atomic_number", arrays[1]),
            ("energy_eV", arrays[2]),
            ("depth_A", arrays[3]),
            ("y_A", arrays[4]),
            ("z_A", arrays[5]),
            ("cos_x", arrays[6]),
            ("cos_y", arrays[7]),
            ("cos_z", arrays[8]),
        ]


_EVENT_DOC = """
    Parameters
    ----------
    directory : :obj:`str`
         directory of calculation
    filename : :obj:`str`, optional
         filename. Default ``{name}``
    max_rows : :obj:`int`, optional
         read at most this many lines (the first ones). Default ``None``: all of them.

    Attributes
    ----------
    df : :class:`pandas.DataFrame`
        One row per atom (empty if there were none). Columns:
          - ``ion``: number of the ion that caused the event (int)
          - ``atomic_number``: Z of the atom ({who}) (int)
          - ``energy_eV``: energy of the atom [eV]
          - ``depth_A``: last depth X [Ang]
          - ``y_A``, ``z_A``: last lateral position [Ang]
          - ``cos_x``, ``cos_y``, ``cos_z``: direction cosines of the final trajectory

    Notes
    -----
    SRIM writes ``depth_A`` in an unusual format (an integer mantissa such as
    ``5020011E-04``); the values are decoded to ordinary floats.

    Creating the object reads the file once and keeps no rows; :attr:`df` reads them
    all on first use, and :meth:`iter_chunks` streams them in chunks.
"""


class Backscat(_EventFile):
    __doc__ = (
        "``BACKSCAT.txt`` The kinetics of all backscattered ions (needs ``backscattered=1``)\n"
        + _EVENT_DOC.format(name="BACKSCAT.txt", who="the backscattered ion")
    )
    _filename = "BACKSCAT.txt"
    iter_chunks = _StreamedTable.iter_chunks  # listed on the class page by autodoc


class Transmit(_EventFile):
    __doc__ = (
        "``TRANSMIT.txt`` The kinetics of all transmitted ions (needs ``transmit=1``)\n"
        + _EVENT_DOC.format(name="TRANSMIT.txt", who="the transmitted ion")
    )
    _filename = "TRANSMIT.txt"
    iter_chunks = _StreamedTable.iter_chunks  # listed on the class page by autodoc


class Sputter(_EventFile):
    __doc__ = (
        "``SPUTTER.txt`` The kinetics of all target atoms sputtered from the target "
        "(needs ``sputtered=1``)\n"
        + _EVENT_DOC.format(name="SPUTTER.txt", who="the sputtered atom")
    )
    _filename = "SPUTTER.txt"
    iter_chunks = _StreamedTable.iter_chunks  # listed on the class page by autodoc
