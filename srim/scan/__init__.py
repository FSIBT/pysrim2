"""Batched runs and scans built on :class:`srim.spec.RunSpec`."""

from .batches import BatchInfo, MergedResults, derive_seed, run_batches
from .cache import cache_key, clear_cache
from .executor import run_scan
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
    "cache_key",
    "clear_cache",
    "derive_seed",
    "run_batches",
    "run_scan",
    "spec_hash",
]
