"""Run specification: the ``run.yaml`` file.

A run spec holds everything needed to regenerate ``TRIM.IN``; a ``run.yaml`` file
wraps it with informational provenance::

    version: 1
    spec:
      ion: {symbol: B, energy_keV: 20.0, angle_deg: 7.0}
      number_ions: 200
      calculation: 1
      seed: 12345
      settings: {description: pysrim run, ranges: 1}
      target:
        - name: Au
          thickness_nm: 100.0
          density_g_cm3: 19.3
          phase: 0
          elements:
            - {symbol: Au, stoich: 1.0, displacement_eV: 25.0, lattice_eV: 0.0, surface_eV: 3.0}
    provenance: {pysrim2_version: ..., ...}

Units are part of the key names. pysrim's own objects use eV for the ion energy and
Angstrom for layer widths; conversion is exact (1 nm = 10 A, 1 keV = 1000 eV) and done in
decimal arithmetic, so values with a short decimal form (``100.0`` nm, ``20.0`` keV)
give a ``TRIM.IN`` that is byte-identical to the one written for the equivalent
``TRIM(...)`` object.

``settings`` lists only the :class:`~srim.srim.TRIMSettings` values that differ from
their defaults, plus ``description`` and ``ranges`` which are always written. The angle
of incidence is ``ion.angle_deg`` and the random seed is ``seed`` (not in ``settings``).
If a spec has no ``seed``, one is picked when it is loaded, and ``TRIM.run`` records it.
"""

import math
import random
import re
from dataclasses import dataclass, field
from decimal import Decimal
from pathlib import Path

import yaml

SCHEMA_VERSION = 1

#: TRIMSettings keys allowed in ``settings`` -> (type, default)
SETTINGS_SCHEMA = {
    "description": (str, "pysrim run"),
    "reminders": (int, 0),
    "autosave": (int, 0),
    "plot_mode": (int, 5),
    "plot_xmin": (float, 0.0),
    "plot_xmax": (float, 0.0),
    "ranges": (int, 0),
    "backscattered": (int, 0),
    "transmit": (int, 0),
    "sputtered": (int, 0),
    "collisions": (int, 0),
    "exyz": (int, 0),
    "bragg_correction": (float, 1.0),
    "version": (int, 0),
}
#: settings that are always written to the file
_SETTING_HINTS = {
    "ranges": " (must be 0 or 1)",
    "backscattered": " (must be 0 or 1)",
    "transmit": " (must be 0 or 1)",
    "sputtered": " (must be 0 or 1)",
    "reminders": " (must be 0 or 1)",
    "version": " (must be 0 or 1)",
    "collisions": " (must be 0, 1 or 2)",
    "plot_mode": " (must be 0..5)",
    "description": ' (must not contain a double quote ")',
}
ALWAYS_SETTINGS = ("description", "ranges")

_ION = {"symbol": str, "mass_amu": float, "energy_keV": float, "angle_deg": float}
_ELEMENT = {
    "symbol": str,
    "stoich": float,
    "displacement_eV": float,
    "lattice_eV": float,
    "surface_eV": float,
    "mass_amu": float,
}
_LAYER = {
    "name": str,
    "thickness_nm": float,
    "density_g_cm3": float,
    "phase": int,
    "elements": list,
}
_TOP = {
    "version": int,
    "ion": dict,
    "target": list,
    "settings": dict,
    "number_ions": int,
    "calculation": int,
    "seed": int,
}
_REQUIRED = {
    "": ("ion", "target"),
    "ion": ("symbol", "energy_keV"),
    "layer": ("thickness_nm", "density_g_cm3", "elements"),
    "element": ("symbol", "stoich"),
}
_MAX_ANGLE = 90.0


class SpecError(ValueError):
    """A run spec is invalid; the message names the dotted key at fault."""


# --- exact unit conversion -----------------------------------------------------


def _exact(value, factor, op):
    """``value * factor`` or ``value / factor`` computed on the shortest decimal form"""
    d = Decimal(repr(float(value)))
    return float(d * factor if op == "*" else d / factor)


def keV_to_eV(value):
    return _exact(value, 1000, "*")


def eV_to_keV(value):
    return _exact(value, 1000, "/")


