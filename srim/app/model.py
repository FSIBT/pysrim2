"""The run setup the app edits: a plain dict that becomes a :class:`srim.spec.RunSpec`.

The document has the shape of the spec's dict form (see :meth:`srim.spec.RunSpec.to_dict`),
plus app-only keys that start with ``_`` (the layer's display unit). :func:`clean` drops
those and the empty values, so the spec the library builds is the same as a scripted one.
:func:`problems` says what is missing or refused, in the words the setup shows, and
:class:`History` keeps undo and redo. Nothing here imports Qt, and the library is the only
judge of values: the only rule of its own is "a value is missing".
"""

import filecmp
import re
import shutil
import warnings
from collections.abc import Mapping
from copy import deepcopy
from pathlib import Path
from typing import Any

import srim
from srim.app.guidance import Guidance
from srim.beam.shapes import Histogram
from srim.spec import RunSpec, SpecError

#: nm per display unit (the layer's thickness is shown in the best one)
UNITS = {"Å": 0.1, "nm": 1.0, "µm": 1e3, "mm": 1e6}

EXACT_TEXT = (
    "Exact: each ion gets its own energy, angle and position from the distributions (TRIM.DAT)."
)
BINNED_TEXT = "Binned: the beam is split into bins and SRIM runs once per bin."
BINNED_AUTO = "Automatic chose binned because the monolayer calculation cannot read TRIM.DAT."

_KEY = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*(\[\d+\])?(\.[A-Za-z_][A-Za-z0-9_]*(\[\d+\])?)*")
_BEAM_NUMBERS = ("energy_keV", "angle_deg", "azimuth_deg")
_POSITION = ("y_nm", "z_nm", "depth_nm")
_LAYER_FIELDS = {
    "name": "name",
    "thickness_nm": "thickness",
    "density_g_cm3": "density",
    "phase": "phase",
    "elements": "elements",
}


def best_unit(thickness_nm: float) -> str:
    """The display unit that shows ``thickness_nm`` with the fewest digits."""
    if thickness_nm >= 1e6:
        return "mm"
    if thickness_nm >= 1e3:
        return "µm"
    if thickness_nm >= 1:
        return "nm"
    return "Å"


def new_document() -> dict:
    return {
        "version": 1,
        "ion": {"symbol": None, "energy_keV": None, "angle_deg": 0.0},
        "number_ions": 1000,
        "calculation": 1,
        "seed": 0,
        "settings": {},
        "target": [],
    }


def new_layer() -> dict:
    return {
        "name": "",
        "thickness_nm": None,
        "density_g_cm3": None,
        "phase": 0,
        "elements": [],
        "_unit": "nm",
    }


def new_element(symbol: str, mass_amu: float | None = None) -> dict:
    """An element of a layer; ``mass_amu`` is kept only when given (None is SRIM's natural mass)."""
    element = {
        "symbol": symbol,
        "stoich": 1.0,
        "displacement_eV": 25.0,
        "lattice_eV": 0.0,
        "surface_eV": 3.0,
    }
    if mass_amu is not None:
        element["mass_amu"] = mass_amu
    return element


# --- the document and the spec --------------------------------------------------------


def _without_app_keys(value: Any) -> Any:
    """A copy of ``value`` with every ``_`` key removed, at any depth."""
    if isinstance(value, dict):
        return {
            key: _without_app_keys(item)
            for key, item in value.items()
            if not (isinstance(key, str) and key.startswith("_"))
        }
    if isinstance(value, list):
        return [_without_app_keys(item) for item in value]
    return value


def _state(doc: dict) -> dict:
    """What undo and "modified" compare: the spec as :func:`clean` gives it, and the scan."""
    out = clean(doc)
    if "_scan" in doc:
        out["_scan"] = deepcopy(doc["_scan"])
    return out


def is_distributed(doc: dict) -> bool:
    """True when a beam value is a mapping (a distribution), or azimuth or position is set."""
    ion = doc.get("ion") or {}
    return (
        isinstance(ion.get("energy_keV"), Mapping)
        or isinstance(ion.get("angle_deg"), Mapping)
        or ion.get("azimuth_deg") is not None
        or ion.get("position") is not None
    )


def default_beam_mode(doc: dict) -> tuple[str, str]:
    """The beam mode a distributed setup gets when it has no ``beam`` key, and why."""
    if doc.get("calculation") in (1, 2):
        return ("exact", EXACT_TEXT)
    return ("binned", BINNED_AUTO)


