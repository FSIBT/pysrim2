"""Density functions for the ``Function`` beam distribution tests.

Referenced as ``python:beam_funcs:<name>``; pytest's prepend import mode puts this directory on
``sys.path`` (there is no ``__init__.py`` here on purpose).
"""

import numpy as np


def ramp(x):
    """Density proportional to x (vectorised)."""
    return np.asarray(x, dtype=float)


def flat(x):
    """Constant density 1 (a scalar, so the caller must broadcast it)."""
    return 1.0


def bad(x):
    """A negative density, which must be refused."""
    return -1.0
