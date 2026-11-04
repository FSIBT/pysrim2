"""Parsers for the tabular SRIM output files and the Results aggregate"""

import os
import re

from ._common import (
    SRIM_Output,
    _element_names,
    _find_output,
    _header_symbols,
    _to_float,
    double_regex,
)
from .collision import Collision
from .events import EXYZ, Backscat, Sputter, Transmit
from .range3d import Range3D

_SUMMARY_LINES = {
    rb"Average": ("mean_depth_A", "straggle_depth_A"),
    rb"Lateral": ("lateral_range_A", "lateral_straggle_A"),
    rb"Radial": ("radial_range_A", "radial_straggle_A"),
}
_SUMMARY_KEYS = [k for pair in _SUMMARY_LINES.values() for k in pair] + ["skewness", "kurtosis"]


_LAZY_OUTPUTS = (
    ("range3d", Range3D, "RANGE_3D.txt"),
    ("exyz", EXYZ, "EXYZ.txt"),
    ("backscat", Backscat, "BACKSCAT.txt"),
    ("transmit", Transmit, "TRANSMIT.txt"),
    ("sputter", Sputter, "SPUTTER.txt"),
    ("collision", Collision, "COLLISON.txt"),
)


class Results:
    """Gathers all results from folder

    Parameters
    ----------
    directory : :obj:`str`
        directory to look for TRIM calculations

    Notes
    -----
    The files of the large per-ion outputs (``RANGE_3D.txt``, ``EXYZ.txt``,
    ``BACKSCAT.txt``, ``TRANSMIT.txt``, ``SPUTTER.txt``, ``COLLISON.txt``) are read at
    first access of the attribute (``results.range3d`` ...), not when ``Results`` is
    created; the result is cached. A broken file therefore raises (or warns) then, and a
    file changed afterwards is not re-read.

    Files that are looked for:
      - ``IONIZ.txt`` handled by :class:`srim.output.Ioniz`
      - ``VACANCY.txt`` handled by :class:`srim.output.Vacancy`
      - ``NOVAC.txt`` handled by :class:`srim.output.NoVacancy`
      - ``E2RECOIL.txt`` handled by :class:`srim.output.EnergyToRecoils`
      - ``PHONON.txt`` handled by :class:`srim.output.Phonons`
      - ``RANGE.txt`` handled by :class:`srim.output.Range`
      - ``RANGE_3D.txt`` handled by :class:`srim.output.Range3D`
        (``range3d`` is ``None`` if the file is absent and parsed on first access; the
        ``SRIM Outputs`` subfolder is searched too)
      - ``EXYZ.txt`` handled by :class:`srim.output.EXYZ`, ``BACKSCAT.txt`` by
        :class:`srim.output.Backscat`, ``TRANSMIT.txt`` by :class:`srim.output.Transmit`,
        ``SPUTTER.txt`` by :class:`srim.output.Sputter` (``exyz``, ``backscat``,
        ``transmit``, ``sputter``; each ``None`` if the file is absent)
      - ``COLLISON.txt`` handled by :class:`srim.output.Collision` (``collision``;
        ``None`` if absent; opening it reads only the header; ``collision.df`` streams the file)
    """

    def __init__(self, directory):
        """Retrives all the calculation files in a given directory"""
        self.ioniz = Ioniz(directory)
        self.vacancy = Vacancy(directory)

        try:
            self.novac = NoVacancy(directory)
        except ValueError:
            self.novac = None

        self.etorecoils = EnergyToRecoils(directory)
        self.phonons = Phonons(directory)
        self.range = Range(directory)

        # SRIM writes some files into "SRIM Outputs" in some installs. The main directory
        # takes precedence: it holds copies made by TRIM.copy_output_files or older installs,
        # and a fresh run removes stale files from both places.
        # The (possibly large) event files are parsed on first access, then cached.
        self._lazy = {}
        for key, parser, filename in _LAZY_OUTPUTS:
            self._lazy[key] = [parser, _find_output(directory, filename), filename, None]

    def _get_lazy(self, key):
        entry = self._lazy[key]
        parser, folder, filename, parsed = entry
        if parsed is None and folder is not None:
            parsed = entry[3] = parser(folder, filename)
        return parsed

    @property
    def range3d(self):
        """:class:`srim.output.Range3D`, parsed on first access; ``None`` if absent"""
        return self._get_lazy("range3d")

    @property
    def collision(self):
        """:class:`srim.output.Collision`, created on first access; ``None`` if absent"""
        return self._get_lazy("collision")

    @property
    def exyz(self):
        """:class:`srim.output.EXYZ`, parsed on first access; ``None`` if absent"""
        return self._get_lazy("exyz")

    @property
    def backscat(self):
        """:class:`srim.output.Backscat`, parsed on first access; ``None`` if absent"""
        return self._get_lazy("backscat")

    @property
    def transmit(self):
        """:class:`srim.output.Transmit`, parsed on first access; ``None`` if absent"""
        return self._get_lazy("transmit")

    @property
    def sputter(self):
        """:class:`srim.output.Sputter`, parsed on first access; ``None`` if absent"""
        return self._get_lazy("sputter")

    def to_dataframes(self):
        """Depth-profile outputs as :class:`pandas.DataFrame` objects

        Returns
        -------
        :obj:`dict`
            Keys ``ioniz``, ``vacancy``, ``novac``, ``e2recoil``, ``phonon``,
            ``range``, ``range3d``, ``exyz``, ``backscat``, ``transmit``, ``sputter``,
            ``collision`` (one row per ion collision and cascade recoil; can be large);
            outputs that are missing (e.g. ``novac`` for
            Kinchin-Pease calculations) are omitted.
        """
        outputs = {
            "ioniz": self.ioniz,
            "vacancy": self.vacancy,
            "novac": self.novac,
            "e2recoil": self.etorecoils,
            "phonon": self.phonons,
            "range": self.range,
            "range3d": self.range3d,
            "exyz": self.exyz,
            "backscat": self.backscat,
            "transmit": self.transmit,
            "sputter": self.sputter,
            "collision": self.collision,
        }
        return {key: out.df for key, out in outputs.items() if out is not None}


