"""Scan definition: a base run spec, the parameters that vary and what to extract.

A scan is a :class:`RunSpec` plus, in a ``run.yaml`` ``scan:`` section (next to ``spec:``)::

    version: 1
    spec: {...}            # the base run; the scan varies some of its values
    scan:
      grid:                # cartesian product, the last key varies fastest
        ion.energy_keV: [10.0, 20.0]
        target[0].thickness_nm: [50.0, 100.0]
      points:              # explicit rows (zipped); combined with each grid combination
        - {ion.angle_deg: 0.0}
      extract: [implant_summary, sputter_yield, {vacancy_at: {depth_nm: 50.0}}]
      ions: 1000           # or: precision: {target: sputter_yield, rel: 0.05, ...}
      batch_ions: 100      # optional: split each point into batches of this many ions
      seed: 1              # optional base seed for the batches

Keys are dotted like ``--set``: ``ion.energy_keV``, ``target.0.thickness_nm``; the form
``target[0].thickness_nm`` is accepted and normalised to ``target.0.thickness_nm``, which is
the name of the parameter column in the results. Values are converted by the run spec's
own schema, so a scan point is exactly the run spec with those values changed.

Every point has a stable id, :func:`spec_hash`: the SHA-256 of the canonical YAML of its
run spec without ``seed`` and ``number_ions`` (those are run choices, see :class:`Scan`).
"""

import hashlib
import itertools
import re
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, replace
from numbers import Integral
from pathlib import Path
from typing import Any

import yaml

from ..spec import RunSpec, SpecError
from .extract import reference, resolve
from .precision import Precision

#: extractors used when ``extract`` is not given
DEFAULT_EXTRACT = ("implant_summary",)
#: run choices that are not scan parameters (ion count: ``ions``/``precision``; seed: ``seed``)
RESERVED_KEYS = {
    "number_ions": "use ions= (or precision=) for the ion count",
    "seed": "use seed= for the base seed",
}
_SECTION_KEYS = ("grid", "points", "extract", "ions", "precision", "batch_ions", "seed")


def normalise_key(key) -> str:
    """``target[0].thickness_nm`` -> ``target.0.thickness_nm`` (the ``--set`` form)"""
    text = re.sub(r"\[(\d+)\]", r".\1", str(key).strip())
    return re.sub(r"\.{2,}", ".", text).strip(".")


def spec_hash(spec: RunSpec) -> str:
    """SHA-256 hex digest of the canonical YAML of ``spec``, without ``seed`` and ``number_ions``.

    Two specs with the same digest produce the same ``TRIM.IN`` apart from the seed and the
    ion count, so the digest (with a seed and an ion count) is a cache key. Beam distributions
    are covered as in :func:`srim.scan.cache.cache_key` (inline histograms, function source).
    """
    from ..beam.spec import fingerprint  # lazy: srim.beam imports srim.scan.extract

    data = spec.to_dict(inline=True)
    for key in RESERVED_KEYS:
        data.pop(key, None)
    digest = fingerprint(spec)
    if digest is not None:  # only with functions, so plain specs keep their ids
        data["function_sources"] = digest
    text = yaml.safe_dump(data, sort_keys=True, default_flow_style=False)
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


@dataclass(frozen=True, eq=False)
class ScanPoint:
    """One point of a scan.

    ``index`` is its position in the scan (grid order), ``params`` maps the normalised keys
    to the values of this point, ``spec`` is the run spec with those values applied and
    with ``number_ions`` set to the ion count of the point (:attr:`Scan.ions`, or the batch
    size for a :class:`Precision`), and ``id`` is :func:`spec_hash` of ``spec`` (which does
    not depend on the ion count).
    """

    index: int
    params: dict
    spec: RunSpec
    id: str


def _plain(value):
    """numpy scalars -> Python scalars (for YAML and for the spec's own conversion)"""
    if hasattr(value, "item") and not isinstance(value, (str, bytes)):
        return value.item()
    return value


def _text(value) -> str:
    return str(_plain(value))


def _apply(base: RunSpec, params: Mapping, where: str) -> RunSpec:
    """``base`` with ``params`` (normalised keys) set; errors start with ``where``"""
    assignments = [f"{key}={_text(value)}" for key, value in params.items()]
    try:
        return base.with_overrides(assignments)
    except SpecError as exc:
        raise SpecError(f"{where}: {exc}") from None