def nm_to_A(value):
    return _exact(value, 10, "*")


def A_to_nm(value):
    return _exact(value, 10, "/")


# --- dataclasses -----------------------------------------------------------------


@dataclass
class ElementSpec:
    """One element of a layer. ``mass_amu`` is only given when it differs from the
    element database (as in imported TRIM.IN files)."""

    symbol: str
    stoich: float
    displacement_eV: float = 25.0
    lattice_eV: float = 0.0
    surface_eV: float = 3.0
    mass_amu: float | None = None


@dataclass
class LayerSpec:
    name: str
    thickness_nm: float
    density_g_cm3: float
    phase: int = 0
    elements: list = field(default_factory=list)


@dataclass
class IonSpec:
    symbol: str
    mass_amu: float
    energy_keV: float
    angle_deg: float = 0.0


@dataclass
class RunSpec:
    """A complete run description; see the module docstring for the file layout."""

    version: int
    ion: IonSpec
    target: list
    settings: dict = field(default_factory=dict)
    number_ions: int = 1000
    calculation: int = 1
    seed: int = 0

    # -- dict / yaml ------------------------------------------------------------

    def to_dict(self):
        """The spec as plain Python data (what is stored under ``spec:``, plus ``version``)."""
        layers = []
        for layer in self.target:
            elements = []
            for e in layer.elements:
                item = {
                    "symbol": e.symbol,
                    "stoich": e.stoich,
                    "displacement_eV": e.displacement_eV,
                    "lattice_eV": e.lattice_eV,
                    "surface_eV": e.surface_eV,
                }
                if e.mass_amu is not None:
                    item["mass_amu"] = e.mass_amu
                elements.append(item)
            layers.append(
                {
                    "name": layer.name,
                    "thickness_nm": layer.thickness_nm,
                    "density_g_cm3": layer.density_g_cm3,
                    "phase": layer.phase,
                    "elements": elements,
                }
            )
        ion = {"symbol": self.ion.symbol}
        if self.ion.mass_amu != _db_mass(self.ion.symbol):
            ion["mass_amu"] = self.ion.mass_amu
        ion["energy_keV"] = self.ion.energy_keV
        ion["angle_deg"] = self.ion.angle_deg
        return {
            "version": self.version,
            "ion": ion,
            "number_ions": self.number_ions,
            "calculation": self.calculation,
            "seed": self.seed,
            "settings": dict(self.settings),
            "target": layers,
        }

    @classmethod
    def from_dict(cls, data, _prefix=""):
        """Build and validate a spec from plain data. Raises :class:`SpecError`."""
        spec = _build(data, _prefix)
        _check_values(spec, _prefix)
        return spec

    def validate(self):
        """Raise :class:`SpecError` (naming the dotted key) if the spec is invalid."""
        RunSpec.from_dict(self.to_dict())

    def to_yaml(self, path, provenance=None):
        """Write ``run.yaml``: ``version``, ``spec`` and ``provenance`` (a dict, may be empty)."""
        self.validate()
        spec = self.to_dict()
        document = {
            "version": spec.pop("version"),
            "spec": spec,
            "provenance": dict(provenance or {}),
        }
        text = yaml.safe_dump(document, sort_keys=False, default_flow_style=False)
        Path(path).write_text(text, encoding="utf-8")

    @classmethod
    def from_yaml(cls, path):
        """Read and validate a ``run.yaml``. Raises :class:`SpecError`."""
        try:
            document = yaml.safe_load(Path(path).read_text(encoding="utf-8"))
        except yaml.YAMLError as exc:
            raise SpecError(f"{path}: not valid YAML: {exc}") from exc
        if not isinstance(document, dict):
            raise SpecError("run file must be a mapping with the keys 'version' and 'spec'")
        _check_keys(document, ("version", "spec", "provenance"), "")
        if "spec" not in document:
            raise SpecError("missing required key 'spec'")
        spec = document["spec"]
        if not isinstance(spec, dict):
            raise SpecError("'spec' must be a mapping")
        if "version" in document:
            spec = {"version": document["version"], **spec}
        return cls.from_dict(spec, "spec.")

    def with_overrides(self, assignments):
        """A new spec with ``dotted.key=value`` assignments applied.

        Values are converted by the schema (``ion.energy_keV=30`` is a float,
        ``number_ions=500`` an int). List items are addressed by index
        (``target.0.thickness_nm=50``).
        """
        data = self.to_dict()
        touched = {}  # id(mapping) -> (mapping, keys set in this call)
        for item in assignments:
            key, sep, raw = item.partition("=")
            if not sep or not key.strip():
                raise SpecError(f"override {item!r} must look like dotted.key=value")
            node, name = _assign(data, key.strip(), raw)
            touched.setdefault(id(node), (node, set()))[1].add(name)
        # A new element symbol means the old mass no longer applies (unless set as well).
        for node, names in touched.values():
            if "symbol" in names and "mass_amu" not in names:
                node.pop("mass_amu", None)
        return RunSpec.from_dict(data)


