"""Parsers for the tabular SRIM output files and the Results aggregate"""

import os
import re

from ._common import (
    SRIM_Output,
    _element_names,
    _header_symbols,
)
from .range3d import Range3D


class Results:
    """Gathers all results from folder

    Parameters
    ----------
    directory : :obj:`str`
        directory to look for TRIM calculations

    Notes
    -----
    Files that are looked for:
      - ``IONIZ.txt`` handled by :class:`srim.output.Ioniz`
      - ``VACANCY.txt`` handled by :class:`srim.output.Vacancy`
      - ``NOVAC.txt`` handled by :class:`srim.output.NoVacancy`
      - ``E2RECOIL.txt`` handled by :class:`srim.output.EnergyToRecoils`
      - ``PHONON.txt`` handled by :class:`srim.output.Phonons`
      - ``RANGE.txt`` handled by :class:`srim.output.Range`
      - ``RANGE_3D.txt`` handled by :class:`srim.output.Range3D`
        (``range3d`` is ``None`` if the file is absent)
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

        try:
            self.range3d = Range3D(directory)
        except FileNotFoundError:
            self.range3d = None

    def to_dataframes(self):
        """Depth-profile outputs as :class:`pandas.DataFrame` objects

        Returns
        -------
        :obj:`dict`
            Keys ``ioniz``, ``vacancy``, ``novac``, ``e2recoil``, ``phonon``,
            ``range``, ``range3d``; outputs that are missing (e.g. ``novac`` for
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