def _check_reserved(key: str, where: str) -> None:
    if key in RESERVED_KEYS:
        raise SpecError(f"{where}: {key!r} is not a scan parameter; {RESERVED_KEYS[key]}")


def _grid(grid) -> dict | None:
    if grid is None:
        return None
    if not isinstance(grid, Mapping):
        raise SpecError(f"scan.grid must be a mapping of keys to lists of values, got {grid!r}")
    out: dict[str, list] = {}
    for key, values in grid.items():
        name = normalise_key(key)
        where = f"scan.grid.{name}"
        if isinstance(values, (str, bytes, Mapping)) or not isinstance(values, Iterable):
            raise SpecError(f"{where} must be a list of values, got {values!r}")
        if name in out:
            raise SpecError(f"{where}: the key is given twice (after normalising it)")
        _check_reserved(name, where)
        values = [_plain(v) for v in values]
        if not values:
            raise SpecError(f"{where} has no values")
        out[name] = values
    return out or None


def _rows(points) -> list[dict] | None:
    if points is None:
        return None
    if hasattr(points, "to_dict") and hasattr(points, "columns"):  # a DataFrame
        records = points.to_dict("records")
    elif isinstance(points, (list, tuple)):
        records = list(points)
    else:
        raise SpecError("scan.points must be a list of mappings or a DataFrame")
    if not records:
        raise SpecError("scan.points is empty")
    rows: list[dict] = []
    for i, record in enumerate(records):
        if not isinstance(record, Mapping):
            raise SpecError(f"scan.points[{i}] must be a mapping, got {record!r}")
        row = {}
        for key, value in record.items():
            name = normalise_key(key)
            if name in row:
                raise SpecError(f"scan.points[{i}]: the key {name!r} is given twice")
            _check_reserved(name, f"scan.points[{i}]")
            row[name] = _plain(value)
        if rows and set(row) != set(rows[0]):
            raise SpecError(
                f"scan.points[{i}] has keys {sorted(row)} but row 0 has {sorted(rows[0])}; "
                "all rows must set the same keys"
            )
        rows.append(row)
    return rows


def _extract(entries) -> list:
    if entries is None:
        return list(DEFAULT_EXTRACT)
    if isinstance(entries, (str, Mapping)) or not isinstance(entries, Iterable):
        raise SpecError(f"scan.extract must be a list of extractors, got {entries!r}")
    out = []
    for i, entry in enumerate(entries):
        if not callable(entry):
            try:
                resolve(entry)
            except (ValueError, KeyError, TypeError) as exc:
                raise SpecError(f"scan.extract[{i}]: {exc}") from None
        out.append(entry)
    return out


def _ions(ions, base: RunSpec):
    if ions is None:
        return base.number_ions
    if isinstance(ions, Precision):
        return ions
    if isinstance(ions, bool) or not isinstance(ions, Integral) or ions < 1:
        raise SpecError(f"scan.ions must be a whole number >= 1 or a Precision, got {ions!r}")
    return int(ions)


def _batch_ions(batch_ions, ions):
    if batch_ions is None:
        return None
    if isinstance(ions, Precision):
        raise SpecError(
            "scan.batch_ions cannot be combined with a precision target: a precision target "
            "already runs in batches (set precision.batch_ions)"
        )
    if isinstance(batch_ions, bool) or not isinstance(batch_ions, Integral) or batch_ions < 1:
        raise SpecError(f"scan.batch_ions must be a whole number >= 1, got {batch_ions!r}")
    return int(batch_ions)


def _seed(seed):
    if seed is None:
        return None
    if isinstance(seed, bool) or not isinstance(seed, Integral) or seed < 0:
        raise SpecError(f"scan.seed must be a whole number >= 0, got {seed!r}")
    return int(seed)


