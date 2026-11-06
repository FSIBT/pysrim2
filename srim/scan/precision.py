"""Precision targets: run each point until an extracted value is known to a tolerance.

:class:`Precision` describes the goal; the executor (:func:`srim.scan.run_scan`) runs the
batches. ``target`` names the extracted value the tolerance applies to, e.g.
``"sputter_yield"``; whether that column exists can only be known after a run, so it is
not checked here.

The estimator ("batch means"). A point runs as batches of ions, each with its own seed.
The reported value of every extracted column is the extractor applied to all batches of
the point together. Its uncertainty ``<name>_err`` is the standard error across the
batches: each batch is evaluated alone, and with ``w_i`` the ions of batch ``i`` and
``xbar`` the ion-weighted mean of the batch values ``x_i`` (see :func:`batch_error`)::

    err = sqrt(sum_i w_i**2 * (x_i - xbar)**2) / sum_i w_i * sqrt(n / (n - 1))

With equal weights this is ``std(x, ddof=1) / sqrt(n)``. It needs two batches; with one
batch the error is NaN. Only numeric columns get an error.

The scheduler (in the executor). A point starts with :func:`initial_batches` batches. When
all of its batches are done, the target's value and error are compared with the goal
(:func:`goal_of`, :func:`is_met`). If the goal is not met and the ion maximum is not
reached, :func:`batches_to_add` says how many batches to add: enough for the error to
scale down to the goal (the error goes down with the square root of the ions), with a
10 % margin, and never more than the maximum allows. The rule only ever adds ions, so the
number of rounds is bounded by ``max_ions / batch_ions``.
"""

import math
from dataclasses import dataclass
from numbers import Integral, Real

import numpy as np

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


# -- estimator and scheduling rules (used by the executor; pure functions) ---------------


def batch_error(values, weights) -> float:
    """Standard error of the ion-weighted mean of the batch values ``values``.

    ``weights`` are the ions of each batch (positive). Returns NaN for fewer than two
    batches or a non-finite value. Raises :class:`ValueError` for a weight that is not
    positive or lists of different lengths. See the module docstring for the formula.
    """
    x = np.asarray(values, dtype=float)
    w = np.asarray(weights, dtype=float)
    if x.ndim != 1 or x.shape != w.shape:
        raise ValueError("values and weights must be lists of the same length")
    if w.size and not np.all(w > 0):
        raise ValueError(f"weights must be positive, got {weights!r}")
    n = x.size
    if n < 2 or not np.all(np.isfinite(x)):
        return math.nan
    total = float(w.sum())
    xbar = float((w * x).sum() / total)
    spread = math.sqrt(float(np.sum((w * (x - xbar)) ** 2)))
    return spread / total * math.sqrt(n / (n - 1))


def initial_batches(precision: Precision) -> int:
    """Batches a point starts with: enough for ``min_ions`` (at least two, for an error),
    and never more than ``max_ions`` allows."""
    batch = precision.batch_ions
    first = max(2, math.ceil(precision.min_ions / batch))
    return min(first, math.ceil(precision.max_ions / batch))


def goal_of(precision: Precision, value: float) -> float:
    """The uncertainty the estimate must reach: ``abs``, or ``rel * |value|``.

    For a relative target a value of zero (or NaN) gives a goal of zero (or NaN), which
    :func:`is_met` never accepts.
    """
    if precision.abs is not None:
        return precision.abs
    return precision.rel * abs(float(value))


def is_met(err: float, goal: float) -> bool:
    """True when the error is finite and at most the (positive, finite) goal."""
    return math.isfinite(err) and math.isfinite(goal) and goal > 0 and err <= goal


def batches_to_add(precision: Precision, ions: int, err: float, goal: float, n_batches: int):
    """How many batches to add to a point that has ``ions`` ions and error ``err``.

    The error scales as ``1/sqrt(ions)``, so the ions needed for ``goal`` are
    ``ions * (err / goal)**2``; a 10 % margin is added and the batches needed are counted in
    units of ``batch_ions``. When the goal cannot be computed (value zero or NaN, or no
    error yet) the number of batches so far is added (doubling). The result is at least 1
    and never more than the batches the maximum still allows (0 at the maximum).
    """
    remaining = math.ceil((precision.max_ions - ions) / precision.batch_ions)
    if remaining <= 0:
        return 0
    count = n_batches
    if math.isfinite(err) and math.isfinite(goal) and goal > 0:
        # a huge err/goal ratio would overflow; anything that large means "the maximum"
        ratio = min(err / goal, 1e6)
        needed = ions * ratio**2 * 1.1
        count = math.ceil((needed - ions) / precision.batch_ions)
    return min(max(count, 1), remaining)
