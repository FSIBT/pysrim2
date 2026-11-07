"""Binned mode: the bins of a distributed beam, the ions split over them, and one bin's spec.

A binned beam cuts each distributed beam parameter (energy, tilt) into bins, runs SRIM once per
bin of the product of the two, and gives each run the ions of its bin. :func:`make_bins` cuts
the parameters, :func:`allocate_ions` splits a number of ions over the bins by Webster
(Sainte-Laguë) apportionment, and :func:`bin_spec` is the fixed-value run spec of one bin.
:func:`bins_frame` puts the bins and their ion counts in a table.

Apportionment: each ion is a seat; bin ``k`` with probability ``p_k`` has its next seat at the
priority ``p_k / (n_k + 0.5)`` (``n_k`` seats so far). Seats are awarded in order of decreasing
priority, ties to the lower bin index. :func:`allocate_ions` gives the same result as that
seat-by-seat definition, computed without a loop over the ions.
"""

import heapq
import math
import warnings
from dataclasses import dataclass

import numpy as np
import pandas as pd

from ..spec import RunSpec, SpecError
from .shapes import Shape
from .spec import BIN_WARNING, DEFAULT_ANGLE_BINS, DEFAULT_ENERGY_BINS, BeamOptions, effective

#: bins per distributed parameter when ``beam.bins`` does not give them
AUTO_BINS = {"energy_keV": DEFAULT_ENERGY_BINS, "angle_deg": DEFAULT_ANGLE_BINS}
#: a binned beam with more combined bins warns
WARN_BINS = BIN_WARNING
#: mass outside explicit bin edges above this (as a fraction) warns
_DROP_WARNING = 0.001


@dataclass(frozen=True)
class BeamBin:
    """One bin of a binned beam: the value a run uses, its probability and its range.

    ``energy_range`` and ``angle_range`` are ``(low, high)`` of the bin, None when that
    parameter is fixed (the value is then the fixed value).
    """

    index: int
    energy_keV: float
    angle_deg: float
    probability: float
    energy_range: "tuple[float, float] | None"
    angle_range: "tuple[float, float] | None"


# --- bins -------------------------------------------------------------------------------


def _axis(value, bin_option, name: str):
    """``(value, probability, range)`` of the bins of one parameter.

    ``value`` is a float (fixed: one bin) or a :class:`Shape` clipped to its domain.
    ``bin_option`` is the ``beam.bins`` entry of ``name``: an int (equal-probability bins) or a
    list of edges. A bin with no probability is dropped.
    """
    if not isinstance(value, Shape):
        return [(float(value), 1.0, None)]
    lo, hi = value.support()
    if lo == hi:
        return [(float(lo), 1.0, None)]
    key = f"beam.bins.{name}"
    if isinstance(bin_option, list):
        edges = _clipped_edges(bin_option, lo, hi, key)
        probabilities = value.probabilities(edges)
        dropped = 1.0 - float(probabilities.sum())
        if dropped > _DROP_WARNING:
            warnings.warn(
                f"{key}: {dropped:.2%} of the probability lies outside the given edges and is "
                "dropped (the rest is renormalised)",
                UserWarning,
                stacklevel=3,
            )
    else:
        edges = np.asarray(value.equal_probability_edges(bin_option), dtype=float)
        probabilities = value.probabilities(edges)
    keep = probabilities > 0
    probabilities = probabilities[keep]
    probabilities = probabilities / probabilities.sum()
    lows = edges[:-1][keep]
    highs = edges[1:][keep]
    means = [value.interval_mean(low, high) for low, high in zip(lows, highs, strict=True)]
    return [
        (float(mean), float(p), (float(low), float(high)))
        for mean, p, low, high in zip(means, probabilities, lows, highs, strict=True)
    ]


def _clipped_edges(edges, lo: float, hi: float, key: str) -> np.ndarray:
    """The edges clipped to the support ``[lo, hi]``, without the repeats clipping makes."""
    kept: list[float] = []
    for edge in edges:
        clipped = min(max(float(edge), lo), hi)
        if not kept or clipped > kept[-1]:
            kept.append(clipped)
    if len(kept) < 2:
        raise SpecError(f"{key}: no probability inside the distribution's support [{lo}, {hi}]")
    return np.asarray(kept, dtype=float)