def _check_trim_dat_units(points, ions, batch_ions) -> None:
    """Refuse an exact distributed point whose run is more than one TRIM.DAT file can hold.

    An exact distributed beam is written to TRIM.DAT, which holds at most
    :data:`srim.beam.spec.MAX_TRIM_DAT_IONS` ions, so each run (a unit) must have that many or
    fewer: a fixed count needs ``batch_ions``, a precision target its ``batch_ions``.
    """
    from ..beam.spec import MAX_TRIM_DAT_IONS  # lazy: srim.beam imports srim.scan

    for point in points:
        if not point.spec.is_distributed or is_binned(point.spec):
            continue
        if isinstance(ions, Precision):
            key, limit, fix = "precision.batch_ions", ions.batch_ions, "precision.batch_ions"
        elif batch_ions is not None:
            key, limit, fix = "batch_ions", batch_ions, "batch_ions"
        else:
            key, limit, fix = "ions", point.spec.number_ions, "batch_ions"
        if limit > MAX_TRIM_DAT_IONS:
            raise SpecError(
                f"scan point {point.index}: an exact distributed beam runs in TRIM.DAT files of "
                f"at most {MAX_TRIM_DAT_IONS} ions, and {key} is {limit}; set {fix}: "
                f"{MAX_TRIM_DAT_IONS} (the ions then run as batches), or use beam.mode: binned"
            )


def is_binned(spec: RunSpec) -> bool:
    """True for a point whose beam runs as bins: ``beam.mode: binned`` with a distribution.

    Such a point is run as one SRIM run per bin (the bin units of the executor), so the
    point's ions are split over them; the other points of a scan are not affected.
    """
    return spec.beam is not None and spec.beam.mode == "binned" and spec.is_distributed


def _entry(extractor):
    if not callable(extractor):
        return extractor
    try:
        return reference(extractor)
    except ValueError as exc:
        raise SpecError(f"scan.extract: {exc}") from None


