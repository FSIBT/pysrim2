"""Parsers for the tabular SRIM output files and the Results aggregate"""

import os
import re

from ._common import (
    SRIM_Output,
)


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


class Ioniz(SRIM_Output):
    """``IONIZ.txt`` Ionization by ions and depth. Includes header information about calculation

    Parameters
    ----------
    directory : :obj:`str`
         directory of calculation
    filename : :obj:`str`, optional
         filename for Ioniz. Default ``IONIZ.txt``
    """

    def __init__(self, directory, filename="IONIZ.txt"):
        with open(os.path.join(directory, filename), "rb") as f:
            output = f.read()
            ion = self._read_ion(output)
            num_ions = self._read_num_ions(output)
            data = self._read_table(output)

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


class Vacancy(SRIM_Output):
    """``VACANCY.txt`` Table of the final distribution of vacancies vs depth

    Parameters
    ----------
    directory : :obj:`str`
         directory of calculation
    filename : :obj:`str`, optional
         filename for Vacancy. Default ``VACANCY.txt``
    """

    def __init__(self, directory, filename="VACANCY.txt"):
        with open(os.path.join(directory, filename), "rb") as f:
            output = f.read()
            ion = self._read_ion(output)
            num_ions = self._read_num_ions(output)
            data = self._read_table(output)

        self._ion = ion
        self._num_ions = num_ions
        self._depth = data[:, 0]
        self._ion_knock_ons = data[:, 1]
        self._vacancies = data[:, 2:]

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
        """Vacancies produced [Vacancies/(Angstrom-Ion) by ion]"""
        return self._ion_knock_ons

    @property
    def vacancies(self):
        """Vacancies [Vacancies/(Angstrom-Ion)] produced of element in layer"""
        return self._vacancies


class NoVacancy(SRIM_Output):
    """``NOVAC.txt`` Table of Replacement Collisions

    Parameters
    ----------
    directory : :obj:`str`
         directory of calculation
    filename : :obj:`str`, optional
         filename for NoVacancy. Default ``NOVAC.txt``
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


class EnergyToRecoils(SRIM_Output):
    """``E2RECOIL.txt`` Energy transfered to atoms through binary collision

    Parameters
    ----------
    directory : :obj:`str`
         directory of calculation
    filename : :obj:`str`, optional
         filename for EnergyToRecoils. Default ``E2RECOIL.txt``
    """

    def __init__(self, directory, filename="E2RECOIL.txt"):
        with open(os.path.join(directory, filename), "rb") as f:
            output = f.read()
            ion = self._read_ion(output)
            num_ions = self._read_num_ions(output)
            data = self._read_table(output)

        self._ion = ion
        self._num_ions = num_ions
        self._depth = data[:, 0]
        self._ions = data[:, 1]
        self._recoils = data[:, 2:]

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


class Phonons(SRIM_Output):
    """``PHONON.txt``  Distribution of Phonons

    Parameters
    ----------
    directory : :obj:`str`
         directory of calculation
    filename : :obj:`str`, optional
         filename for Phonons. Default ``PHONON.txt``
    """

    def __init__(self, directory, filename="PHONON.txt"):
        with open(os.path.join(directory, filename), "rb") as f:
            output = f.read()
            ion = self._read_ion(output)
            num_ions = self._read_num_ions(output)
            data = self._read_table(output)

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


class Range(SRIM_Output):
    """``RANGE.txt`` Table of the final distribution of the ions, and any recoiling target atoms

    Parameters
    ----------
    directory : :obj:`str`
         directory of calculation
    filename : :obj:`str`, optional
         filename for Range. Default ``RANGE.txt``
    """

    def __init__(self, directory, filename="RANGE.txt"):
        with open(os.path.join(directory, filename), "rb") as f:
            output = f.read()
            ion = self._read_ion(output)
            num_ions = self._read_num_ions(output)
            data = self._read_table(output)

        self._ion = ion
        self._num_ions = num_ions
        self._depth = data[:, 0]
        self._ions = data[:, 1]
        self._elements = data[:, 2:]

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
