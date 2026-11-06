"""Extractors: turn one calculation's outputs into the columns of a scan row.

An extractor is a function ``f(results, point) -> dict`` where ``results`` is a
:class:`srim.output.Results` (or :class:`srim.scan.MergedResults`) and ``point`` the scan
point (``None`` when used on its own). Its dict is flattened into the columns of that
point's row, so every key should say what it measures and in which unit.

Predefined extractors (registered under their names, see :func:`get`):

- ``implant_summary``: :func:`srim.analysis.implant_summary` of ``range3d``
- ``peak_vacancy_depth``, ``sputter_yield``, ``backscattered_fraction``,
  ``transmitted_fraction``, ``energy_to_recoils``: plain extractors
- ``vacancy_at(depth_nm)`` and ``ions_in_layer(layer_index, boundaries_nm=None)``:
  factories; calling them with their parameters returns the extractor

Parameters of a factory come from a YAML mapping, ``{"vacancy_at": {"depth_nm": 50}}``
(see :func:`resolve`); a plain name, ``"sputter_yield"``, is enough for the others.
Register your own with :func:`register`.
"""

import math
from collections.abc import Callable, Iterable, Mapping
from pathlib import Path
from typing import Any

import numpy as np

from ..analysis import implant_summary as _implant_summary
from ..output import Results
from ..spec import SpecError, parse_trim_in

#: ``extract(results, point) -> dict``; ``point`` is a ``ScanPoint`` (or ``None``)
Extractor = Callable[[Results, Any], dict[str, Any]]

_REGISTRY: dict[str, Callable] = {}
_FACTORIES: set[str] = set()


def register(name: str, fn: Callable, *, factory: bool = False, replace: bool = False) -> None:
    """Make ``fn`` available by ``name`` (for :func:`get`, :func:`resolve` and ``run.yaml``).

    Parameters
    ----------
    name : :obj:`str`
        the name; must not be empty. Registering a taken name raises unless ``replace=True``.
    fn : callable
        an extractor ``fn(results, point) -> dict``, or with ``factory=True`` a function
        that takes parameters and returns an extractor
    factory : :obj:`bool`
        mark ``fn`` as a factory: :func:`resolve` then needs a mapping with its parameters
    replace : :obj:`bool`
        allow replacing an existing registration
    """
    if not isinstance(name, str) or not name:
        raise ValueError(f"extractor name must be a non-empty string, got {name!r}")
    if not callable(fn):
        raise TypeError(f"extractor {name!r} must be callable")
    if name in _REGISTRY and not replace:
        raise ValueError(f"extractor {name!r} is already registered")
    _REGISTRY[name] = fn
    if factory:
        _FACTORIES.add(name)
    else:
        _FACTORIES.discard(name)


def available() -> list[str]:
    """Names of the registered extractors and factories, sorted"""
    return sorted(_REGISTRY)


def get(name: str) -> Callable:
    """The extractor (or, for a factory name, the factory) registered as ``name``

    Raises
    ------
    KeyError
        if ``name`` is not registered; the message lists the known names
    """
    try:
        return _REGISTRY[name]
    except KeyError:
        known = ", ".join(available()) or "none"
        raise KeyError(f"unknown extractor {name!r}; known extractors: {known}") from None


def resolve(entry: Any) -> Extractor:
    """The extractor for a ``run.yaml`` style entry.

    ``entry`` is either a registered name (``"sputter_yield"``), or a one-key mapping
    ``{name: params}`` where ``params`` is a mapping of keyword arguments for a factory
    (``{"vacancy_at": {"depth_nm": 50}}``). A plain name of a factory is an error, since
    the factory needs its parameters; an extractor takes none in the mapping form.
    """
    if isinstance(entry, str):
        if entry in _FACTORIES:
            raise ValueError(f"extractor {entry!r} needs parameters, e.g. {{{entry!r}: {{...}}}}")
        return get(entry)
    if isinstance(entry, Mapping) and len(entry) == 1:
        ((name, params),) = entry.items()
        fn = get(name)
        if name not in _FACTORIES:
            raise ValueError(f"extractor {name!r} takes no parameters")
        if params is None:
            params = {}
        if not isinstance(params, Mapping):
            raise ValueError(f"parameters of {name!r} must be a mapping, got {params!r}")
        return fn(**dict(params))
    raise ValueError(f"an extractor is a name or a one-key mapping {{name: params}}, got {entry!r}")