def clean(doc: dict) -> dict:
    """The document as the spec sees it: app keys and empty values dropped.

    A ``beam`` key stays only for a distributed beam; a distributed setup with no ``beam``
    gets the default mode of :func:`default_beam_mode` when that is binned.
    """
    out = _without_app_keys(doc)
    ion = out.get("ion")
    if isinstance(ion, dict):
        if ion.get("mass_amu") is None:
            ion.pop("mass_amu", None)
        for key in ("azimuth_deg", "position"):
            if ion.get(key) is None:
                ion.pop(key, None)
    for layer in out.get("target") or []:
        if isinstance(layer, dict):
            if layer.get("name") == "":
                del layer["name"]
            for element in layer.get("elements") or []:
                if isinstance(element, dict) and element.get("mass_amu") is None:
                    element.pop("mass_amu", None)
    if not is_distributed(out):
        out.pop("beam", None)
    elif "beam" not in out and default_beam_mode(out)[0] == "binned":
        out["beam"] = {"mode": "binned"}
    return out


def to_spec(doc: dict, base_dir: Any = None) -> RunSpec:
    """The :class:`RunSpec` of ``doc``. Raises :class:`~srim.spec.SpecError`."""
    return RunSpec.from_dict(clean(doc), base_dir=base_dir)


def from_spec(spec: RunSpec) -> dict:
    """The document of ``spec``: its dict form, with each layer's display unit."""
    doc = spec.to_dict()
    for layer in doc["target"]:
        layer["_unit"] = best_unit(layer["thickness_nm"])
    return doc


# --- problems ------------------------------------------------------------------------


def target_for_key(key: str) -> str | None:
    """Where a click on a problem about the library key ``key`` goes (a tab and field), or None."""
    key = key.removeprefix("spec.")
    head, _, rest = key.partition(".")
    if head == "ion":
        field, _, sub = rest.partition(".")
        if field in ("symbol", "mass_amu"):
            return "beam/ion"
        if field in _BEAM_NUMBERS:
            return f"beam/{field}"
        if field == "position":
            name = sub.partition(".")[0]
            return f"beam/{name}" if name in _POSITION else "beam/y_nm"
        return None
    if head == "number_ions":
        return "beam/number_ions"
    if head == "scan":
        field = re.split(r"[.\[]", rest, maxsplit=1)[0]
        if field in ("grid", "points", ""):
            return "scan/vary"
        return "scan/precision" if field == "precision" else f"scan/{field}"
    if head == "beam":
        return "beam/mode"
    if head == "calculation":
        return "settings/calculation"
    if head == "seed":
        return "settings/seed"
    if head == "settings":
        name = rest.partition(".")[0]
        return f"settings/{name}" if name else None
    match = re.fullmatch(r"target(?:\[(\d+)\])?", head)
    if match:
        if match.group(1) is None:
            return "geometry"
        index = match.group(1)
        if not rest:
            return f"geometry/{index}"
        field = re.split(r"[.\[]", rest, maxsplit=1)[0]
        mapped = _LAYER_FIELDS.get(field)
        return f"geometry/{index}/{mapped}" if mapped else None
    return None


def layer_label(layer: dict) -> str:
    """The name of a layer, else its element symbols joined by "+", else "new layer"."""
    if layer.get("name"):
        return str(layer["name"])
    symbols = "+".join(e["symbol"] for e in layer.get("elements") or [] if e.get("symbol"))
    return symbols or "new layer"


def _missing(doc: dict) -> list[Guidance]:
    out = []
    ion = doc.get("ion") or {}
    if not ion.get("symbol"):
        out.append(Guidance("Choose the ion: pick an element on the Beam tab", "beam/ion"))
    if ion.get("energy_keV") is None:
        out.append(Guidance("Enter the ion energy on the Beam tab", "beam/energy_keV"))
    if doc.get("number_ions") is None:
        out.append(Guidance("Enter the number of ions on the Beam tab", "beam/number_ions"))
    layers = doc.get("target") or []
    if not layers:
        out.append(Guidance("Add a target layer on the Geometry tab", "geometry"))
    for i, layer in enumerate(layers):
        here = f"Layer {i + 1} ({layer_label(layer)})"
        elements = layer.get("elements") or []
        if not elements:
            out.append(Guidance(f"{here}: add at least one element", f"geometry/{i}/elements"))
        for element in elements:
            if element.get("stoich") is None:
                text = f"{here}: enter the amount of {element.get('symbol')}"
                out.append(Guidance(text, f"geometry/{i}/elements"))
        if layer.get("density_g_cm3") is None:
            out.append(Guidance(f"{here}: enter the density", f"geometry/{i}/density"))
        if layer.get("thickness_nm") is None:
            out.append(Guidance(f"{here}: enter the thickness", f"geometry/{i}/thickness"))
    return out


