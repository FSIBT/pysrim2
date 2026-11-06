"""Batched runs and scans built on :class:`srim.spec.RunSpec`."""

from .batches import BatchInfo, MergedResults, derive_seed, run_batches

__all__ = ["BatchInfo", "MergedResults", "derive_seed", "run_batches"]