def combine(extractors: Iterable[Any]) -> Extractor:
    """One extractor that runs ``extractors`` in order and merges their dicts.

    Items are extractors or entries accepted by :func:`resolve`. A key produced twice
    raises ``ValueError`` (naming the key), so no column is silently overwritten.
    """
    fns = [item if callable(item) else resolve(item) for item in extractors]

    def extract(results, point=None):
        out: dict[str, Any] = {}
        for fn in fns:
            for key, value in fn(results, point).items():
                if key in out:
                    raise ValueError(f"duplicate column {key!r} from more than one extractor")
                out[key] = value
        return out

    return extract


# -- helpers ----------------------------------------------------------------------------


def _range3d_df(results, who):
    table = getattr(results, "range3d", None)
    if table is None:
        raise ValueError(
            f"{who} needs the final ion positions (RANGE_3D.txt): run with ranges=1 "
            "(TRIM(..., ranges=1) or settings: {ranges: 1} in run.yaml)"
        )
    return table.df


def _output_df(results, attribute, who, setting):
    table = getattr(results, attribute, None)
    if table is None:
        raise ValueError(f"{who} needs the {attribute} output: {setting}")
    return table.df


def _trim_in_ions(directory):
    """Number of ions requested in the ``TRIM.IN`` of ``directory``; ``None`` if unreadable."""
    try:
        return parse_trim_in(Path(directory, "TRIM.IN")).number_ions
    except (OSError, SpecError):
        return None


def _incident_ions(results):
    """Number of ions the calculation was asked for, the denominator of the fractions.

    From the run spec (``number_ions``, set by ``TRIM.run`` and by merged results), else
    from the ``TRIM.IN`` in the results folder, else the ion count of RANGE.txt rounded to
    the nearest integer (``num_ions``).
    """
    spec = _spec_dict(getattr(results, "spec", None)) or {}
    count = spec.get("number_ions")
    if count is None and getattr(results, "directory", None) is not None:
        count = _trim_in_ions(results.directory)
    if count is None:
        count = results.range.metadata["num_ions"]
    count = float(count)
    if not count > 0:
        raise ValueError(f"no incident ions recorded in the results (number_ions={count:g})")
    return count


def _vacancy_profile(results):
    """``(depth_A, vacancies, knock_ons)``, per A per ion.

    ``vacancies`` is the sum of the element columns, the vacancies of the target. This is
    the "Total Target Vacancies" of VACANCY.txt. Primary knock-on atoms are not vacancies
    and are returned separately.
    """
    df = results.vacancy.df
    elements = [c for c in df.columns if c.startswith("vacancies_")]
    vacancies = df[elements].to_numpy(dtype=float).sum(axis=1)
    knock_ons = df["knock_ons_per_A_per_ion"].to_numpy(dtype=float)
    return df["depth_A"].to_numpy(dtype=float), vacancies, knock_ons


def _bin_geometry(depth_a):
    """``(lower, centre, width)`` of each depth bin [A].

    ``depth_A`` of the depth tables is the UPPER edge of its bin; the first bin starts at
    the surface (0).
    """
    upper = np.asarray(depth_a, dtype=float)
    lower = np.concatenate([[0.0], upper[:-1]])
    return lower, (lower + upper) / 2.0, upper - lower


def _spec_dict(spec):
    if spec is None or isinstance(spec, Mapping):
        return spec
    to_dict = getattr(spec, "to_dict", None)
    return to_dict() if to_dict is not None else None


# -- predefined extractors ---------------------------------------------------------------


def implant_summary(results, point=None) -> dict[str, float]:
    """Implantation statistics of the stopped ions (:func:`srim.analysis.implant_summary`).

    Needs ``ranges=1`` (``range3d``). Lengths in Angstrom.

    Returns
    -------
    dict
        ``n_ions_stopped`` (count of stopped ions; ``n_ions`` is the scan's own column for
        the ions run), ``mean_depth_A`` (projected range), ``straggle_depth_A``,
        ``lateral_range_A``, ``lateral_straggle_A``, ``radial_range_A``,
        ``radial_straggle_A``, ``skewness`` and ``kurtosis`` (dimensionless).
        The keys of :meth:`srim.analysis.implant_summary` with ``n_ions`` renamed to
        ``n_ions_stopped``.
    """
    summary = dict(_implant_summary(_range3d_df(results, "implant_summary")))
    summary["n_ions_stopped"] = summary.pop("n_ions")
    return summary


