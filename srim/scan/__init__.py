"""Batched runs and scans built on :class:`srim.spec.RunSpec`."""

from .batches import BatchInfo, MergedResults, derive_seed, run_batches
from .precision import Precision
from .result import ScanResult
from .spec import Scan, ScanPoint, spec_hash

__all__ = [
    "BatchInfo",
    "MergedResults",
    "Precision",
    "Scan",
    "ScanPoint",
    "ScanResult",
    "derive_seed",
    "run_batches",
    "spec_hash",
]
