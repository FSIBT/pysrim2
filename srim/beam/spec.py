"""Beam distributions in the run spec: positions, binning options and their checks.

The run spec (:class:`srim.spec.RunSpec`) holds the beam parameters ``ion.energy_keV``,
``ion.angle_deg``, ``ion.azimuth_deg`` and ``ion.position`` as a number or a distribution
(:class:`~srim.beam.shapes.Shape`), and the binning options as ``beam``. This module defines
the parts that are beam-specific: :class:`PositionSpec`, :class:`BeamOptions`, the checks of a
spec that has them (:func:`check_beam`, run by ``RunSpec.from_dict``) and :func:`effective`,
the clipped values a run samples from.

A spec without distributions has none of these: :func:`check_beam` does nothing for it and
its ``TRIM.IN`` is unchanged.
"""

import math
import warnings
from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any

from ..scan.extract import PYTHON_PREFIX
from ..spec import SpecError, _check_type
from .shapes import Function, Shape, Uniform, shape_from_dict

#: most ions a TRIM.DAT file can hold (SRIM's limit); a distributed exact run splits above it
MAX_TRIM_DAT_IONS = 99_999

#: ``beam.mode`` values
BEAM_MODES = ("exact", "binned")
#: default ``beam.max_bins``: binned beams with more combined bins are refused
DEFAULT_MAX_BINS = 2000
#: a binned beam with more combined bins than this warns
BIN_WARNING = 200
#: automatic bins per parameter (equal-probability bins) when ``beam.bins`` does not give them
DEFAULT_ENERGY_BINS = 10
DEFAULT_ANGLE_BINS = 6
#: keys of ``beam.bins``
BIN_KEYS = ("energy_keV", "angle_deg")
#: keys of ``ion.position``
POSITION_KEYS = ("y_nm", "z_nm", "depth_nm")

_MAX_TILT = 90.0
_FULL_TURN = 360.0


@dataclass
class PositionSpec:
    """Where the ions start, in the target's lateral frame (nm) and depth (nm).

    Each value is a number or a distribution. ``y_nm`` and ``z_nm`` are the lateral
    coordinates; ``depth_nm`` must lie inside the target. Positions are exact-mode only.
    """

    y_nm: "float | Shape" = 0.0
    z_nm: "float | Shape" = 0.0
    depth_nm: "float | Shape" = 0.0


@dataclass
class BeamOptions:
    """The ``beam:`` section of a run spec.

    ``mode`` is ``exact`` (every ion is in a TRIM.DAT file, the default) or ``binned`` (one
    SRIM run per bin, see the design). ``bins`` maps ``energy_keV`` and/or ``angle_deg`` to a
    number of equal-probability bins (an int) or to bin edges (a list); ``max_bins`` caps the
    combined bins of a binned beam.
    """

    mode: str = "exact"
    bins: dict = field(default_factory=dict)
    max_bins: int = DEFAULT_MAX_BINS

    def to_dict(self) -> dict[str, Any]:
        """``{mode, bins?, max_bins?}``: the optional keys only when they differ from the default."""
        out: dict[str, Any] = {"mode": self.mode}
        if self.bins:
            out["bins"] = {
                name: list(value) if isinstance(value, list) else value
                for name, value in self.bins.items()
            }
        if self.max_bins != DEFAULT_MAX_BINS:
            out["max_bins"] = self.max_bins
        return out


# --- parsing (used by RunSpec) ----------------------------------------------------------


def quantity_from_value(value: Any, key: str, base_dir: Any = None) -> "float | Shape":
    """A beam number from a YAML value: a finite number, or a distribution mapping.

    A distribution mapping is checked for the Python-function rules first, so a bad function
    reference is named by its key before anything is imported.

    Raises
    ------
    SpecError
        naming ``key``
    """
    if isinstance(value, Mapping):
        _check_raw_function(value, key)
        return shape_from_dict(value, key, base_dir)
    return _check_type(value, float, key)


