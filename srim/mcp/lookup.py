"""Looking up elements, isotopes and materials for the agent.

:func:`lookup_element` takes an element with an optional mass number (``"N"``, ``"N15"``,
``"15N"``, ``"N-15"``). :func:`find_material` matches the common materials and the user's own
ones by name or formula, and reads any other parsable formula as a material without a density.
Plain data in and out; nothing here imports Qt or the MCP SDK.
"""

import re
from pathlib import Path

from srim.app.materials import load_user_materials
from srim.core.compounds import formula_elements, predefined_compounds
from srim.core.elementdb import ElementDB
from srim.core.isotopes import isotope_label, stable_isotopes
from srim.mcp.backend import BackendError

# An element symbol, with an optional mass number before it or after it ("15N", "N-15", "N15")
_ELEMENT = re.compile(r"(?:(\d{1,3})-?)?([A-Z][a-z]?)(?:-?(\d{1,3}))?")


def lookup_element(text: str) -> dict:
    """An element, and the isotope when a mass number is given.

    Returns ``{"symbol", "z", "mass_amu", "isotopes"}``: ``mass_amu`` is the named isotope's mass
    or else the natural mass, and ``isotopes`` lists the natural isotopes (most abundant first)
    with ``label``, ``mass_amu`` and ``abundance`` (in %). Raises :class:`BackendError` for an
    unknown element or an isotope that is not natural.
    """
    match = _ELEMENT.fullmatch(text.strip())
    if match is None:
        raise BackendError(f"{text!r} is not an element symbol, for example N, 15N or N-15")
    before, symbol, after = match.groups()
    if before and after:
        raise BackendError(f"{text!r} gives a mass number twice")
    try:
        element = ElementDB.lookup(symbol)
    except KeyError:
        raise BackendError(f"{symbol!r} is not a chemical element") from None

    isotopes = stable_isotopes(symbol)
    mass = float(element["mass"])
    number = before or after
    if number is not None:
        named = [i for i in isotopes if i.mass_number == int(number)]
        if not named:
            natural = ", ".join(isotope_label(i) for i in isotopes) or "none"
            raise BackendError(
                f"{symbol}-{int(number)} is not a natural isotope of {symbol} (natural: {natural})"
            )
        mass = named[0].mass_amu

    return {
        "symbol": symbol,
        "z": int(element["z"]),
        "mass_amu": mass,
        "isotopes": [
            {"label": isotope_label(i), "mass_amu": i.mass_amu, "abundance": i.abundance_percent}
            for i in isotopes
        ],
    }


def _names(name: str) -> set[str]:
    """The names a compound answers to: "Silicon dioxide (fused silica)" is both of its parts."""
    match = re.fullmatch(r"(.*?)\s*\((.*)\)", name)
    if match is None:
        return {name.casefold()}
    return {name.casefold(), match.group(1).strip().casefold(), match.group(2).strip().casefold()}


def _elements(pairs) -> list[dict]:
    return [{"symbol": symbol, "stoich": float(ratio)} for symbol, ratio in pairs]


def find_material(text: str, materials_path: Path | None = None) -> list[dict]:
    """The materials called ``text`` (a name or a formula, any case), or a formula's guess.

    The common materials and the user's own ones (read from ``materials_path``, or the default
    file) match by name, and the common ones also by formula. Each entry is ``{"name",
    "formula", "density_g_cm3", "elements", "source"}``; a user material has no formula
    (None). When nothing matches and ``text`` is a formula of real elements, one entry with
    ``source`` "formula" and no density is returned, so the caller can ask for the density.
    Anything else gives an empty list.
    """
    query = text.strip()
    key = query.casefold()
    found: list[dict] = []
    for compound in predefined_compounds():
        if key in _names(compound.name) | {compound.formula.casefold()}:
            found.append(
                {
                    "name": compound.name,
                    "formula": compound.formula,
                    "density_g_cm3": compound.density_g_cm3,
                    "elements": _elements(formula_elements(compound.formula)),
                    "source": "predefined",
                }
            )
    for material in load_user_materials(materials_path):
        if material.name.casefold() == key:
            found.append(
                {
                    "name": material.name,
                    "formula": None,
                    "density_g_cm3": material.density_g_cm3,
                    "elements": [
                        {"symbol": e["symbol"], "stoich": float(e["stoich"])}
                        for e in material.elements
                    ],
                    "source": "user",
                }
            )
    if found:
        return found

    try:
        pairs = formula_elements(query)
    except ValueError:
        return []
    if not pairs or any(ratio <= 0 for _, ratio in pairs):
        return []
    return [
        {
            "name": None,
            "formula": query,
            "density_g_cm3": None,
            "elements": _elements(pairs),
            "source": "formula",
        }
    ]
