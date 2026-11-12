"""Common materials and chemical formulas, from ``srim.data.compounds.yaml``"""

import functools
import os
from dataclasses import dataclass

import yaml

import srim

from .material import Material


@dataclass(frozen=True)
class Compound:
    """A material with a chemical formula, as offered by the desktop app

    Parameters
    ----------
    name : :obj:`str`
        Name of the material, for example ``"Kapton"``.
    formula : :obj:`str`
        Chemical formula, for example ``"C22H10N2O5"``.
    density_g_cm3 : :obj:`float`
        Density [g/cm3].
    phase : :obj:`int`
        0 for a solid or liquid, 1 for a gas.
    """

    name: str
    formula: str
    density_g_cm3: float
    phase: int = 0


@functools.cache
def _compounds():
    path = os.path.join(srim.__path__[0], "data", "compounds.yaml")
    with open(path) as f:
        data = yaml.load(f, Loader=yaml.SafeLoader)
    return tuple(Compound(**entry) for entry in data)


def predefined_compounds():
    """The common materials, in the order of the data file"""
    return _compounds()


def formula_elements(formula):
    """Elements of a chemical formula, with their ratios

    Parameters
    ----------
    formula : :obj:`str`
        Chemical formula such as ``"SiO2"`` or ``"N78.08O20.95Ar0.93"``. A ratio that is
        not written is 1.0.

    Returns
    -------
    :obj:`list` of (:obj:`str`, :obj:`float`)
        Element symbols and ratios in formula order.

    Raises
    ------
    ValueError
        If the formula is malformed, names an unknown element or repeats an element.
    """
    try:
        elements = Material._formula_to_elements(formula)
    except KeyError as error:
        raise ValueError(f"chemical formula {formula!r} has an unknown element") from error
    return [(element.symbol, ratio) for element, ratio in elements.items()]