# --- parsing and validation ----------------------------------------------------------


def _type_name(kind):
    return {
        int: "an integer",
        float: "a number",
        str: "a string",
        list: "a list",
        dict: "a mapping",
    }[kind]


def _check_type(value, kind, key):
    if kind is float:
        ok = isinstance(value, (int, float)) and not isinstance(value, bool)
        if ok and not math.isfinite(value):
            raise SpecError(f"{key} must be a finite number, got {value!r}")
    elif kind is int:
        ok = isinstance(value, int) and not isinstance(value, bool)
    else:
        ok = isinstance(value, kind)
    if not ok:
        raise SpecError(f"{key} must be {_type_name(kind)}, got {value!r}")
    return float(value) if kind is float else value


def _check_keys(data, allowed, prefix):
    for key in data:
        if key not in allowed:
            raise SpecError(
                f"unknown key '{prefix}{key}' (allowed: {', '.join(sorted(map(str, allowed)))})"
            )


def _require(data, section, prefix):
    for key in _REQUIRED[section]:
        if key not in data:
            raise SpecError(f"missing required key '{prefix}{key}'")


def _mapping(value, key):
    if not isinstance(value, dict):
        raise SpecError(f"{key} must be a mapping, got {value!r}")
    return value


def _db_mass(symbol):
    """Mass from the element table, or None if ``symbol`` is not an element"""
    from .core.elementdb import ElementDB

    try:
        return ElementDB.lookup(symbol)["mass"]
    except (KeyError, ValueError):
        return None


def _symbol_mass(symbol, key):
    from .core.elementdb import ElementDB

    try:
        element = ElementDB.lookup(symbol)
    except (KeyError, ValueError):
        element = None
    if element is None or element["symbol"] != symbol:
        raise SpecError(f"{key} {symbol!r} is not a chemical element symbol")
    return element["mass"]


def _typed(data, schema, prefix):
    out = {}
    for key, value in data.items():
        out[key] = _check_type(value, schema[key], f"{prefix}{key}")
    return out


def _build(data, prefix=""):
    data = _mapping(data, prefix.rstrip(".") or "spec")
    _check_keys(data, _TOP, prefix)
    _require(data, "", prefix)
    top = {k: v for k, v in data.items() if k not in ("ion", "target", "settings")}
    top = _typed(top, _TOP, prefix)
    version = top.get("version", SCHEMA_VERSION)
    if version != SCHEMA_VERSION:
        raise SpecError(f"{prefix}version {version} is not supported (this is version 1)")

    p = f"{prefix}ion."
    ion = _mapping(data["ion"], f"{prefix}ion")
    _check_keys(ion, _ION, p)
    ion = _typed(ion, _ION, p)
    _require(ion, "ion", p)
    mass = _symbol_mass(ion["symbol"], f"{p}symbol")
    mass = ion.get("mass_amu", mass)
    ion_spec = IonSpec(ion["symbol"], mass, ion["energy_keV"], ion.get("angle_deg", 0.0))

    raw_settings = data.get("settings", {})
    p = f"{prefix}settings."
    _check_keys(_mapping(raw_settings, f"{prefix}settings"), SETTINGS_SCHEMA, p)
    settings = {}
    for key, value in raw_settings.items():
        kind = SETTINGS_SCHEMA[key][0]
        if kind is int and isinstance(value, bool):
            value = int(value)
        settings[key] = _check_type(value, kind, f"{p}{key}")

    layers_raw = data["target"]
    if not isinstance(layers_raw, list):
        raise SpecError(f"{prefix}target must be a list of layers, got {layers_raw!r}")
    layers = []
    for i, raw in enumerate(layers_raw):
        layers.append(_build_layer(raw, f"{prefix}target[{i}]."))

    if "seed" in top:
        seed = top["seed"]
    else:
        seed = random.randint(0, 100000)
    return RunSpec(
        version=version,
        ion=ion_spec,
        target=layers,
        settings=settings,
        number_ions=top.get("number_ions", 1000),
        calculation=top.get("calculation", 1),
        seed=seed,
    )


