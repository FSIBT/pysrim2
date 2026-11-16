"""The headless backend: the run setup edited without a window (Qt-free).

The setup is kept as the app keeps it (see :mod:`srim.app.model`): a dict in the document layout,
with the scan as ``_scan``, and the path it was opened from or saved to. Each change is checked
by the library before it is kept, so a bad value is refused and nothing changes. The checks
fill in what is still missing (in a copy) so that a half-made setup can be checked too. Undo
and redo need the app.
"""

from copy import deepcopy
from pathlib import Path
from typing import Any

import yaml

from srim.app.model import (
    History,
    best_unit,
    clean,
    new_document,
    problems,
    read_setup,
    to_spec,
    write_setup,
)
from srim.app.scan_model import new_scan, scan_from_section, to_scan
from srim.app.settings import AppSettings
from srim.mcp.backend import BackendError, NeedsApp
from srim.scan.spec import normalise_key
from srim.spec import SpecError, apply_overrides

#: the run settings of ``set_section("run", ...)``: the spec's keys other than ion and target
RUN_KEYS = ("number_ions", "calculation", "seed", "settings", "beam")
#: the scan keys of ``scan.<key>=value``; ``grid.<key>`` takes a list of values
SCAN_KEYS = ("grid", "ions", "batch_ions", "seed", "keep_seed", "extract")
SECTIONS = ("ion", "target", "run", "scan")


class HeadlessBackend:
    """The setup of one agent session, with no window."""

    mode = "headless"

    def __init__(self, cwd: Path, settings: AppSettings | None = None):
        self._cwd = Path(cwd).absolute()
        self.settings = settings if settings is not None else AppSettings()
        self._doc: dict = new_document()
        self._path: Path | None = None
        self._history = History()
        self._written: set[Path] = set()  # files this backend saved in this session

    # --- reading -------------------------------------------------------------------

    def get_setup(self) -> dict:
        setup = clean(self._doc)
        setup["scan"] = deepcopy(self._doc.get("_scan"))
        found = problems(self._doc, self._base_dir())
        return {
            "path": str(self._path) if self._path is not None else None,
            "setup": setup,
            "problems": [{"text": p.text, "blocking": p.blocking} for p in found],
            "modified": self.has_unsaved_changes(),
        }

    def has_unsaved_changes(self) -> bool:
        return self._history.is_modified(self._doc)

    def would_replace(self, path: str) -> bool:
        target = self._resolve(path)
        return target.exists() and target not in self._written

    # --- changing the setup ---------------------------------------------------------

    def set_values(self, assignments: list[str]) -> dict:
        spec_items, scan_items = [], []
        for item in assignments:
            key = item.partition("=")[0].strip()
            (scan_items if key.startswith("scan.") else spec_items).append(item)
        new = deepcopy(self._doc)
        if spec_items:
            new = self._with_spec_values(new, spec_items)
        for item in scan_items:
            _set_scan_value(new, item)
        self._keep(new)
        return self.get_setup()

    def set_section(self, name: str, value: Any) -> dict:
        new = deepcopy(self._doc)
        if name == "ion":
            new["ion"] = _mapping(value, "ion")
        elif name == "target":
            if not isinstance(value, list):
                raise BackendError("target must be a list of layers")
            new["target"] = [_mapping(layer, "a layer of target") for layer in value]
            _set_units(new["target"])
        elif name == "run":
            _set_run(new, _mapping(value, "run"))
        elif name == "scan":
            _set_scan_section(new, value)
        else:
            raise BackendError(f"unknown section {name!r} (allowed: {', '.join(SECTIONS)})")
        self._keep(new)
        return self.get_setup()

    # --- files -----------------------------------------------------------------------

    def new_setup(self) -> dict:
        self._doc = new_document()
        self._path = None
        self._history = History()
        return self.get_setup()

    def open_setup(self, path: str) -> dict:
        target = self._resolve(path)
        try:
            doc = read_setup(target)
        except SpecError as error:
            raise BackendError(str(error)) from error
        self._doc = doc
        self._path = target
        self._history = History()
        self._history.mark_saved(doc)
        return self.get_setup()

    def save_setup(self, path: str | None) -> dict:
        if path is None:
            if self._path is None:
                raise BackendError("the setup has no file yet: give a path to save it to")
            target = self._path
        else:
            target = self._resolve(path)
        try:
            saved = write_setup(self._doc, target, self._base_dir())
        except SpecError as error:
            raise BackendError(str(error)) from error
        self._doc = saved
        self._path = target
        self._written.add(target)
        self._history.mark_saved(saved)
        return self.get_setup()

    # --- undo and redo (the app's) ----------------------------------------------------

    def undo(self) -> dict:
        raise NeedsApp()

    def redo(self) -> dict:
        raise NeedsApp()

    # --- internals ---------------------------------------------------------------------

    def _resolve(self, path: str) -> Path:
        """``path`` as an absolute path, relative to the working directory."""
        return (self._cwd / Path(path).expanduser()).absolute()

    def _base_dir(self) -> Path:
        """The folder relative histogram files are read from: the setup's, else the working dir."""
        return self._path.parent if self._path is not None else self._cwd

    def _with_spec_values(self, doc: dict, items: list[str]) -> dict:
        """``doc`` with the ``dotted.key=value`` items applied to its spec, app keys kept."""
        data = clean(doc)
        try:
            apply_overrides(data, items)
        except SpecError as error:
            raise BackendError(str(error)) from error
        return _keep_app_keys(data, doc)

    def _keep(self, new: dict) -> None:
        """Check ``new`` with the library, then make it the setup. Raises :class:`BackendError`."""
        _check(new, self._base_dir())
        self._doc = new