def make_bins(spec: RunSpec) -> list[BeamBin]:
    """The bins of a beam: the product of the energy and tilt bins, energy-major.

    A parameter that is not distributed has one bin at its value (range None). A distributed
    one is cut into ``beam.bins`` equal-probability bins, or into the ``beam.bins`` edges
    (clipped to the distribution's support; mass outside them is dropped and the rest
    renormalised). Each bin's value is the mean of its interval. Bins with no probability
    are dropped.

    Raises
    ------
    SpecError
        if there are more than ``beam.max_bins`` bins (naming ``beam.max_bins``), or the edges
        leave no probability
    UserWarning
        above :data:`WARN_BINS` bins, or when edges drop more than 0.1 % of the probability
    """
    beam = spec.beam if spec.beam is not None else BeamOptions()
    values = effective(spec)
    energy = _axis_for(values["energy_keV"], beam.bins, "energy_keV")
    angle = _axis_for(values["angle_deg"], beam.bins, "angle_deg")
    total = len(energy) * len(angle)
    if total > beam.max_bins:
        raise SpecError(
            f"beam.max_bins: the binned beam has {total} bins ({len(energy)} energy x "
            f"{len(angle)} angle), more than beam.max_bins = {beam.max_bins}; use fewer bins "
            "or raise beam.max_bins"
        )
    if total > WARN_BINS:
        warnings.warn(
            f"binned beam with {total} bins ({len(energy)} energy x {len(angle)} angle); "
            f"more than {WARN_BINS} bins runs SRIM that many times and is slow",
            UserWarning,
            stacklevel=2,
        )
    out: list[BeamBin] = []
    for e_value, e_prob, e_range in energy:
        for a_value, a_prob, a_range in angle:
            probability = e_prob * a_prob
            if not probability > 0:
                continue
            out.append(BeamBin(len(out), e_value, a_value, probability, e_range, a_range))
    return out


def _axis_for(value, bins, name):
    return _axis(value, bins.get(name, AUTO_BINS[name]) if isinstance(value, Shape) else None, name)


# --- ion allocation ---------------------------------------------------------------------


def _check_count(value, name: str) -> int:
    if isinstance(value, bool) or not isinstance(value, (int, np.integer)) or value < 0:
        raise ValueError(f"{name} must be a non-negative integer, got {value!r}")
    return int(value)


def _seats_at(p: np.ndarray, s: float) -> np.ndarray:
    """Per bin, the number of seats with priority ``p / (j + 0.5) >= s``.

    Computed from the float priorities themselves, so the count is exact for those priorities.
    """
    m = np.maximum(np.floor(p / s + 0.5), 0).astype(np.int64)
    while True:  # the seat m-1 must have priority >= s
        down = ((m > 0) & (p / (m - 0.5) < s)).astype(np.int64)
        if not down.any():
            break
        m = m - down
    while True:  # the seat m must not have priority >= s
        up = ((p > 0) & (p / (m + 0.5) >= s)).astype(np.int64)
        if not up.any():
            break
        m = m + up
    return m