class Ioniz(SRIM_Output):
    """``IONIZ.txt`` Ionization by ions and depth. Includes header information about calculation

    Parameters
    ----------
    directory : :obj:`str`
         directory of calculation
    filename : :obj:`str`, optional
         filename for Ioniz. Default ``IONIZ.txt``

    Attributes
    ----------
    df : :class:`pandas.DataFrame`
        One row per depth bin. Columns:
          - ``depth_A``: depth of bin [Ang]
          - ``ions_eV_per_A_per_ion``: ionization by ions [eV/(Angstrom-Ion)]
          - ``recoils_eV_per_A_per_ion``: ionization by recoils [eV/(Angstrom-Ion)]
    metadata : :obj:`dict`
        ``ion``, ``energy_eV``, ``num_ions``, ``source``
    """

    def __init__(self, directory, filename="IONIZ.txt"):
        with open(os.path.join(directory, filename), "rb") as f:
            output = f.read()
            ion = self._read_ion(output)
            num_ions = self._read_num_ions(output)
            data = self._read_table(output)

        self._source = os.path.join(directory, filename)
        self._ion = ion
        self._num_ions = num_ions
        self._depth = data[:, 0]
        self._ions = data[:, 1]
        self._recoils = data[:, 2]

    @property
    def ion(self):
        """Ion used in SRIM calculation

        **mass** could be wrong
        """
        return self._ion

    @property
    def num_ions(self):
        """Number of Ions in SRIM simulation"""
        return self._num_ions

    @property
    def depth(self):
        """Depth [Ang] of bins in SRIM Calculation"""
        return self._depth

    @property
    def ions(self):
        """Ionization energy [eV/(Angstrom Ion)] lost to electronic stopping
        in incident ions"""
        return self._ions

    @property
    def recoils(self):
        """Ionization energy [eV/(Angstrom Ion)] lost to electronic stopping
        in recoil ions"""
        return self._recoils

    def _dataframe_columns(self):
        return [
            ("depth_A", self._depth),
            ("ions_eV_per_A_per_ion", self._ions),
            ("recoils_eV_per_A_per_ion", self._recoils),
        ]


