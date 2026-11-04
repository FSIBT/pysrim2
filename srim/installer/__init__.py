"""Download, verify and install SRIM outside the PyPI package."""

from srim.installer import container as _container  # noqa: F401  (registers runtime, image)
from srim.installer import skill as _skill  # noqa: F401  (registers skill)