def _build_layer(raw, prefix):
    raw = _mapping(raw, prefix.rstrip("."))
    _check_keys(raw, _LAYER, prefix)
    _require(raw, "layer", prefix)
    layer = _typed(raw, _LAYER, prefix)
    elements = []
    for j, item in enumerate(layer["elements"]):
        ep = f"{prefix}elements[{j}]."
        item = _mapping(item, ep.rstrip("."))
        _check_keys(item, _ELEMENT, ep)
        _require(item, "element", ep)
        item = _typed(item, _ELEMENT, ep)
        _symbol_mass(item["symbol"], f"{ep}symbol")
        elements.append(
            ElementSpec(
                item["symbol"],
                item["stoich"],
                item.get("displacement_eV", 25.0),
                item.get("lattice_eV", 0.0),
                item.get("surface_eV", 3.0),
                item.get("mass_amu"),
            )
        )
    name = layer.get("name")
    if name is None:
        name = "+".join(e.symbol for e in elements)
    return LayerSpec(
        name, layer["thickness_nm"], layer["density_g_cm3"], layer.get("phase", 0), elements
    )


def _check_values(spec, prefix=""):
    def positive(value, key):
        if not value > 0:
            raise SpecError(f"{key} must be > 0, got {value!r}")

    def non_negative(value, key):
        if value < 0:
            raise SpecError(f"{key} must be >= 0, got {value!r}")

    p = f"{prefix}ion."
    positive(spec.ion.energy_keV, p + "energy_keV")
    positive(spec.ion.mass_amu, p + "mass_amu")
    if not 0.0 <= spec.ion.angle_deg < _MAX_ANGLE:
        raise SpecError(f"{p}angle_deg must be in [0, 90), got {spec.ion.angle_deg!r}")
    positive(spec.number_ions, f"{prefix}number_ions")
    if spec.calculation not in range(1, 8):
        raise SpecError(f"{prefix}calculation must be 1..7, got {spec.calculation!r}")
    non_negative(spec.seed, f"{prefix}seed")
    if not spec.target:
        raise SpecError(f"{prefix}target must contain at least one layer")
    for i, layer in enumerate(spec.target):
        lp = f"{prefix}target[{i}]."
        positive(layer.thickness_nm, lp + "thickness_nm")
        positive(layer.density_g_cm3, lp + "density_g_cm3")
        if layer.phase not in (0, 1):
            raise SpecError(f"{lp}phase must be 0 (solid) or 1 (gas), got {layer.phase!r}")
        if '"' in layer.name:
            raise SpecError(f'{lp}name must not contain a double quote (")')
        if not layer.elements:
            raise SpecError(f"{lp}elements must contain at least one element")
        for j, e in enumerate(layer.elements):
            ep = f"{lp}elements[{j}]."
            positive(e.stoich, ep + "stoich")
            non_negative(e.displacement_eV, ep + "displacement_eV")
            non_negative(e.lattice_eV, ep + "lattice_eV")
            non_negative(e.surface_eV, ep + "surface_eV")
            if e.mass_amu is not None:
                positive(e.mass_amu, ep + "mass_amu")
    # Delegate the remaining settings checks (ranges of the integer flags ...) to TRIMSettings.
    from .srim import TRIMSettings

    if spec.settings.get("plot_xmin", 0.0) > spec.settings.get("plot_xmax", 0.0):
        raise SpecError(f"{prefix}settings.plot_xmin must be <= settings.plot_xmax")
    for key, value in spec.settings.items():
        try:
            # plot_xmin must not exceed plot_xmax; checked separately above
            TRIMSettings(**{"plot_xmax": 1e18, **{key: value}})
        except ValueError:
            raise SpecError(
                f"{prefix}settings.{key} has an invalid value {value!r}"
                + _SETTING_HINTS.get(key, "")
            ) from None