def problems(doc: dict, base_dir: Any = None) -> list[Guidance]:
    """What the setup still needs, then what the library refuses or warns about.

    Missing values come first and stop the check. Otherwise the library's error (one, with the
    field it names) is listed, then each of its warnings as a note that does not block a run.
    """
    missing = _missing(doc)
    if "_scan" in doc:
        from srim.app import scan_model  # lazy: scan_model imports this module

        missing += scan_model.scan_missing(doc)
    if missing:
        return missing
    found = []
    with warnings.catch_warnings(record=True) as seen:
        warnings.simplefilter("always")
        try:
            to_spec(doc, base_dir)
            if "_scan" in doc:
                scan_model.to_scan(doc, base_dir)
        except SpecError as error:
            text = str(error)
            match = _KEY.match(text)
            found.append(Guidance(text, target_for_key(match.group(0) if match else "")))
    for warning in seen:
        if issubclass(warning.category, UserWarning):
            found.append(Guidance(str(warning.message), "beam/mode", blocking=False))
    return found


# --- undo and redo ---------------------------------------------------------------------


class History:
    """Undo and redo for the document, and whether it differs from the last saved state.

    ``push`` is called with the state *before* an edit. A new edit clears redo. The baseline
    for :meth:`is_modified` is the last :meth:`mark_saved` state, or an empty new document.
    """

    def __init__(self, limit: int = 200):
        self.limit = limit
        self._undo: list[dict] = []
        self._redo: list[dict] = []
        self._saved: dict | None = None

    @property
    def can_undo(self) -> bool:
        return bool(self._undo)

    @property
    def can_redo(self) -> bool:
        return bool(self._redo)

    def push(self, doc: dict) -> None:
        self._undo.append(deepcopy(doc))
        self._redo.clear()
        self._trim(self._undo)

    def undo(self, current: dict) -> dict | None:
        if not self._undo:
            return None
        self._redo.append(deepcopy(current))
        self._trim(self._redo)
        return self._undo.pop()

    def redo(self, current: dict) -> dict | None:
        if not self._redo:
            return None
        self._undo.append(deepcopy(current))
        self._trim(self._undo)
        return self._redo.pop()

    def clear(self) -> None:
        self._undo.clear()
        self._redo.clear()

    def mark_saved(self, current: dict) -> None:
        self._saved = _state(current)

    def is_modified(self, current: dict) -> bool:
        baseline = self._saved if self._saved is not None else _state(new_document())
        return _state(current) != baseline

    def _trim(self, stack: list[dict]) -> None:
        overflow = len(stack) - self.limit
        if overflow > 0:
            del stack[:overflow]


# --- saving a document with its histogram files -----------------------------------------


def _beam_values(doc: dict):
    ion = doc.get("ion") or {}
    for name in _BEAM_NUMBERS:
        yield ion.get(name)
    position = ion.get("position")
    if isinstance(position, Mapping):
        for name in _POSITION:
            yield position.get(name)


def _inside(path: Path, folder: Path) -> bool:
    try:
        path.resolve().relative_to(folder.resolve())
    except ValueError:
        return False
    return True


def _destination(source: Path, beam: Path, claimed: dict[Path, Path]) -> Path:
    """The first ``beam/<name>``, ``<name>-2``, ... that is free or already holds ``source``."""
    n = 1
    while True:
        name = source.name if n == 1 else f"{source.stem}-{n}{source.suffix}"
        dest = beam / name
        if dest not in claimed and (not dest.exists() or filecmp.cmp(dest, source, shallow=False)):
            return dest
        n += 1


def prepare_for_save(doc: dict, folder: Path) -> tuple[dict, list[tuple[Path, Path]]]:
    """A copy of ``doc`` whose histogram files sit in ``folder/beam``, and the copies to make.

    A histogram file outside ``folder`` is copied to ``folder/beam/<name>`` (``-2``, ``-3``...
    before the suffix when another file already has that name) and the copy's document refers
    to it as ``beam/<name>``; a file already inside ``folder`` is left as it is. The same source
    gets one copy. Pass absolute paths. Raises :class:`~srim.spec.SpecError` for a histogram
    file that does not exist.
    """
    folder = Path(folder)
    saved = deepcopy(doc)
    beam = folder / "beam"
    copies: list[tuple[Path, Path]] = []
    claimed: dict[Path, Path] = {}  # destination -> source
    by_source: dict[Path, Path] = {}  # source -> destination
    for value in _beam_values(saved):
        if not isinstance(value, dict) or not isinstance(value.get("histogram"), dict):
            continue
        body = value["histogram"]
        if body.get("file") is None:
            continue
        source = Path(body["file"])
        if _inside(source, folder):
            continue
        if not source.is_file():
            raise SpecError(f"the histogram file {source} cannot be found")
        dest = by_source.get(source)
        if dest is None:
            dest = _destination(source, beam, claimed)
            by_source[source] = dest
            claimed[dest] = source
            copies.append((source, dest))
        body["file"] = dest.relative_to(folder).as_posix()
    return saved, copies


