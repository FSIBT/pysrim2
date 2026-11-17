"""The headless backend: the run setup edited without a window (Qt-free).

The setup is kept as the app keeps it (see :mod:`srim.app.model`): a dict in the document layout,
with the scan as ``_scan``, and the path it was opened from or saved to. Each change is checked
by the library before it is kept, so a bad value is refused and nothing changes. The checks
fill in what is still missing (in a copy) so that a half-made setup can be checked too. Undo
and redo need the app.
"""

import os
import shutil
from copy import deepcopy
from datetime import UTC, datetime
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
from srim.app.runs import (
    RUNS_SUFFIX,
    ParallelChoice,
    estimate_seconds,
    plan_run,
    run_folder,
    scan_units,
)
from srim.app.scan_model import new_scan, scan_from_section, to_scan
from srim.app.settings import AppSettings
from srim.mcp.backend import BackendError, NeedsApp
from srim.mcp.detached import PID_FILE, run_state, send_control, start_detached
from srim.mcp.limits import default_workers
from srim.notebook import read_run_folder
from srim.scan import Precision, Scan
from srim.scan.executor import mark_scan_folder
from srim.scan.spec import normalise_key
from srim.spec import SpecError, apply_overrides

#: the run settings of ``set_section("run", ...)``: the spec's keys other than ion and target
RUN_KEYS = ("number_ions", "calculation", "seed", "settings", "beam")
#: the scan keys of ``scan.<key>=value``; ``grid.<key>`` takes a list of values
SCAN_KEYS = ("grid", "ions", "batch_ions", "seed", "keep_seed", "extract")
SECTIONS = ("ion", "target", "run", "scan")
#: the status of a run folder whose run never started (it has no runner.json)
NOT_STARTED = "not started"
#: the statuses of a run that is going: its folder is not deleted
LIVE_STATUSES = ("running", "paused")


