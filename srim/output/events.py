"""Parsers for the event files: ``EXYZ.txt``, ``BACKSCAT.txt``, ``TRANSMIT.txt``, ``SPUTTER.txt``"""

import os
import re
import warnings

import numpy as np

from ..core.ion import Ion
from ._common import (
    SRIM_Output,
    SRIMOutputParseError,
    _empty_arrays,
    _prefix_factor,
    _read_numeric_table,
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


def _read_or_empty(path, header_end, row, columns, name):
    """``(header, arrays)`` from :func:`_read_numeric_table`; an empty file gives empty arrays and a
    warning, a file without the table header raises :class:`SRIMOutputParseError`."""
    header, arrays = _read_numeric_table(path, header_end, row, columns, name)
    if arrays is None:
        if header:
            raise SRIMOutputParseError(f"unable to extract table from {name}")
        warnings.warn(f"{name} is empty", stacklevel=3)
        arrays = _empty_arrays(columns)
    return header, arrays


def _ion_energy_eV(value, unit):
    return 1000.0 * _to_float(value) * _prefix_factor(energy_prefixes, unit.decode("ascii"))


class EXYZ(SRIM_Output):
    """``EXYZ.txt`` Energy of every ion along its path (needs ``exyz`` > 0 in TRIM)

    The ``exyz`` setting is the energy increment [eV]: SRIM writes a line each
    time an ion has lost that much energy. Based on the parser by philis-johan.

    Parameters
    ----------
    directory : :obj:`str`
         directory of calculation
    filename : :obj:`str`, optional
         filename for EXYZ. Default ``EXYZ.txt``

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
    metadata : :obj:`dict`
        ``ion``, ``energy_eV``, ``energy_interval_eV`` (the ``exyz`` increment),
        ``num_ions`` (number of different ions in the file), ``source``
    """

    def __init__(self, directory, filename="EXYZ.txt"):
        path = os.path.join(directory, filename)
        header, arrays = _read_or_empty(
            path,
            re.compile(rb"-+(?:[ \t]+-+)+[ \t]*"),
            _row_regex(rb"\d+", *[_NUMBER] * 6),
            [(0, "i")] + [(j, "f") for j in range(1, 7)],
            filename,
        )
        self._ion = None
        self._interval = float("nan")
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

        self._source = path
        (
            self._ion_number,
            self._energy,
            self._depth,
            self._y,
            self._z,
            self._electronic_stop,
            self._recoil_energy_loss,
        ) = arrays

    @property
    def ion(self):
        """Ion used in SRIM calculation

        **mass** could be wrong
        """
        return self._ion

    @property
    def num_ions(self):
        """Number of different ions in the file"""
        return len(np.unique(self._ion_number))

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
        return self._ion_number

    @property
    def energy(self):
        """Energy [keV] of the ion"""
        return self._energy

    @property
    def depth(self):
        """Depth [Ang] of the ion"""
        return self._depth

    @property
    def y(self):
        """Y position [Ang] of the ion"""
        return self._y

    @property
    def z(self):
        """Z position [Ang] of the ion"""
        return self._z

    @property
    def electronic_stop(self):
        """Electronic stopping [eV/Ang] of the ion"""
        return self._electronic_stop

    @property
    def recoil_energy_loss(self):
        """Energy [eV] lost to the last recoil"""
        return self._recoil_energy_loss

    def _dataframe_columns(self):
        return [
            ("ion", self._ion_number),
            ("energy_keV", self._energy),
            ("depth_A", self._depth),
            ("y_A", self._y),
            ("z_A", self._z),
            ("electronic_stopping_eV_per_A", self._electronic_stop),
            ("last_recoil_energy_eV", self._recoil_energy_loss),
        ]


class _EventFile(SRIM_Output):
    """Atoms leaving the target, one line each (TRIM.DAT format). Shared by the subclasses."""

    _filename = None

    def __init__(self, directory, filename=None):
        filename = filename or self._filename
        path = os.path.join(directory, filename)
        # SRIM writes the depth with an integer mantissa and an exponent (``5020011E-04``) and
        # separates a minus sign from the digits (``-  6202433E-06``)
        fields = [rb"[SBT]", rb"\d+", rb"\d+", _NUMBER, rb"-?[ \t]*" + _NUMBER] + [_NUMBER] * 5
        header, arrays = _read_or_empty(
            path,
            re.compile(rb"[ \t]*Numb[ \t]+Numb.*"),
            _row_regex(*fields),
            [(1, "i"), (2, "i")] + [(j, "f") for j in range(3, 10)],
            filename,
        )
        self._ion = None
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

        self._source = path
        self._ion_number, self._atomic_number = arrays[:2]
        self._values = np.column_stack(arrays[2:])

    @property
    def ion(self):
        """Ion used in SRIM calculation

        **mass** could be wrong
        """
        return self._ion

    @property
    def num_events(self):
        """Number of atoms in the file"""
        return len(self._ion_number)

    @property
    def num_ions(self):
        """Number of different ions that produced an event"""
        return len(np.unique(self._ion_number))

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
        return self._ion_number

    @property
    def atomic_number(self):
        """Atomic number Z of each atom"""
        return self._atomic_number

    @property
    def energy(self):
        """Energy [eV] of each atom"""
        return self._values[:, 0]

    @property
    def depth(self):
        """Last depth X [Ang] of each atom"""
        return self._values[:, 1]

    @property
    def y(self):
        """Last lateral Y position [Ang]"""
        return self._values[:, 2]

    @property
    def z(self):
        """Last lateral Z position [Ang]"""
        return self._values[:, 3]

    @property
    def cos_x(self):
        """Direction cosine along X (depth)"""
        return self._values[:, 4]

    @property
    def cos_y(self):
        """Direction cosine along Y"""
        return self._values[:, 5]

    @property
    def cos_z(self):
        """Direction cosine along Z"""
        return self._values[:, 6]

    def _dataframe_columns(self):
        return [
            ("ion", self._ion_number),
            ("atomic_number", self._atomic_number),
            ("energy_eV", self.energy),
            ("depth_A", self.depth),
            ("y_A", self.y),
            ("z_A", self.z),
            ("cos_x", self.cos_x),
            ("cos_y", self.cos_y),
            ("cos_z", self.cos_z),
        ]


_EVENT_DOC = """
    Parameters
    ----------
    directory : :obj:`str`
         directory of calculation
    filename : :obj:`str`, optional
         filename. Default ``{name}``

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
    metadata : :obj:`dict`
        ``ion``, ``energy_eV``, ``num_events`` (lines in the file), ``source``

    Notes
    -----
    SRIM writes ``depth_A`` in an unusual format (an integer mantissa such as
    ``5020011E-04``); the values are decoded to ordinary floats.
"""


class Backscat(_EventFile):
    __doc__ = (
        "``BACKSCAT.txt`` The kinetics of all backscattered ions (needs ``backscattered=1``)\n"
        + _EVENT_DOC.format(name="BACKSCAT.txt", who="the backscattered ion")
    )
    _filename = "BACKSCAT.txt"


class Transmit(_EventFile):
    __doc__ = (
        "``TRANSMIT.txt`` The kinetics of all transmitted ions (needs ``transmit=1``)\n"
        + _EVENT_DOC.format(name="TRANSMIT.txt", who="the transmitted ion")
    )
    _filename = "TRANSMIT.txt"


class Sputter(_EventFile):
    __doc__ = (
        "``SPUTTER.txt`` The kinetics of all target atoms sputtered from the target "
        "(needs ``sputtered=1``)\n"
        + _EVENT_DOC.format(name="SPUTTER.txt", who="the sputtered atom")
    )
    _filename = "SPUTTER.txt"