def read_setup(path: Path) -> dict:
    """The document of the ``run.yaml`` at ``path``: its spec in the document layout, and its
    ``scan:`` section as ``_scan`` when the file has a scan (not a single run's wrapper).

    Raises :class:`~srim.spec.SpecError` for a file that cannot be read or is not a setup.
    """
    from srim.app import scan_model  # lazy: scan_model imports this module

    path = Path(path).absolute()
    try:
        spec = RunSpec.from_yaml(path)
        section = scan_model.section_from_file(path)
    except OSError as error:
        raise SpecError(f"cannot read {path}: {error.strerror or error}") from error
    except UnicodeDecodeError as error:
        raise SpecError(f"{path} is not a text file: {error}") from error
    doc = from_spec(spec)
    if section is not None and not scan_model.is_single_run_section(
        section, spec.seed, spec.number_ions
    ):
        doc["_scan"] = scan_model.scan_from_section(section)
    return doc


def write_setup(doc: dict, path: Path, base_dir: Path | None) -> dict:
    """Write ``doc`` to ``path`` as a ``run.yaml`` and return the document as saved.

    ``base_dir`` is the folder relative histogram files are read from (the working directory
    when None). Raises :class:`~srim.spec.SpecError` for the first blocking problem, or for a
    histogram file that does not exist. Histogram files are copied to ``beam/`` next to
    ``path``, and the file records them relative to that folder. A setup with a scan is written
    as a scan's ``run.yaml`` (:meth:`srim.scan.Scan.to_yaml`), otherwise as a plain one. The
    caller takes the returned document as its current state (it refers to the copies).
    """
    from srim.app import scan_model  # lazy: scan_model imports this module

    path = Path(path).absolute()
    blocking = [problem for problem in problems(doc, base_dir) if problem.blocking]
    if blocking:
        raise SpecError(blocking[0].text)
    folder = path.parent
    base = base_dir or Path.cwd()
    resolved = _with_absolute_histograms(doc, base)
    prepared, copies = prepare_for_save(resolved, folder)
    for source, dest in copies:
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(source, dest)
    provenance = {"app": f"pysrim2 {srim.__version__}"}
    if "_scan" in prepared:
        scan = scan_model.to_scan(prepared, folder)
        _relative_histograms(scan.base, folder)
        scan.to_yaml(path, provenance=provenance)
    else:
        spec = to_spec(prepared, folder)
        _relative_histograms(spec, folder)
        spec.to_yaml(path, provenance=provenance)
    return prepared


def _with_absolute_histograms(doc: dict, base: Path) -> dict:
    """A copy of ``doc`` whose relative histogram files are taken from ``base``."""
    out = deepcopy(doc)
    ion = out.get("ion") or {}
    values = [ion.get("energy_keV"), ion.get("angle_deg"), ion.get("azimuth_deg")]
    position = ion.get("position")
    if isinstance(position, dict):
        values += [position.get(name) for name in ("y_nm", "z_nm", "depth_nm")]
    for value in values:
        body = value.get("histogram") if isinstance(value, dict) else None
        if isinstance(body, dict) and body.get("file") is not None:
            file = Path(body["file"])
            if not file.is_absolute():
                body["file"] = str((base / file).absolute())
    return out


def _relative_histograms(spec: RunSpec, folder: Path) -> None:
    """Write the histogram files inside ``folder`` relative to it, so a moved folder still works.

    A histogram read from a file is stored with its absolute path; this changes the path of
    the ones inside ``folder`` to ``beam/<name>`` for the run file that is written next.
    """
    values = [spec.ion.energy_keV, spec.ion.angle_deg, spec.ion.azimuth_deg]
    if spec.ion.position is not None:
        values += [spec.ion.position.y_nm, spec.ion.position.z_nm, spec.ion.position.depth_nm]
    root = folder.resolve()
    for value in values:
        if not isinstance(value, Histogram) or value.file is None:
            continue
        file = Path(value.file).resolve()
        try:
            value.file = file.relative_to(root).as_posix()
        except ValueError:
            pass  # outside the folder: the absolute path stays
