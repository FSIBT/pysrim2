"""The scan section of the run setup, as plain data (the document's ``_scan`` key).

A single run has no ``_scan``. A scan has one: the ``scan:`` section of ``run.yaml`` as plain
data, with grid keys in the ``--set`` form (``target.0.thickness_nm``), ``None`` for a value
still missing, and ``precision`` present only for a precision target. :func:`to_scan` builds the
library's :class:`~srim.scan.Scan` from it, and the library is the judge of every value.

Nothing here imports Qt. :mod:`srim.app.model` calls :func:`scan_missing` and :func:`to_scan`
from its ``problems``, so this module imports the model's helpers lazily or not at all there.
"""

from collections.abc import Mapping
from copy import deepcopy
from pathlib import Path
from typing import Any

import yaml

from srim.app.guidance import Guidance
from srim.app.model import layer_label, to_spec
from srim.scan import Precision, Scan
from srim.scan.spec import _SECTION_KEYS, DEFAULT_EXTRACT, normalise_key
from srim.spec import SpecError

#: the beam parameters the Scan tab offers, with their labels (layer parameters follow)
SCAN_KEYS = (
    ("ion.energy_keV", "Ion energy (keV)"),
    ("ion.angle_deg", "Ion angle (degrees)"),
)
#: the parameters of each layer, as ``target.<index>.<field>``, with their labels
LAYER_SCAN_FIELDS = (
    ("thickness_nm", "thickness (nm)"),
    ("density_g_cm3", "density (g/cm3)"),
)


def new_scan() -> dict:
    """An empty scan (what ticking "Run as a scan" adds): nothing to vary, no ions yet, and the
    default extractor. The other keys of the section are left out until the user sets them."""
    return {"grid": {}, "extract": list(DEFAULT_EXTRACT), "ions": None}


def scan_keys(doc: dict) -> list[tuple[str, str]]:
    """The (key, label) pairs the Scan tab offers for ``doc``: the beam, then each layer."""
    keys = list(SCAN_KEYS)
    for index, layer in enumerate(doc.get("target") or []):
        for field, label in LAYER_SCAN_FIELDS:
            keys.append(
                (
                    f"target.{index}.{field}",
                    f"Layer {index + 1} ({layer_label(layer)}): {label}",
                )
            )
    return keys


def values_from_range(start: float, stop: float, steps: int, log: bool) -> list[float]:
    """``steps`` values from ``start`` to ``stop`` (both included), evenly spaced in value or,
    with ``log``, in log10. ``steps`` must be at least 2; ``log`` needs both ends above zero.
    Raises :class:`ValueError` otherwise.
    """
    if isinstance(steps, bool) or not isinstance(steps, int) or steps < 2:
        raise ValueError(f"steps must be a whole number of at least 2, got {steps!r}")
    if log and not (start > 0 and stop > 0):
        raise ValueError("a logarithmic range needs a start and a stop above zero")
    last = steps - 1
    values = []
    for i in range(steps):
        if i == 0:
            values.append(float(start))
        elif i == last:
            values.append(float(stop))
        elif log:
            values.append(float(start) * (float(stop) / float(start)) ** (i / last))
        else:
            values.append(float(start) + (float(stop) - float(start)) * i / last)
    return values


def _grid_from_section(grid: Any) -> dict:
    if not isinstance(grid, Mapping):
        raise SpecError("scan.grid must be a mapping of keys to lists of values")
    out: dict[str, Any] = {}
    for key, values in grid.items():
        name = normalise_key(key)
        if name in out:
            raise SpecError(f"scan.grid.{name}: the key is given twice (after normalising it)")
        out[name] = deepcopy(values)
    return out


def _points_from_section(points: Any) -> Any:
    if not isinstance(points, list):
        return deepcopy(points)  # the library names the error
    out = []
    for row in points:
        if isinstance(row, Mapping):
            out.append({normalise_key(key): deepcopy(value) for key, value in row.items()})
        else:
            out.append(deepcopy(row))
    return out


def scan_from_section(section: Mapping) -> dict:
    """The ``_scan`` value of a ``scan:`` section read from a file (keys normalised).

    Keys the library does not know, and both ``ions`` and ``precision``, are refused here, so a
    setup never silently drops part of a scan file. Values are kept as they are: the library
    judges them in :func:`scan_missing` and :func:`to_scan`. Raises :class:`SpecError`.
    """
    for key in section:
        if key not in _SECTION_KEYS:
            raise SpecError(f"unknown key 'scan.{key}' (allowed: {', '.join(_SECTION_KEYS)})")
    if "ions" in section and "precision" in section:
        raise SpecError("scan: give either ions or precision, not both")
    scan = new_scan()
    if section.get("grid") is not None:
        scan["grid"] = _grid_from_section(section["grid"])
    if section.get("points") is not None:
        scan["points"] = _points_from_section(section["points"])
    if section.get("extract") is not None:
        scan["extract"] = deepcopy(section["extract"])
    scan["ions"] = section.get("ions")
    if section.get("precision") is not None:
        scan["precision"] = deepcopy(section["precision"])
    scan["batch_ions"] = section.get("batch_ions")
    scan["seed"] = section.get("seed")
    scan["keep_seed"] = section.get("keep_seed", False)
    return scan


def section_from_file(path: str | Path) -> dict | None:
    """The raw ``scan:`` mapping of the ``run.yaml`` at ``path``, or None for a plain run.

    Raises :class:`SpecError` when the section is not a mapping.
    """
    document = yaml.safe_load(Path(path).read_text(encoding="utf-8"))
    section = document.get("scan") if isinstance(document, dict) else None
    if section is None:
        return None
    if not isinstance(section, Mapping):
        raise SpecError("scan must be a mapping")
    return dict(section)


def scan_missing(doc: dict) -> list[Guidance]:
    """What the scan still needs from the user (the library is not asked yet)."""
    scan = doc["_scan"]
    out = []
    # a one-point keep_seed scan needs no value to vary: its one point is the base run
    if not scan.get("keep_seed") and not scan.get("grid") and not scan.get("points"):
        out.append(Guidance("Add a value to vary on the Scan tab", "scan/vary"))
    if scan.get("ions") is None and scan.get("precision") is None:
        out.append(Guidance("Enter the ions per point on the Scan tab", "scan/ions"))
    return out


def to_scan(doc: dict, base_dir: Any = None) -> Scan:
    """The library's :class:`~srim.scan.Scan` of ``doc`` (its base run and its ``_scan``).

    Raises :class:`~srim.spec.SpecError` with the library's key for a value it refuses, and when
    the document has no scan or no ions.
    """
    scan = doc.get("_scan")
    if scan is None:
        raise SpecError("the setup has no scan: add one on the Scan tab")
    base = to_spec(doc, base_dir)
    ions = scan.get("ions")
    if scan.get("precision") is not None:
        ions = Precision.from_dict(scan["precision"], prefix="scan.precision.")
    elif ions is None:
        raise SpecError("scan.ions: enter the ions per point")
    return Scan(
        base,
        grid=scan.get("grid") or None,
        points=scan.get("points"),
        extract=scan.get("extract"),
        ions=ions,
        seed=scan.get("seed"),
        batch_ions=scan.get("batch_ions"),
        keep_seed=scan.get("keep_seed", False),
    )
