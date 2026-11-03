from importlib.metadata import version as _version

__version__ = _version("pysrim2")

from .core import Element, ElementDB, Ion, Layer, Material, Target
from .srim import SR, TRIM