class Vacancy(SRIM_Output):
    """``VACANCY.txt`` Table of the final distribution of vacancies vs depth

    Parameters
    ----------
    directory : :obj:`str`
         directory of calculation
    filename : :obj:`str`, optional
         filename for Vacancy. Default ``VACANCY.txt``

    Attributes
    ----------
    df : :class:`pandas.DataFrame`
        One row per depth bin. Columns:
          - ``depth_A``: depth of bin [Ang]
          - ``knock_ons_per_A_per_ion``: primary knock-ons by the ion
            [knock-ons/(Angstrom-Ion)]
          - ``vacancies_<element>_per_A_per_ion``: vacancies of each target element,
            in file order (a repeated element becomes ``Si``, ``Si_2``, ...)
    metadata : :obj:`dict`
        ``ion``, ``energy_eV``, ``num_ions``, ``source``
    """

    def __init__(self, directory, filename="VACANCY.txt"):
        with open(os.path.join(directory, filename), "rb") as f:
            output = f.read()
            ion = self._read_ion(output)
            num_ions = self._read_num_ions(output)
            data = self._read_table(output)
            symbols = _header_symbols(output, rb"^\s*DEPTH((?:\s+[A-Z][a-z]?)+)\s*$")

        self._source = os.path.join(directory, filename)
        self._ion = ion
        self._num_ions = num_ions
        self._depth = data[:, 0]
        self._ion_knock_ons = data[:, 1]
        self._vacancies = data[:, 2:]
        self._element_names = _element_names(symbols[1:], self._vacancies.shape[1])

    @property
    def ion(self):
        """Ion used in SRIM calculation

        **mass** could be wrong
        """
        return self._ion

    @property
    def num_ions(self):
        """Number of Ions in SRIM simulation"""
        return self._num_ions

    @property
    def depth(self):
        """Depth [Ang] of bins in SRIM Calculation"""
        return self._depth

    @property
    def knock_ons(self):
        """Primary knock-ons produced by the ion [knock-ons/(Angstrom-Ion)]"""
        return self._ion_knock_ons

    @property
    def vacancies(self):
        """Vacancies [Vacancies/(Angstrom-Ion)] produced of element in layer"""
        return self._vacancies

    def _dataframe_columns(self):
        return [
            ("depth_A", self._depth),
            ("knock_ons_per_A_per_ion", self._ion_knock_ons),
            ([f"vacancies_{e}_per_A_per_ion" for e in self._element_names], self._vacancies),
        ]


class NoVacancy(SRIM_Output):
    """``NOVAC.txt`` Table of Replacement Collisions

    Parameters
    ----------
    directory : :obj:`str`
         directory of calculation
    filename : :obj:`str`, optional
         filename for NoVacancy. Default ``NOVAC.txt``

    Attributes
    ----------
    df : :class:`pandas.DataFrame`
        One row per depth bin. Columns:
          - ``depth_A``: depth of bin [Ang]
          - ``replacements_per_A_per_ion``: replacement collisions [Number/(Angstrom-Ion)]
    metadata : :obj:`dict`
        ``ion``, ``energy_eV``, ``num_ions``, ``source``
    """

    def __init__(self, directory, filename="NOVAC.txt"):
        with open(os.path.join(directory, filename), "rb") as f:
            output = f.read()

            # Check if it is KP calculation
            if re.search(b"Recoil/Damage Calculations made with Kinchin-Pease Estimates", output):
                raise ValueError("NOVAC has no data for KP calculations")

            ion = self._read_ion(output)
            num_ions = self._read_num_ions(output)
            data = self._read_table(output)

        self._source = os.path.join(directory, filename)
        self._ion = ion
        self._num_ions = num_ions
        self._depth = data[:, 0]
        self._number = data[:, 1]

    @property
    def ion(self):
        """Ion used in SRIM calculation

        **mass** could be wrong
        """
        return self._ion

    @property
    def num_ions(self):
        """Number of Ions in SRIM simulation"""
        return self._num_ions

    @property
    def depth(self):
        """Depth [Ang] of bins in SRIM Calculation"""
        return self._depth

    @property
    def number(self):
        """Replacement Collisions [Number/(Angstrom-Ion)]"""
        return self._number

    def _dataframe_columns(self):
        return [
            ("depth_A", self._depth),
            ("replacements_per_A_per_ion", self._number),
        ]