def vacancy_at(depth_nm: float) -> Extractor:
    """Factory: vacancy and knock-on densities at depth ``depth_nm`` [nm].

    ``vacancies`` is the vacancies of the target elements (the sum of the element columns
    of the ``vacancy`` table); ``knock_ons`` the primary knock-on atoms. Each is linearly
    interpolated between the bin centres of the table (``depth_A`` is the bin's upper
    edge, see :func:`_bin_geometry`) and is 0 beyond the table's last bin.

    YAML: ``{"vacancy_at": {"depth_nm": 50}}``.

    Returns
    -------
    extractor
        with the keys ``vacancies_per_A_per_ion_at_{depth_nm}nm`` and
        ``knock_ons_per_A_per_ion_at_{depth_nm}nm`` (1/A/ion; ``depth_nm`` formatted with
        ``:g``, e.g. ``..._at_50nm``)
    """
    depth = float(depth_nm)
    if not math.isfinite(depth) or depth < 0:
        raise ValueError(f"depth_nm must be a finite number >= 0, got {depth_nm!r}")
    suffix = f"at_{depth:g}nm"

    def extract(results, point=None):
        depth_a, vacancies, knock_ons = _vacancy_profile(results)
        return {
            f"vacancies_per_A_per_ion_{suffix}": _at_depth(depth_a, vacancies, depth * 10),
            f"knock_ons_per_A_per_ion_{suffix}": _at_depth(depth_a, knock_ons, depth * 10),
        }

    return extract


def _at_depth(depth_a, values, x_a):
    """``values`` (one per bin) interpolated at depth ``x_a`` [A] between the bin centres.

    0 beyond the last bin edge (the table's depth range), nan for an empty table. A depth
    in the first bin (``[0, first edge]``) gets the first bin's value.
    """
    if len(depth_a) == 0:
        return math.nan
    if 0.0 <= x_a <= depth_a[-1]:
        _, centres, _ = _bin_geometry(depth_a)
        return float(np.interp(x_a, centres, values))
    return 0.0


def peak_vacancy_depth(results, point=None) -> dict[str, float]:
    """Depth of the maximum of the vacancy profile (element vacancies, see :func:`vacancy_at`).

    Returns
    -------
    dict
        ``peak_vacancy_depth_A`` [A]: centre of the bin with the most vacancies (the first
        one on a tie); ``nan`` for an empty table.
    """
    depth_a, vacancies, _ = _vacancy_profile(results)
    if len(depth_a) == 0:
        return {"peak_vacancy_depth_A": math.nan}
    _, centres, _ = _bin_geometry(depth_a)
    return {"peak_vacancy_depth_A": float(centres[int(np.argmax(vacancies))])}


def sputter_yield(results, point=None) -> dict[str, float]:
    """Sputtered atoms per incident ion (``SPUTTER.txt``, one row per sputtered atom).

    Needs ``settings: {sputtered: 1}`` and ``calculation=2`` (full cascades). An empty
    file gives 0.

    Returns
    -------
    dict
        ``sputter_yield_atoms_per_ion`` (atoms/ion)
    """
    df = _output_df(
        results,
        "sputter",
        "sputter_yield",
        "run with settings sputtered=1 and calculation=2",
    )
    return {"sputter_yield_atoms_per_ion": len(df) / _incident_ions(results)}


def backscattered_fraction(results, point=None) -> dict[str, float]:
    """Backscattered atoms per incident ion (``BACKSCAT.txt``, one row per atom).

    Needs ``settings: {backscattered: 1}``. One row per atom leaving, so a cascade can
    give more than one per ion; for single ions it is a fraction.

    Returns
    -------
    dict
        ``backscattered_fraction`` (dimensionless)
    """
    df = _output_df(
        results,
        "backscat",
        "backscattered_fraction",
        "run with settings backscattered=1",
    )
    return {"backscattered_fraction": len(df) / _incident_ions(results)}


def transmitted_fraction(results, point=None) -> dict[str, float]:
    """Transmitted atoms per incident ion (``TRANSMIT.txt``, one row per atom).

    Needs ``settings: {transmit: 1}``. See :func:`backscattered_fraction` for cascades.

    Returns
    -------
    dict
        ``transmitted_fraction`` (dimensionless)
    """
    df = _output_df(
        results,
        "transmit",
        "transmitted_fraction",
        "run with settings transmit=1",
    )
    return {"transmitted_fraction": len(df) / _incident_ions(results)}


