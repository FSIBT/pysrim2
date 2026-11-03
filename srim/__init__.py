from importlib.metadata import version as _version

__version__ = _version("pysrim2")

from .srim import TRIM, SR

from .core import ElementDB, Element, Material, Ion, Layer, Target