class EnergyToRecoils(SRIM_Output):
    """``E2RECOIL.txt`` Energy transfered to atoms through binary collision

    Parameters
    ----------
    directory : :obj:`str`
         directory of calculation
    filename : :obj:`str`, optional
         filename for EnergyToRecoils. Default ``E2RECOIL.txt``

    Attributes
    ----------
    df : :class:`pandas.DataFrame`
        One row per depth bin. Columns:
          - ``depth_A``: depth of bin [Ang]
          - ``ions_eV_per_A_per_ion``: energy from ions [eV/(Angstrom-Ion)]
          - ``absorbed_<element>_eV_per_A_per_ion``: energy absorbed by each target
            element [eV/(Angstrom-Ion)]
    metadata : :obj:`dict`
        ``ion``, ``energy_eV``, ``num_ions``, ``source``
    """

    def __init__(self, directory, filename="E2RECOIL.txt"):
        with open(os.path.join(directory, filename), "rb") as f:
            output = f.read()
            ion = self._read_ion(output)
            num_ions = self._read_num_ions(output)
            data = self._read_table(output)
            symbols = _header_symbols(
                output, rb"^\s*\(Ang\.\)\s+IONS((?:\s+by\s+[A-Z][a-z]?)+)\s*$"
            )

        self._source = os.path.join(directory, filename)
        self._ion = ion
        self._num_ions = num_ions
        self._depth = data[:, 0]
        self._ions = data[:, 1]
        self._recoils = data[:, 2:]
        self._element_names = _element_names(symbols, self._recoils.shape[1])

    @property
    def ion(self):
        """Ion used in SRIM calculation

        **mass** could be wrong
        """
        return self._ion

    @property
    def num_ions(self):
        """Number of Ions in SRIM simulation"""
        return self._num_ions

    @property
    def depth(self):
        """Depth [Ang] of bins in SRIM Calculation"""
        return self._depth

    @property
    def ions(self):
        """Energy [eV/(Angstrom-Ion)] transfered to material through ion collisions"""
        return self._ions

    @property
    def absorbed(self):
        """Energy [eV/(Angstrom-Ion)] absorbed from collisions with Atom

        TODO: fix terminology
        """
        return self._recoils

    def _dataframe_columns(self):
        return [
            ("depth_A", self._depth),
            ("ions_eV_per_A_per_ion", self._ions),
            ([f"absorbed_{e}_eV_per_A_per_ion" for e in self._element_names], self._recoils),
        ]


class Phonons(SRIM_Output):
    """``PHONON.txt``  Distribution of Phonons

    Parameters
    ----------
    directory : :obj:`str`
         directory of calculation
    filename : :obj:`str`, optional
         filename for Phonons. Default ``PHONON.txt``

    Attributes
    ----------
    df : :class:`pandas.DataFrame`
        One row per depth bin. Columns:
          - ``depth_A``: depth of bin [Ang]
          - ``ions_phonons_per_A_per_ion``: phonons from ions [Phonons/(Angstrom-Ion)]
          - ``recoils_phonons_per_A_per_ion``: phonons from recoils [Phonons/(Angstrom-Ion)]
    metadata : :obj:`dict`
        ``ion``, ``energy_eV``, ``num_ions``, ``source``
    """

    def __init__(self, directory, filename="PHONON.txt"):
        with open(os.path.join(directory, filename), "rb") as f:
            output = f.read()
            ion = self._read_ion(output)
            num_ions = self._read_num_ions(output)
            data = self._read_table(output)

        self._source = os.path.join(directory, filename)
        self._ion = ion
        self._num_ions = num_ions
        self._depth = data[:, 0]
        self._ions = data[:, 1]
        self._recoils = data[:, 2]

    @property
    def ion(self):
        """Ion used in SRIM calculation

        **mass** could be wrong
        """
        return self._ion

    @property
    def num_ions(self):
        """Number of Ions in SRIM simulation"""
        return self._num_ions

    @property
    def depth(self):
        """Depth [Ang] of bins in SRIM Calculation"""
        return self._depth

    @property
    def ions(self):
        """Number of phonons [Phonons/(Angstrom Ion)] created from ions collisions"""
        return self._ions

    @property
    def recoils(self):
        """Number of phonons [Phonons/(Angstrom Ion)] created from recoils
        resulting from ion collisions"""
        return self._recoils

    def _dataframe_columns(self):
        return [
            ("depth_A", self._depth),
            ("ions_phonons_per_A_per_ion", self._ions),
            ("recoils_phonons_per_A_per_ion", self._recoils),
        ]


