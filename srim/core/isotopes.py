"""Natural isotopes of the elements, from ``srim.data.isotopes.yaml``"""

import functools
import os
import re
from dataclasses import dataclass

import yaml

import srim

from .elementdb import ElementDB


@dataclass(frozen=True)
class Isotope:
    """A naturally occurring isotope of an element

    Parameters
    ----------
    symbol : :obj:`str`
        Element symbol, for example ``"Si"``.
    mass_number : :obj:`int`
        Mass number, the ``28`` of silicon-28.
    mass_amu : :obj:`float`
        Atomic mass [amu].
    abundance_percent : :obj:`float`
        Natural abundance [%].
    """

    symbol: str
    mass_number: int
    mass_amu: float
    abundance_percent: float


@functools.cache
def _table():
    """Natural isotopes of each element symbol, most abundant first"""
    path = os.path.join(srim.__path__[0], "data", "isotopes.yaml")
    with open(path) as f:
        data = yaml.load(f, Loader=yaml.SafeLoader)
    table = {}
    for symbol, entries in data.items():
        isotopes = [
            Isotope(
                symbol=symbol,
                mass_number=int(entry["A"]),
                mass_amu=float(entry["mass_amu"]),
                abundance_percent=float(entry["abundance_percent"]),
            )
            for entry in entries
        ]
        table[symbol] = tuple(sorted(isotopes, key=lambda i: i.abundance_percent, reverse=True))
    return table


def _check_symbol(symbol):
    """Raises ValueError unless ``symbol`` is an element symbol such as ``"Si"``"""
    if not isinstance(symbol, str) or not re.fullmatch(r"[A-Z][a-z]?", symbol):
        raise ValueError(f"{symbol!r} is not an element symbol")
    try:
        ElementDB.lookup(symbol)
    except KeyError:
        raise ValueError(f"{symbol!r} is not an element symbol") from None


def stable_isotopes(symbol):
    """Naturally occurring isotopes of an element

    Parameters
    ----------
    symbol : :obj:`str`
        Element symbol, for example ``"Si"``.

    Returns
    -------
    :obj:`tuple` of :class:`Isotope`
        Most abundant first. Empty for an element with no natural isotopes.

    Raises
    ------
    ValueError
        If ``symbol`` is not an element symbol.
    """
    _check_symbol(symbol)
    return _table().get(symbol, ())


def isotope_label(isotope):
    """Label of an isotope, for example ``"Si-28"``"""
    return f"{isotope.symbol}-{isotope.mass_number}"


def find_isotope(symbol, mass_amu):
    """The natural isotope of an element whose mass matches, or None

    A match is a mass within 1e-4 amu; the closest one is returned.
    """
    matches = [i for i in stable_isotopes(symbol) if abs(i.mass_amu - mass_amu) <= 1e-4]
    return min(matches, key=lambda i: abs(i.mass_amu - mass_amu), default=None)