class Scan:
    """A base run spec, the parameters to vary and what to extract from each run.

    Parameters
    ----------
    base : :class:`srim.spec.RunSpec`
        the run that every point starts from
    grid : mapping, optional
        ``{dotted key: [values]}``; the points are the cartesian product of the lists
        (the last key varies fastest)
    points : list of mappings, or DataFrame, optional
        one row per point, all rows with the same keys. With ``grid`` as well, each row is
        combined with each grid combination; a key may be in one of them only.
    extract : list, optional
        extractors: registered names, ``{name: params}`` mappings, or Python callables
        (written to run files as ``python:`` references, readable for module-level
        functions only). Default ``["implant_summary"]``.
    ions : int or :class:`~srim.scan.Precision`, optional
        ions per point, or a precision goal. Default: ``base.number_ions``.
    batch_ions : int, optional
        with a fixed ``ions``: split each point into batches of this many ions (the last one
        may be smaller), each an independent run that the workers share. The point's values
        and their ``<value>_err`` come from the batches (batch means), as for a precision
        target. Default ``None``: one run per point. Not allowed with a precision target.
    seed : int, optional
        base seed of the batches (``None``: the scan draws a random base seed and records
        it in ``scan.yaml``)

    With neither ``grid`` nor ``points`` the scan has the single point ``base``. Keys
    ``number_ions`` and ``seed`` cannot be parameters. Every error names the dotted key
    (``scan.grid.ion.energy_keV``, ``scan.points[1]``) as a :class:`SpecError`.
    """

    def __init__(
        self,
        base,
        grid=None,
        points=None,
        extract=None,
        ions=None,
        seed=None,
        batch_ions=None,
    ):
        if not isinstance(base, RunSpec):
            raise TypeError(f"a scan starts from a RunSpec, not {type(base).__name__}")
        self.base = base
        self.grid = _grid(grid)
        self.rows = _rows(points)
        if self.grid and self.rows:
            both = sorted(set(self.grid) & set(self.rows[0]))
            if both:
                raise SpecError(
                    f"scan: {both[0]!r} is in both grid and points; each key may be in one"
                )
        self.extract = _extract(extract)
        self.ions = _ions(ions, base)
        self.batch_ions = _batch_ions(batch_ions, self.ions)
        self.seed = _seed(seed)
        if self.rows is not None:
            for i, row in enumerate(self.rows):
                _apply(base, row, f"scan.points[{i}]")
        if self.grid is not None:
            for key, values in self.grid.items():
                for value in values:
                    _apply(base, {key: value}, f"scan.grid.{key}")
        self._points = self._expand()
        _check_trim_dat_units(self._points, self.ions, self.batch_ions)

    @property
    def param_names(self) -> list[str]:
        """The scanned keys, in the order of the points' ``params``"""
        names = list(self.rows[0]) if self.rows else []
        return names + list(self.grid or {})

    def points(self) -> list[ScanPoint]:
        """All points, in order. Duplicate points are rejected when the scan is built
        (in ``__init__``), not here."""
        return list(self._points)

    def _ions_per_point(self) -> int:
        if isinstance(self.ions, Precision):
            return self.ions.batch_ions
        return self.ions

    def _expand(self) -> list[ScanPoint]:
        rows = self.rows if self.rows is not None else [{}]
        if self.grid is not None:
            keys = list(self.grid)
            combos = [
                dict(zip(keys, c, strict=True)) for c in itertools.product(*self.grid.values())
            ]
        else:
            combos = [{}]
        points: list[ScanPoint] = []
        first_index: dict[str, int] = {}
        for row in rows:
            for combo in combos:
                params = {**row, **combo}
                index = len(points)
                spec = _apply(self.base, params, f"scan point {index}")
                spec = replace(spec, number_ions=self._ions_per_point())
                digest = spec_hash(spec)
                if digest in first_index:
                    earlier = points[first_index[digest]].params
                    raise SpecError(
                        f"scan points {first_index[digest]} and {index} give the same run "
                        f"spec: {earlier} and {params}"
                    )
                first_index[digest] = index
                points.append(ScanPoint(index, params, spec, digest))
        return points

    # -- plain data and run.yaml -------------------------------------------------------

    def to_dict(self) -> dict:
        """The ``scan:`` section as plain data (Python extractors as ``python:`` references)."""
        out: dict[str, Any] = {}
        if self.grid is not None:
            out["grid"] = {k: list(v) for k, v in self.grid.items()}
        if self.rows is not None:
            out["points"] = [dict(row) for row in self.rows]
        out["extract"] = [_entry(e) for e in self.extract]
        if isinstance(self.ions, Precision):
            out["precision"] = self.ions.to_dict()
        else:
            out["ions"] = self.ions
        if self.batch_ions is not None:
            out["batch_ions"] = self.batch_ions
        if self.seed is not None:
            out["seed"] = self.seed
        return out

    def to_yaml(self, path, provenance=None):
        """Write a ``run.yaml`` with the base spec and this ``scan:`` section.

        Python extractors are written as ``python:<module>:<name>`` (see
        :func:`srim.scan.extract.reference`).
        """
        spec = self.base.to_dict()
        document = {
            "version": spec.pop("version"),
            "spec": spec,
            "provenance": dict(provenance or {}),
            "scan": self.to_dict(),
        }
        Path(path).write_text(
            yaml.safe_dump(document, sort_keys=False, default_flow_style=False),
            encoding="utf-8",
        )

    @classmethod
    def from_yaml(cls, path):
        """Read a ``run.yaml`` with a ``scan:`` section. Raises :class:`SpecError`.

        Extractors written as ``python:<module>:<qualname>`` are imported, which runs that
        module's top-level code: only load scan files you trust.
        """
        base = RunSpec.from_yaml(path)  # validates the spec, the version and the keys
        document = yaml.safe_load(Path(path).read_text(encoding="utf-8"))
        section = document.get("scan")
        if section is None:
            raise SpecError(f"{path}: no 'scan' section (a plain run file: use RunSpec)")
        if not isinstance(section, Mapping):
            raise SpecError("scan must be a mapping")
        for key in section:
            if key not in _SECTION_KEYS:
                raise SpecError(f"unknown key 'scan.{key}' (allowed: {', '.join(_SECTION_KEYS)})")
        if "ions" in section and "precision" in section:
            raise SpecError("scan: give either ions or precision, not both")
        ions = section.get("ions")
        if "precision" in section:
            ions = Precision.from_dict(section["precision"], prefix="scan.precision.")
        return cls(
            base,
            grid=section.get("grid"),
            points=section.get("points"),
            extract=section.get("extract"),
            ions=ions,
            seed=section.get("seed"),
            batch_ions=section.get("batch_ions"),
        )