class HeadlessBackend:
    """The setup of one agent session, with no window."""

    mode = "headless"

    def __init__(self, cwd: Path, settings: AppSettings | None = None, cores: int | None = None):
        self._cwd = Path(cwd).absolute()
        self.settings = settings if settings is not None else AppSettings()
        self._cores = cores if cores is not None else (os.cpu_count() or 1)
        self._doc: dict = new_document()
        self._path: Path | None = None
        self._history = History()
        self._written: set[Path] = set()  # files this backend saved in this session
        self._started: Path | None = None  # the run folder this backend started last

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

    # --- runs ------------------------------------------------------------------------

    def estimate(self, workers: int | None = None, batch_ions: int | None = None) -> dict:
        scan, workers = self._plan(workers, batch_ions)
        return self._estimate(scan, workers)

    def start_run(self, workers: int | None = None, batch_ions: int | None = None) -> dict:
        if self._path is None:
            raise BackendError("save the setup first: save_setup(path)")
        self._plan(workers, batch_ions)  # an incomplete setup is refused before anything is written
        if self.has_unsaved_changes():
            self.save_setup(None)
        scan, workers = self._plan(workers, batch_ions)
        folder = run_folder(self._path, datetime.now(UTC))
        folder.mkdir(parents=True)
        mark_scan_folder(folder)  # the marker first: start_detached needs it
        scan.to_yaml(folder / "scan.yaml")
        start_detached(folder, workers=workers)
        self._started = folder
        return {"folder": str(folder), "estimate": self._estimate(scan, workers)}

    def run_status(self, folder: str | None = None) -> dict:
        return run_state(self._run_folder(folder))

    def control_run(self, action: str, folder: str | None = None) -> dict:
        target = self._run_folder(folder)
        send_control(target, action)
        return {"folder": str(target), "requested": action}

    def list_runs(self, setup_path: str | None = None) -> list[dict]:
        runs = self._runs_dir(setup_path)
        if not runs.is_dir():
            return []
        folders = [p for p in runs.iterdir() if (p / "scan.yaml").is_file()]
        folders.sort(key=_run_order, reverse=True)
        return [{"folder": str(p), "status": _status_of(p)} for p in folders]

    def open_run(self, folder: str) -> dict:
        target = self._resolve(folder)
        if not (target / "scan.yaml").is_file():
            raise BackendError(f"{target} has no scan.yaml: not a run folder")
        try:
            result, finished = read_run_folder(target)
        except Exception as error:  # an unreadable scan is refused with its message
            raise BackendError(f"cannot read {target}: {error}") from error
        all_points = result.scan.points()
        index_of = {point.id: point.index for point in all_points}
        points = []
        for _, row in result.table.iterrows():
            index = index_of[row["id"]]
            path = row.get("path")
            points.append(
                {
                    "index": index,
                    "status": row["status"],
                    "path": path if isinstance(path, str) else None,
                    "params": dict(all_points[index].params),
                }
            )
        points.sort(key=lambda point: point["index"])
        return {"folder": str(target), "finished": finished, "points": points}

    def delete_run(self, folder: str) -> dict:
        target = self._resolve(folder)
        runs = self._runs_dir(None)
        if target.resolve().parent != runs.resolve() or not (target / "scan.yaml").is_file():
            raise BackendError(
                f"{target} is not a run folder of this setup (its runs are in {runs})"
            )
        if (target / PID_FILE).is_file():
            try:
                status = run_state(target)["status"]
            except Exception as error:  # a run that cannot be read may still be going: refused
                raise BackendError(f"cannot tell whether {target} is going: {error}") from error
            if status in LIVE_STATUSES:
                raise BackendError(f"the run in {target} is {status}: stop it first (control_run)")
        shutil.rmtree(target)
        if self._started == target:
            self._started = None
        return {"folder": str(target), "deleted": True}

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

    def _plan(self, workers: int | None, batch_ions: int | None) -> tuple[Scan, int]:
        """The library's scan of the setup, and its workers (the default when None).

        Raises :class:`BackendError` for an incomplete setup or one the library refuses.
        """
        workers = _whole(workers, "workers")
        batch_ions = _whole(batch_ions, "batch_ions")
        base = self._base_dir()
        blocking = [p.text for p in problems(self._doc, base) if p.blocking]
        if blocking:
            raise BackendError("the setup is not complete: " + "; ".join(blocking))
        try:
            scan = plan_run(self._doc, base, ParallelChoice(batch_ions=batch_ions))
        except SpecError as error:
            raise BackendError(str(error)) from error
        if workers is None:
            workers = default_workers(scan_units(scan), self._cores)
        return scan, workers

    def _estimate(self, scan: Scan, workers: int) -> dict:
        return {
            "seconds": estimate_seconds(scan, self.settings.ions_per_s, workers),
            "workers": workers,
            "cores": self._cores,
            "units": scan_units(scan),
            "ions": _total_ions(scan),
        }

    def _runs_dir(self, setup_path: str | None) -> Path:
        """The ``<stem>-runs`` folder of the setup at ``setup_path`` (the current one when None)."""
        setup = self._resolve(setup_path) if setup_path is not None else self._path
        if setup is None:
            raise BackendError("the setup has no file yet: save it, or give its setup_path")
        return setup.parent / f"{setup.stem}{RUNS_SUFFIX}"

    def _run_folder(self, folder: str | None) -> Path:
        """``folder`` as a path; None is the latest run this backend started, else the newest
        run of the setup."""
        if folder is not None:
            return self._resolve(folder)
        if self._started is not None:
            return self._started
        if self._path is not None:
            for run in self.list_runs():
                if run["status"] != NOT_STARTED:
                    return Path(run["folder"])
        raise BackendError("no run: start one with start_run()")

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


def _whole(value: Any, name: str) -> int | None:
    """``value`` as a whole number of at least 1 (None stays None). Raises :class:`BackendError`."""
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, int) or value < 1:
        raise BackendError(f"{name} must be a whole number of at least 1, got {value!r}")
    return value


def _total_ions(scan: Scan) -> int:
    """The ions of all points (a precision target at its ``max_ions``)."""
    per_point = scan.ions.max_ions if isinstance(scan.ions, Precision) else scan.ions
    return per_point * len(scan.points())


def _run_order(folder: Path) -> tuple[str, int]:
    """The sort key of a run folder: its UTC stamp, then its ``-N`` number (none is 1)."""
    stamp, _, number = folder.name.partition("-")
    return stamp, int(number) if number.isdigit() else 1


def _status_of(folder: Path) -> str:
    """The status of a run folder for :meth:`HeadlessBackend.list_runs`."""
    if not (folder / PID_FILE).is_file():
        return NOT_STARTED
    try:
        return run_state(folder)["status"]
    except Exception:  # a folder that cannot be read is listed as such, not left out
        return "unreadable"


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