# --- --set support ---------------------------------------------------------------


def _assign(data, dotted, raw):
    """Set ``dotted`` (``ion.energy_keV``, ``target.0.elements.1.stoich`` ...) to the
    schema-converted value of the string ``raw`` inside the plain-data spec ``data``.
    ``target[0].thickness_nm`` is accepted for ``target.0.thickness_nm``. Returns the
    mapping that was changed and the key."""
    parts = re.sub(r"\[(\d+)\]", r".\1", dotted).replace("..", ".").split(".")
    node, kind = data, "root"
    schema = {"root": _TOP, "ion": _ION, "layer": _LAYER, "element": _ELEMENT}
    for depth, part in enumerate(parts):
        where = ".".join(parts[: depth + 1])
        last = depth == len(parts) - 1
        if kind in ("target", "elements"):
            if not part.isdigit() or int(part) >= len(node):
                raise SpecError(f"{where}: expected an index 0..{len(node) - 1}")
            index = int(part)
            if last:
                raise SpecError(f"{where} is a mapping; set one of its keys")
            node, kind = node[index], "layer" if kind == "target" else "element"
            continue
        if kind == "settings":
            allowed = {k: v[0] for k, v in SETTINGS_SCHEMA.items()}
        else:
            allowed = schema[kind]
        if part not in allowed:
            raise SpecError(f"unknown key '{where}' (allowed: {', '.join(sorted(allowed))})")
        target_type = allowed[part]
        if not last:
            if target_type not in (dict, list):
                raise SpecError(f"{where} has no sub-keys")
            if part not in node:
                node[part] = target_type()
            node = node[part]
            kind = part
            continue
        node[part] = _convert(raw, target_type, dotted)
        return node, part


def _convert(raw, kind, key):
    if kind in (dict, list):
        raise SpecError(f"{key} is a {kind.__name__}; set one of its keys")
    if kind is str:
        return raw
    text = raw.strip()
    try:
        if kind is int:
            if re.fullmatch(r"[+-]?\d+", text):
                return int(text)
            if text.lower() in ("true", "false"):
                return int(text.lower() == "true")
            raise ValueError
        return float(text)
    except ValueError:
        raise SpecError(f"{key} must be {_type_name(kind)}, got {raw!r}") from None


# --- import of an existing TRIM.IN ---------------------------------------------------


def _numbers(line):
    return [float(t) for t in line.split()]


def _is_numeric_line(line):
    try:
        _numbers(line)
    except ValueError:
        return False
    return bool(line.strip())


