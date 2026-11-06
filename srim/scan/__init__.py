"""Batched runs and scans built on :class:`srim.spec.RunSpec`."""

from .batches import BatchInfo, MergedResults, derive_seed, run_batches
from .precision import Precision

__all__ = ["BatchInfo", "MergedResults", "Precision", "derive_seed", "run_batches"]
