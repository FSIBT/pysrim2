"""The user's own materials: ``<data dir>/app/materials.json``, a list of entries.

Each entry is a material with a name, a density in g/cm³, a phase and its elements (as
:func:`srim.app.model.new_element` makes them, isotopes included). A file that is missing,
not JSON or not a list reads as no materials, and an entry that is not a mapping, has no
name or density, or has a bad element is skipped; the rest still load. Writes go through
the atomic writer of :mod:`srim.app.settings`. Nothing here imports Qt.
"""

import json
import math
import numbers
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from srim.app.model import new_element
from srim.app.settings import _write_json_atomic
from srim.paths import data_dir

_ELEMENT_NUMBERS = ("stoich", "displacement_eV", "lattice_eV", "surface_eV")


@dataclass
class UserMaterial:
    name: str
    density_g_cm3: float
    phase: int
    elements: list[dict]


def materials_path() -> Path:
    return data_dir() / "app" / "materials.json"


def _real(value: Any) -> bool:
    return isinstance(value, numbers.Real) and not isinstance(value, bool) and math.isfinite(value)


def _element(raw: Any) -> dict | None:
    if not isinstance(raw, dict):
        return None
    symbol = raw.get("symbol")
    if not isinstance(symbol, str) or not symbol:
        return None
    mass = raw.get("mass_amu")
    if mass is not None and not _real(mass):
        return None
    element = new_element(symbol, None if mass is None else float(mass))
    for key in _ELEMENT_NUMBERS:
        if key in raw:
            if not _real(raw[key]):
                return None
            element[key] = float(raw[key])
    return element


def _material(raw: Any) -> UserMaterial | None:
    """The material of one entry, or None when the entry is broken."""
    if not isinstance(raw, dict):
        return None
    name = raw.get("name")
    density = raw.get("density_g_cm3")
    phase = raw.get("phase", 0)
    elements_raw = raw.get("elements")
    if not isinstance(name, str) or not name:
        return None
    if not _real(density) or density <= 0:
        return None
    if isinstance(phase, bool) or not isinstance(phase, int) or phase not in (0, 1):
        return None
    if not isinstance(elements_raw, list) or not elements_raw:
        return None
    elements = [_element(item) for item in elements_raw]
    if any(element is None for element in elements):
        return None
    return UserMaterial(name, float(density), phase, elements)


def _to_json(material: UserMaterial) -> dict:
    return {
        "name": material.name,
        "density_g_cm3": material.density_g_cm3,
        "phase": material.phase,
        "elements": material.elements,
    }


def load_user_materials(path: Path | None = None) -> list[UserMaterial]:
    """The saved materials in file order; a broken file or entry gives none or is skipped."""
    path = path or materials_path()
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return []
    if not isinstance(data, list):
        return []
    return [m for m in (_material(raw) for raw in data) if m is not None]


def save_user_material(material: UserMaterial, path: Path | None = None) -> None:
    """Save ``material``, replacing the one of the same name (in its place) or adding it last.

    Raises ValueError for a material that the file would not load (no name, a density that
    is not positive, or no elements).
    """
    if _material(_to_json(material)) is None:
        raise ValueError("a material needs a name, a positive density and at least one element")
    path = path or materials_path()
    out: list[UserMaterial] = []
    placed = False
    for existing in load_user_materials(path):
        if existing.name == material.name:
            if not placed:
                out.append(material)
                placed = True
        else:
            out.append(existing)
    if not placed:
        out.append(material)
    _write_json_atomic(path, [_to_json(m) for m in out], ".materials-")


def delete_user_material(name: str, path: Path | None = None) -> None:
    """Remove the material called ``name``; nothing is written when there is none."""
    path = path or materials_path()
    current = load_user_materials(path)
    kept = [m for m in current if m.name != name]
    if len(kept) != len(current):
        _write_json_atomic(path, [_to_json(m) for m in kept], ".materials-")