def ions_in_layer(layer_index: int, boundaries_nm=None) -> Extractor:
    """Factory: fraction of the stopped ions that come to rest in one layer.

    The layer is ``layer_index`` (0 = the surface layer, as in the target of the spec).
    Its depth range comes from the run spec of the results (``results.spec``, set by
    ``TRIM.run``); without a spec, ``boundaries_nm`` gives the layer edges. Stopped ions
    are the rows of ``range3d``; a depth ``d`` is in the layer if ``lower <= d < upper``.

    Parameters
    ----------
    layer_index : :obj:`int`
        index of the layer, from 0
    boundaries_nm : sequence of float, optional
        depth of the layer edges [nm] from the surface, ``[0, t1, t1 + t2, ...]``: one
        more value than there are layers. Used only when the results have no run spec.

    YAML: ``{"ions_in_layer": {"layer_index": 1}}`` (or with ``boundaries_nm``).

    Returns
    -------
    extractor
        with the key ``fraction_of_stopped_ions_in_layer_{layer_index}``: the denominator is
        the number of stopped ions (rows of ``range3d``), not the number of incident ions
    """
    index = int(layer_index)
    if index < 0:
        raise ValueError(f"layer_index must be >= 0, got {layer_index!r}")
    if boundaries_nm is not None:
        boundaries_nm = [float(b) for b in boundaries_nm]
        if len(boundaries_nm) < 2 or np.any(np.diff(boundaries_nm) <= 0):
            raise ValueError("boundaries_nm must be at least two strictly increasing depths")
    key = f"fraction_of_stopped_ions_in_layer_{index}"

    def extract(results, point=None):
        lower, upper = _layer_bounds_A(results, index, boundaries_nm)
        depth = _range3d_df(results, "ions_in_layer")["depth_A"].to_numpy(dtype=float)
        if depth.size == 0:
            return {key: math.nan}
        return {key: float(np.mean((depth >= lower) & (depth < upper)))}

    return extract


def _layer_bounds_A(results, index, boundaries_nm):
    spec = _spec_dict(getattr(results, "spec", None))
    if spec and spec.get("target"):
        thicknesses = [float(layer["thickness_nm"]) for layer in spec["target"]]
        edges = np.concatenate([[0.0], np.cumsum(thicknesses)])
    elif boundaries_nm is not None:
        edges = np.asarray(boundaries_nm, dtype=float)
    else:
        raise ValueError(
            "ions_in_layer needs the layer boundaries: these results have no run spec "
            "(they were not made by TRIM.run); pass boundaries_nm=[0, t1, t1 + t2, ...]"
        )
    if not 0 <= index < len(edges) - 1:
        raise ValueError(
            f"layer_index {index} is out of range: the target has {len(edges) - 1} layer(s)"
        )
    # nm -> Angstrom
    return 10.0 * edges[index], 10.0 * edges[index + 1]


def energy_to_recoils(results, point=None) -> dict[str, float]:
    """Energy the ions transfer to recoil atoms, summed over depth (``E2RECOIL.txt``).

    Integrates the ``ions_eV_per_A_per_ion`` column (energy lost by the primary ions to
    recoils) over the depth bins, each bin weighted by its width in Angstrom (see
    :func:`_bin_geometry`).

    Returns
    -------
    dict
        ``energy_to_recoils_eV_per_ion`` (eV per incident ion)
    """
    df = results.etorecoils.df
    _, _, widths = _bin_geometry(df["depth_A"].to_numpy(dtype=float))
    values = df["ions_eV_per_A_per_ion"].to_numpy(dtype=float)
    return {"energy_to_recoils_eV_per_ion": float(np.sum(values * widths))}


# -- registry of the predefined extractors ---------------------------------------------

register("implant_summary", implant_summary)
register("peak_vacancy_depth", peak_vacancy_depth)
register("sputter_yield", sputter_yield)
register("backscattered_fraction", backscattered_fraction)
register("transmitted_fraction", transmitted_fraction)
register("energy_to_recoils", energy_to_recoils)
register("vacancy_at", vacancy_at, factory=True)
register("ions_in_layer", ions_in_layer, factory=True)