def _check_raw_function(value: Mapping, key: str) -> None:
    if len(value) == 1 and "function" in value:
        params = value["function"]
        if isinstance(params, Mapping) and isinstance(params.get("ref"), str):
            check_function_ref(params["ref"], key)


def position_from_dict(raw: Any, key: str, base_dir: Any = None) -> PositionSpec:
    """``ion.position`` from its YAML mapping; a missing coordinate is 0."""
    if not isinstance(raw, Mapping):
        raise SpecError(f"{key} must be a mapping with y_nm, z_nm and depth_nm, got {raw!r}")
    for name in raw:
        if name not in POSITION_KEYS:
            raise SpecError(
                f"unknown key '{key}.{name}' (allowed: {', '.join(POSITION_KEYS)})"
            )
    values = {
        name: quantity_from_value(raw.get(name, 0.0), f"{key}.{name}", base_dir)
        for name in POSITION_KEYS
    }
    return PositionSpec(**values)


def beam_from_dict(raw: Any, key: str = "beam") -> BeamOptions:
    """The ``beam`` section from its YAML mapping (types only; :func:`check_beam` checks values)."""
    if not isinstance(raw, Mapping):
        raise SpecError(f"{key} must be a mapping, got {raw!r}")
    allowed = ("mode", "bins", "max_bins")
    for name in raw:
        if name not in allowed:
            raise SpecError(f"unknown key '{key}.{name}' (allowed: {', '.join(allowed)})")
    mode = raw.get("mode", "exact")
    if not isinstance(mode, str):
        raise SpecError(f"{key}.mode must be a string, got {mode!r}")
    bins_raw = raw.get("bins", {})
    if not isinstance(bins_raw, Mapping):
        raise SpecError(f"{key}.bins must be a mapping, got {bins_raw!r}")
    bins: dict[str, Any] = {}
    for name, value in bins_raw.items():
        full = f"{key}.bins.{name}"
        if isinstance(value, list):
            bins[name] = [_check_type(v, float, full) for v in value]
        else:
            bins[name] = _int(value, full)
    max_bins = _int(raw.get("max_bins", DEFAULT_MAX_BINS), f"{key}.max_bins")
    return BeamOptions(mode, bins, max_bins)