def parse_trim_in(path):
    """Turn a ``TRIM.IN`` (written by SRIM or by pysrim) into a :class:`RunSpec`.

    The ``TRIM.IN`` format has no units for the target thickness other than Angstrom, which
    is converted to nm. Element masses that differ from the element database are kept as
    ``mass_amu``. Layers own the elements with a non-zero stoichiometry in their row.
    """
    from .core.elementdb import ElementDB

    text = Path(path).read_bytes().decode("latin-1")
    lines = [ln.rstrip("\r") for ln in text.split("\n")]
    pos = 0

    def fail(message):
        raise SpecError(f"{path}: not a TRIM.IN file ({message})")

    def next_line(expect=None):
        nonlocal pos
        if pos >= len(lines):
            fail(f"unexpected end of file, expected {expect}")
        pos += 1
        return lines[pos - 1]

    def block(header, count=None):
        """Skip the header line, return the following numeric values (any number of lines)."""
        nonlocal pos
        next_line(header)
        values = []
        while pos < len(lines) and _is_numeric_line(lines[pos]):
            values += _numbers(lines[pos])
            pos += 1
            if count is not None and len(values) >= count:
                break
        return values

    next_line("title")
    ion_values = block("ion header", 7)
    if len(ion_values) < 7:
        fail("ion line needs 7 numbers")
    z, mass, energy, angle, number, bragg, autosave = ion_values[:7]
    cascade = block("cascade header", 3)
    if len(cascade) < 3:
        fail("cascade line needs 3 numbers")
    calculation, seed, reminders = (int(v) for v in cascade[:3])
    files = block("diskfiles header", 6)
    if len(files) < 6:
        fail("diskfiles line needs 6 numbers")
    ranges, backscat, transmit, sputter, collisions, exyz = (int(v) for v in files[:6])

    next_line("target header")
    match = re.match(r'\s*"(.*)"\s+(\d+)\s+(\d+)\s*$', next_line("target line"))
    if not match:
        fail("target line must look like '\"description\" n_elements n_layers'")
    description, n_elements, n_layers = match.group(1), int(match.group(2)), int(match.group(3))
    plot = block("plot header", 3)
    if len(plot) < 3:
        fail("plot line needs 3 numbers")

    next_line("elements header")
    atoms = []
    for _ in range(n_elements):
        m = re.match(r"\s*Atom\s+\d+\s*=\s*(\w+)\s*=\s*(\d+)\s+(\S+)", next_line("atom line"))
        if not m:
            fail("atom lines must look like 'Atom 1 = Si = 14 28.0855'")
        atoms.append((m.group(1), float(m.group(3))))
    next_line("layer header 1")
    next_line("layer header 2")
    rows = []
    for _ in range(n_layers):
        m = re.match(r'\s*\d+\s+"(.*)"\s+(.*)$', next_line("layer line"))
        if not m:
            fail("layer lines must look like '1 \"name\" width density stoich ...'")
        numbers = _numbers(m.group(2))
        if len(numbers) != 2 + n_elements:
            fail(f"layer {m.group(1)!r} needs width, density and {n_elements} stoichiometries")
        rows.append((m.group(1), numbers))
    phases = block("phase header", n_layers)
    block("bragg header", n_layers)
    displacement = block("displacement header", n_elements)
    lattice = block("lattice header", n_elements)
    surface = block("surface header", n_elements)
    version = block("version header", 1)
    for values, what in (
        (displacement, "displacement"),
        (lattice, "lattice"),
        (surface, "surface"),
    ):
        if len(values) < n_elements:
            fail(f"{what} energies: expected {n_elements} values")
    if len(phases) < n_layers:
        fail("phases: expected one value per layer")

    layers = []
    for index, (name, numbers) in enumerate(rows):
        elements = []
        for k, stoich in enumerate(numbers[2:]):
            if stoich == 0:
                continue
            symbol, atom_mass = atoms[k]
            try:
                db_mass = ElementDB.lookup(symbol)["mass"]
            except (KeyError, ValueError):
                fail(f"unknown element {symbol!r}")
            elements.append(
                ElementSpec(
                    symbol,
                    stoich,
                    displacement[k],
                    lattice[k],
                    surface[k],
                    None if atom_mass == db_mass else atom_mass,
                )
            )
        layers.append(
            LayerSpec(name, A_to_nm(numbers[0]), numbers[1], int(phases[index]), elements)
        )

    ion_symbol = ElementDB.lookup(int(z))["symbol"]
    settings = {"description": description, "ranges": ranges}
    for key, value in (
        ("reminders", reminders),
        ("autosave", int(autosave)),
        ("plot_mode", int(plot[0])),
        ("plot_xmin", plot[1]),
        ("plot_xmax", plot[2]),
        ("backscattered", backscat),
        ("transmit", transmit),
        ("sputtered", sputter),
        ("collisions", collisions),
        ("exyz", exyz),
        ("bragg_correction", bragg),
        ("version", int(version[0]) if version else 0),
    ):
        if value != SETTINGS_SCHEMA[key][1]:
            settings[key] = value
    spec = RunSpec(
        SCHEMA_VERSION,
        IonSpec(ion_symbol, mass, energy, angle),
        layers,
        settings,
        int(number),
        calculation,
        seed,
    )
    # The spec is a dict-level check of everything above (ranges, types).
    return RunSpec.from_dict(spec.to_dict())