class Range(SRIM_Output):
    """``RANGE.txt`` Table of the final distribution of the ions, and any recoiling target atoms

    Parameters
    ----------
    directory : :obj:`str`
         directory of calculation
    filename : :obj:`str`, optional
         filename for Range. Default ``RANGE.txt``

    Attributes
    ----------
    df : :class:`pandas.DataFrame`
        One row per depth bin. Columns:
          - ``depth_A``: depth of bin [Ang]
          - ``ions_atoms_per_cm3_per_atoms_per_cm2``: final ion distribution
            [(Atoms/cm3)/(Atoms/cm2)]
          - ``<element>_atoms_per_cm3_per_atoms_per_cm2``: target atoms of each
            element, in file order [(Atoms/cm3)/(Atoms/cm2)]
    metadata : :obj:`dict`
        ``ion``, ``energy_eV``, ``num_ions``, ``source``
    """

    def __init__(self, directory, filename="RANGE.txt"):
        with open(os.path.join(directory, filename), "rb") as f:
            output = f.read()
            ion = self._read_ion(output)
            num_ions = self._read_num_ions(output)
            data = self._read_table(output)
            symbols = _header_symbols(output, rb"^\s*DEPTH((?:\s+[A-Z][a-z]?)+)\s*$")

        self._source = os.path.join(directory, filename)
        self._ion = ion
        self._num_ions = num_ions
        self._depth = data[:, 0]
        self._ions = data[:, 1]
        self._elements = data[:, 2:]
        self._element_names = _element_names(symbols[1:], self._elements.shape[1])
        self._summary = self._read_summary(output)

    @staticmethod
    def _read_summary(output):
        """Range and straggling values from the header (``nan`` if a line is absent)"""
        nan = float("nan")
        summary = dict.fromkeys(_SUMMARY_KEYS, nan)
        for label, (range_key, straggle_key) in _SUMMARY_LINES.items():
            match = re.search(
                rb"Ion\s+" + label + rb"\s+Range\s*=\s*(" + double_regex.encode() + rb")\s*A"
                rb"\s+Straggling\s*=\s*(" + double_regex.encode() + rb")\s*A",
                output,
            )
            if match:
                summary[range_key] = _to_float(match.group(1))
                summary[straggle_key] = _to_float(match.group(2))
        for key, label in (("skewness", rb"Skewne-"), ("kurtosis", rb"Kurtosis")):
            match = re.search(
                rb"Range\s+" + label + rb"\s*=\s*(" + double_regex.encode() + rb")", output
            )
            if match:
                summary[key] = _to_float(match.group(1))
        return summary

    @property
    def summary(self):
        """:obj:`dict` of the statistics SRIM prints in the file header [Ang]

        Keys ``mean_depth_A`` / ``straggle_depth_A`` (projected range and
        straggling), ``lateral_range_A`` / ``lateral_straggle_A``,
        ``radial_range_A`` / ``radial_straggle_A``, ``skewness`` and
        ``kurtosis`` (of the depth distribution). Values not present in the
        file are ``nan``. See :func:`srim.analysis.implant_summary` for how
        to compute the same quantities from :class:`srim.output.Range3D`.
        """
        return dict(self._summary)

    @property
    def ion(self):
        """Ion used in SRIM calculation

        **mass** could be wrong
        """
        return self._ion

    @property
    def num_ions(self):
        """Number of Ions in SRIM simulation"""
        return self._num_ions

    @property
    def depth(self):
        """Depth [Ang] of bins in SRIM Calculation"""
        return self._depth

    @property
    def ions(self):
        """Ion final distribution [(Atoms/cm3)/(Atoms/cm2)]"""
        return self._ions

    @property
    def elements(self):
        """Per elements [(Atoms/cm3)/(Atoms/cm2)] distribution of each element"""
        return self._elements

    def _dataframe_columns(self):
        return [
            ("depth_A", self._depth),
            ("ions_atoms_per_cm3_per_atoms_per_cm2", self._ions),
            ([f"{e}_atoms_per_cm3_per_atoms_per_cm2" for e in self._element_names], self._elements),
        ]
