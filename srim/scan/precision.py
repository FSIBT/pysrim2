"""Precision targets: run each point until an extracted value is known to a tolerance.

:class:`Precision` only describes the goal (the estimator and the batch scheduling live in
the scan executor). ``target`` names the extracted value the tolerance applies to, e.g.
``"sputter_yield"``; whether that column exists can only be known after a run, so it is
not checked here.
"""

import math
from dataclasses import dataclass
from numbers import Integral, Real

from ..spec import SpecError


def _positive_real(value, key):
    if isinstance(value, bool) or not isinstance(value, Real):
        raise SpecError(f"{key} must be a number, got {value!r}")
    if not math.isfinite(value) or not value > 0:
        raise SpecError(f"{key} must be a finite number > 0, got {value!r}")
    return float(value)


def _whole(value, key):
    if isinstance(value, bool) or not isinstance(value, Integral):
        raise SpecError(f"{key} must be an integer, got {value!r}")
    if value < 1:
        raise SpecError(f"{key} must be >= 1, got {value!r}")
    return int(value)


@dataclass(frozen=True)
class Precision:
    """Stop a point once ``target`` reaches a precision, within ``min_ions``..``max_ions``.

    Exactly one of ``rel`` (relative error of the value) and ``abs`` (absolute error) is
    given. ``batch_ions`` is the number of ions per batch. Raises :class:`SpecError`
    naming the key at fault.
    """

    target: str
    rel: float | None = None
    abs: float | None = None
    min_ions: int = 1000
    max_ions: int = 100_000
    batch_ions: int = 1000

    def __post_init__(self):
        if not isinstance(self.target, str) or not self.target:
            raise SpecError(
                f"precision.target must be the name of an extracted value, got {self.target!r}"
            )
        if (self.rel is None) == (self.abs is None):
            raise SpecError("precision: give exactly one of rel (relative) or abs (absolute)")
        if self.rel is not None:
            object.__setattr__(self, "rel", _positive_real(self.rel, "precision.rel"))
        if self.abs is not None:
            object.__setattr__(self, "abs", _positive_real(self.abs, "precision.abs"))
        object.__setattr__(self, "min_ions", _whole(self.min_ions, "precision.min_ions"))
        object.__setattr__(self, "max_ions", _whole(self.max_ions, "precision.max_ions"))
        object.__setattr__(self, "batch_ions", _whole(self.batch_ions, "precision.batch_ions"))
        if self.min_ions > self.max_ions:
            raise SpecError(
                f"precision.min_ions must be <= precision.max_ions, got "
                f"{self.min_ions} > {self.max_ions}"
            )

    def to_dict(self):
        """Plain data as stored under ``precision:`` in a scan section (unset keys omitted)."""
        out = {"target": self.target}
        if self.rel is not None:
            out["rel"] = self.rel
        if self.abs is not None:
            out["abs"] = self.abs
        out.update(min_ions=self.min_ions, max_ions=self.max_ions, batch_ions=self.batch_ions)
        return out

    @classmethod
    def from_dict(cls, data, prefix="precision."):
        """Build from a mapping; ``prefix`` is put in front of the key names in errors."""
        if not isinstance(data, dict):
            raise SpecError(f"{prefix.rstrip('.')} must be a mapping, got {data!r}")
        allowed = ("target", "rel", "abs", "min_ions", "max_ions", "batch_ions")
        for key in data:
            if key not in allowed:
                raise SpecError(f"unknown key '{prefix}{key}' (allowed: {', '.join(allowed)})")
        if "target" not in data:
            raise SpecError(f"missing required key '{prefix}target'")
        try:
            return cls(**data)
        except SpecError as exc:
            raise SpecError(f"{prefix}{exc}") from None