def allocate_ions(probabilities, total: int) -> np.ndarray:
    """Split ``total`` ions over the bins by Webster (Sainte-Laguë) apportionment.

    ``probabilities`` need not sum to 1 (they are normalised). The result is the seat-by-seat
    definition: each ion goes to the bin with the largest ``p_k / (n_k + 0.5)`` (``n_k`` ions
    so far), ties to the lower index. It sums to ``total``, gives a zero-probability bin no
    ion, and never takes an ion from a bin when ``total`` grows (house-monotone).

    Raises
    ------
    ValueError
        for a negative or non-finite probability, a negative total, or no positive probability
        when ``total`` is positive
    """
    total = _check_count(total, "total")
    p = np.asarray(probabilities, dtype=float)
    if p.ndim != 1:
        raise ValueError(f"probabilities must be one-dimensional, got shape {p.shape}")
    if not np.all(np.isfinite(p)):
        raise ValueError("probabilities must be finite")
    if np.any(p < 0):
        raise ValueError("probabilities must not be negative")
    if total == 0:
        return np.zeros(p.size, dtype=np.int64)
    s = float(p.sum())
    if not s > 0:
        raise ValueError("probabilities must have a positive sum to allocate ions")
    p = p / s

    # bisect for the threshold s: seats with priority >= s are the first ones in the order
    # of awards. lo has more than `total` seats, hi at most `total`.
    pmax = float(p.max())
    lo = pmax / (total + 1)
    hi = 4.0 * pmax
    while hi > lo * (1.0 + 1e-13):
        mid = math.sqrt(lo * hi)
        if int(_seats_at(p, mid).sum()) > total:
            lo = mid
        else:
            hi = mid
    counts = _seats_at(p, hi)
    remaining = total - int(counts.sum())

    # the next `remaining` seats (all below the threshold) go by the heap, as in the definition
    plist = p.tolist()
    seats = counts.tolist()
    heap = [(-(plist[k] / (seats[k] + 0.5)), k) for k in range(len(plist)) if plist[k] > 0]
    heapq.heapify(heap)
    for _ in range(remaining):
        _, k = heapq.heappop(heap)
        seats[k] += 1
        heapq.heappush(heap, (-(plist[k] / (seats[k] + 0.5)), k))
    return np.asarray(seats, dtype=np.int64)


def beam_batch_allocation(probabilities, done: int, size: int) -> np.ndarray:
    """The ions of the batch after ``done`` ions, of ``size`` ions: the difference of the
    allocations of ``done + size`` and ``done`` ions (never negative, by house-monotonicity)."""
    done = _check_count(done, "done")
    size = _check_count(size, "size")
    return allocate_ions(probabilities, done + size) - allocate_ions(probabilities, done)


# --- one bin as a run ---------------------------------------------------------------------


def bin_spec(spec: RunSpec, b: BeamBin, ions: int, seed: int) -> RunSpec:
    """The plain run spec of one bin: its energy and tilt fixed, ``ions`` ions and ``seed``.

    The beam (``beam``), the azimuth and the position are dropped, so the result is a single
    beam; everything else (target, settings, calculation) is kept. The result is validated.
    """
    data = spec.to_dict()
    ion = data["ion"]
    ion["energy_keV"] = float(b.energy_keV)
    ion["angle_deg"] = float(b.angle_deg)
    ion.pop("azimuth_deg", None)
    ion.pop("position", None)
    data.pop("beam", None)
    data["number_ions"] = int(ions)
    data["seed"] = int(seed)
    return RunSpec.from_dict(data)


# --- table --------------------------------------------------------------------------------


def bins_frame(bins, ions_per_bin) -> pd.DataFrame:
    """One row per bin: ``bin, energy_keV, angle_deg, energy_low_keV, energy_high_keV,
    angle_low_deg, angle_high_deg, probability, n_ions``.

    The range columns are NaN for a fixed parameter. ``ions_per_bin`` gives ``n_ions`` for each
    bin, in order.
    """
    counts = np.asarray(ions_per_bin)
    if counts.shape != (len(bins),):
        raise ValueError(f"ions_per_bin needs one count per bin ({len(bins)}), got {counts.shape}")
    nan = math.nan
    e_low = [b.energy_range[0] if b.energy_range is not None else nan for b in bins]
    e_high = [b.energy_range[1] if b.energy_range is not None else nan for b in bins]
    a_low = [b.angle_range[0] if b.angle_range is not None else nan for b in bins]
    a_high = [b.angle_range[1] if b.angle_range is not None else nan for b in bins]
    return pd.DataFrame(
        {
            "bin": [b.index for b in bins],
            "energy_keV": [b.energy_keV for b in bins],
            "angle_deg": [b.angle_deg for b in bins],
            "energy_low_keV": e_low,
            "energy_high_keV": e_high,
            "angle_low_deg": a_low,
            "angle_high_deg": a_high,
            "probability": [b.probability for b in bins],
            "n_ions": counts.astype(np.int64),
        },
        columns=[
            "bin",
            "energy_keV",
            "angle_deg",
            "energy_low_keV",
            "energy_high_keV",
            "angle_low_deg",
            "angle_high_deg",
            "probability",
            "n_ions",
        ],
    )