# --- helpers ---------------------------------------------------------------------------


def _mapping(value: Any, name: str) -> dict:
    if not isinstance(value, dict):
        raise BackendError(f"{name} must be a mapping")
    return deepcopy(value)


def _unit_for(layer: dict) -> str:
    thickness = layer.get("thickness_nm")
    if isinstance(thickness, (int, float)) and not isinstance(thickness, bool):
        return best_unit(thickness)
    return "nm"


def _set_units(layers: list[dict]) -> None:
    """Give each layer its display unit, unless it has one."""
    for layer in layers:
        layer.setdefault("_unit", _unit_for(layer))


def _keep_app_keys(new: dict, old: dict) -> dict:
    """``new`` (a plain spec) with the app's keys of ``old``: the scan, and each layer's unit."""
    if "_scan" in old:
        new["_scan"] = old["_scan"]
    old_layers = old.get("target") or []
    for index, layer in enumerate(new.get("target") or []):
        if index < len(old_layers) and "_unit" in old_layers[index]:
            layer["_unit"] = old_layers[index]["_unit"]
        else:
            layer["_unit"] = _unit_for(layer)
    return new


def _set_run(doc: dict, value: dict) -> None:
    for key in value:
        if key not in RUN_KEYS:
            raise BackendError(f"unknown run setting {key!r} (allowed: {', '.join(RUN_KEYS)})")
    for key, item in value.items():
        if item is None and key == "beam":
            doc.pop("beam", None)  # no beam options: a plain beam
        else:
            doc[key] = deepcopy(item)


def _set_scan_section(doc: dict, value: Any) -> None:
    if value is None:
        doc.pop("_scan", None)
        return
    if not isinstance(value, dict):
        raise BackendError("scan must be a mapping, or None for a single run")
    try:
        doc["_scan"] = scan_from_section(value)
    except SpecError as error:
        raise BackendError(str(error)) from error


def _set_scan_value(doc: dict, item: str) -> None:
    """Set one ``scan.<key>=<yaml value>`` on the document's scan (a new scan when it has none)."""
    key, sep, raw = item.partition("=")
    if not sep or not key.strip():
        raise BackendError(f"override {item!r} must look like scan.key=value")
    name = key.strip().removeprefix("scan.")
    field, _, sub = name.partition(".")
    try:
        value = yaml.safe_load(raw)
    except yaml.YAMLError as error:
        raise BackendError(f"scan.{name}: the value is not valid YAML: {error}") from error
    scan = deepcopy(doc.get("_scan") or new_scan())
    if field == "grid" and sub:
        if not isinstance(value, list):
            raise BackendError(f"scan.{name} must be a list of values, e.g. [20, 40]")
        scan["grid"] = dict(scan.get("grid") or {})
        scan["grid"][normalise_key(sub)] = value
    elif field in SCAN_KEYS and not sub and field != "grid":
        scan[field] = value
    else:
        raise BackendError(
            f"unknown key 'scan.{name}' (allowed: grid.<key>, "
            f"{', '.join(k for k in SCAN_KEYS if k != 'grid')})"
        )
    doc["_scan"] = scan


def _check(doc: dict, base: Path) -> None:
    """Raise :class:`BackendError` when the library refuses ``doc`` once it is complete.

    What is missing is filled in with placeholders in a copy, so that the values that are set
    are still checked. A missing value is not an error here: the setup shows it as a problem.
    """
    filled = _filled(doc)
    try:
        to_spec(filled, base)
        if "_scan" in filled:
            to_scan(filled, base)
    except SpecError as error:
        raise BackendError(str(error)) from error


def _filled(doc: dict) -> dict:
    """A copy of ``doc`` in which every missing value the library needs has a placeholder."""
    out = clean(doc)
    ion = out.setdefault("ion", {})
    ion["symbol"] = ion.get("symbol") or "H"
    if ion.get("energy_keV") is None:
        ion["energy_keV"] = 1.0
    if out.get("number_ions") is None:
        out["number_ions"] = 1
    if not out.get("target"):
        out["target"] = [{"thickness_nm": 1.0, "density_g_cm3": 1.0, "elements": []}]
    for layer in out["target"]:
        if layer.get("thickness_nm") is None:
            layer["thickness_nm"] = 1.0
        if layer.get("density_g_cm3") is None:
            layer["density_g_cm3"] = 1.0
        if not layer.get("elements"):
            layer["elements"] = [{"symbol": "H", "stoich": 1.0}]
        for element in layer["elements"]:
            element["symbol"] = element.get("symbol") or "H"
            if element.get("stoich") is None:
                element["stoich"] = 1.0
    if "_scan" in doc:
        scan = out["_scan"] = deepcopy(doc["_scan"])
        if scan.get("ions") is None and scan.get("precision") is None:
            scan["ions"] = 1
        if not (scan.get("keep_seed") or scan.get("grid") or scan.get("points")):
            scan["keep_seed"] = True
    return out