def _int(value: Any, key: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise SpecError(f"{key} must be an integer, got {value!r}")
    return value


# --- function references ---------------------------------------------------------------


def check_function_ref(ref: str, key: str) -> None:
    """Refuse a ``python:<module>:<name>`` reference that cannot be re-imported by a worker.

    Raises
    ------
    SpecError
        naming ``key``: the module is ``__main__``, or the name is a lambda or a function made
        inside another function
    """
    if not ref.startswith(PYTHON_PREFIX):
        return
    module, _, qualname = ref[len(PYTHON_PREFIX) :].partition(":")
    if module == "__main__":
        raise SpecError(
            f"{key}: a Python distribution must come from an importable module, not __main__ "
            "(put the density function in a module and refer to it as python:<module>:<name>)"
        )
    if "<" in qualname:
        raise SpecError(
            f"{key}: a Python distribution must be a module-level function, not a lambda or a "
            "function made inside another function"
        )


# --- checks and values ----------------------------------------------------------------------


def _clipped(value: Any, low: float, high: float, key: str) -> "float | Shape":
    """A number as a float, a distribution cut to ``[low, high]``.

    Raises
    ------
    SpecError
        naming ``key`` when no probability is left in ``[low, high)``
    """
    if isinstance(value, Shape):
        clipped = value.clip(low, high, key)
        lo, hi = clipped.support()
        if not lo < hi:
            raise SpecError(f"{key}: no probability in [{low}, {high})")
        return clipped
    return float(value)


def _bin_count(value: Any, key: str) -> int:
    """The number of bins a ``beam.bins`` value gives (an int, or the intervals of the edges)."""
    if isinstance(value, list):
        edges = [_check_type(v, float, key) for v in value]
        if len(edges) < 2:
            raise SpecError(f"{key} needs at least two edges, got {len(edges)}")
        if any(b <= a for a, b in zip(edges, edges[1:])):
            raise SpecError(f"{key} edges must be strictly increasing, got {value!r}")
        return len(edges) - 1
    if isinstance(value, bool) or not isinstance(value, int) or value < 1:
        raise SpecError(
            f"{key} must be a positive integer (the number of bins) or a list of edges, "
            f"got {value!r}"
        )
    return value


def _uniform_over_turn(shape: Shape, key: str) -> bool:
    """True for a uniform azimuth over the full turn (the only binned-mode azimuth)."""
    if not isinstance(shape, Uniform):
        return False
    return _clipped(shape, 0.0, _FULL_TURN, key).support() == (0.0, _FULL_TURN)


def _shapes(spec, prefix: str) -> list[tuple[str, Shape]]:
    """``(dotted key, shape)`` of every distribution in the spec."""
    p = f"{prefix}ion."
    items: list[tuple[str, Any]] = [
        (p + "energy_keV", spec.ion.energy_keV),
        (p + "angle_deg", spec.ion.angle_deg),
        (p + "azimuth_deg", spec.ion.azimuth_deg),
    ]
    if spec.ion.position is not None:
        for name in POSITION_KEYS:
            items.append((f"{p}position.{name}", getattr(spec.ion.position, name)))
    return [(key, value) for key, value in items if isinstance(value, Shape)]


def check_beam(spec, prefix: str = "") -> None:
    """Validate the beam part of a run spec; raise :class:`SpecError` naming the dotted key.

    Called by ``RunSpec.from_dict`` (with ``prefix`` ``"spec."`` for a run file). A spec
    without distributions and without ``beam`` passes unchecked; a ``beam`` section with no
    distribution is refused.
    """
    beam = spec.beam
    mode = "exact" if beam is None else beam.mode
    if beam is not None:
        if mode not in BEAM_MODES:
            raise SpecError(f"{prefix}beam.mode must be 'exact' or 'binned', got {mode!r}")
        if isinstance(beam.max_bins, bool) or not isinstance(beam.max_bins, int) or beam.max_bins < 1:
            raise SpecError(
                f"{prefix}beam.max_bins must be a positive integer, got {beam.max_bins!r}"
            )
    if not spec.is_distributed:
        if beam is not None:
            raise SpecError(
                f"{prefix}beam is set, but no beam parameter is a distribution (give "
                "ion.energy_keV, ion.angle_deg, ion.azimuth_deg or ion.position a distribution, "
                "or remove beam)"
            )
        return

    for key, shape in _shapes(spec, prefix):
        if isinstance(shape, Function):
            check_function_ref(shape.ref, key)

    p = f"{prefix}ion."
    energy, angle, azimuth = spec.ion.energy_keV, spec.ion.angle_deg, spec.ion.azimuth_deg
    if isinstance(energy, Shape) and energy.support()[1] <= 0:
        raise SpecError(f"{p}energy_keV: the distribution has no energy above 0 (must be > 0)")
    _clipped(energy, 0.0, math.inf, p + "energy_keV")
    _clipped(angle, 0.0, _MAX_TILT, p + "angle_deg")
    if azimuth is not None:
        if not isinstance(azimuth, Shape) and not 0.0 <= azimuth < _FULL_TURN:
            raise SpecError(f"{p}azimuth_deg must be in [0, 360), got {azimuth!r}")
        _clipped(azimuth, 0.0, _FULL_TURN, p + "azimuth_deg")

    position = spec.ion.position
    if position is not None:
        total = sum(layer.thickness_nm for layer in spec.target)
        depth = position.depth_nm
        key = f"{p}position.depth_nm"
        if isinstance(depth, Shape):
            lo, hi = depth.support()
            described = f"a distribution with support [{lo}, {hi}]"
        else:
            lo = hi = depth
            described = f"{depth!r}"
        if lo < 0 or hi >= total:
            raise SpecError(
                f"{key} must lie inside the target, [0, {total}) nm; got {described}"
            )

    if mode == "exact":
        if spec.calculation not in (1, 2):
            raise SpecError(
                f"{prefix}calculation {spec.calculation} cannot be used with a distributed "
                "beam in exact mode: use 1 (quick, SRIM calculation 4) or 2 (full cascades, "
                "SRIM calculation 5), or set beam.mode: binned"
            )
    else:
        if position is not None:
            raise SpecError(
                f"{p}position needs exact mode: position distributions are exact-only "
                "(set beam.mode: exact)"
            )
        if isinstance(azimuth, Shape) and not _uniform_over_turn(azimuth, p + "azimuth_deg"):
            raise SpecError(
                f"{p}azimuth_deg: a non-uniform azimuth needs exact mode (set beam.mode: exact)"
            )

    bins = {} if beam is None else beam.bins
    counts: dict[str, int] = {}
    for name, value in bins.items():
        key = f"{prefix}beam.bins.{name}"
        if name not in BIN_KEYS:
            raise SpecError(f"{key}: unknown key (allowed: {', '.join(BIN_KEYS)})")
        shape = energy if name == "energy_keV" else angle
        if not isinstance(shape, Shape):
            what = "energy" if name == "energy_keV" else "angle"
            raise SpecError(
                f"{key}: the {what} is fixed, so there is nothing to bin "
                "(give it a distribution or remove the bins)"
            )
        counts[name] = _bin_count(value, key)

    if mode == "binned":
        n_energy = counts.get(
            "energy_keV", DEFAULT_ENERGY_BINS if isinstance(energy, Shape) else 1
        )
        n_angle = counts.get("angle_deg", DEFAULT_ANGLE_BINS if isinstance(angle, Shape) else 1)
        combined = n_energy * n_angle
        max_bins = DEFAULT_MAX_BINS if beam is None else beam.max_bins
        if combined > max_bins:
            raise SpecError(
                f"{prefix}beam.max_bins: the binned beam has {combined} bins "
                f"({n_energy} energy x {n_angle} angle), more than beam.max_bins = {max_bins}; "
                "use fewer bins or raise beam.max_bins"
            )
        if combined > BIN_WARNING:
            warnings.warn(
                f"binned beam with {combined} bins ({n_energy} energy x {n_angle} angle); "
                f"more than {BIN_WARNING} bins runs SRIM that many times and is slow",
                UserWarning,
                stacklevel=2,
            )


def effective(spec) -> dict[str, "float | Shape"]:
    """The values a run samples from: distributions cut to their physical domain.

    Keys: ``energy_keV`` (cut to ``(0, inf)``), ``angle_deg`` (cut to ``[0, 90)``),
    ``azimuth_deg`` (cut to ``[0, 360)``; a distributed spec without an azimuth gets the
    uniform full turn, anything else 0.0), and ``y_nm``, ``z_nm``, ``depth_nm`` (0.0 without
    ``ion.position``). The spec itself is not changed: ``to_dict()`` keeps what was written.
    """
    azimuth = spec.ion.azimuth_deg
    if azimuth is None:
        azimuth = Uniform(0.0, _FULL_TURN) if spec.is_distributed else 0.0
    else:
        azimuth = _clipped(azimuth, 0.0, _FULL_TURN, "ion.azimuth_deg")
    position = spec.ion.position if spec.ion.position is not None else PositionSpec()
    return {
        "energy_keV": _clipped(spec.ion.energy_keV, 0.0, math.inf, "ion.energy_keV"),
        "angle_deg": _clipped(spec.ion.angle_deg, 0.0, _MAX_TILT, "ion.angle_deg"),
        "azimuth_deg": azimuth,
        "y_nm": position.y_nm,
        "z_nm": position.z_nm,
        "depth_nm": position.depth_nm,
    }
