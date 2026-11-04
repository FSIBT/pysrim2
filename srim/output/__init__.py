"""Read output files of SRIM simulation

TODO: Read header information
"""

from ._common import (
    SRIM_Output,
    SRIMOutputParseError,
    _prefix_factor,
    _to_float,
    double_regex,
    energy_prefixes,
    int_regex,
    length_prefixes,
    symbol_regex,
)
from .collision import Collision, buffered_findall
from .events import Backscat, Sputter, Transmit
from .range3d import Range3D
from .sr import SRResults
from .tables import EnergyToRecoils, Ioniz, NoVacancy, Phonons, Range, Results, Vacancy

__all__ = [
    "Backscat",
    "Collision",
    "EnergyToRecoils",
    "Ioniz",
    "NoVacancy",
    "Phonons",
    "Range",
    "Range3D",
    "Results",
    "SRIM_Output",
    "SRIMOutputParseError",
    "SRResults",
    "Sputter",
    "Transmit",
    "Vacancy",
    "buffered_findall",
]
