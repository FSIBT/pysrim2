"""The run setup the window edits: the document, its problems, undo and open and save.

:class:`DocumentModel` holds the current document (a dict, see :mod:`srim.app.model`) and emits
``changed`` after every change. Each edit goes through :meth:`DocumentModel.edit`, so undo and
redo cover all of them. Opening a file starts a new history; saving copies the histogram files
the setup uses next to ``run.yaml`` (see :func:`srim.app.model.prepare_for_save`), so the saved
setup still opens when its folder moves.
"""

import shutil
from collections.abc import Callable
from copy import deepcopy
from pathlib import Path

from PySide6.QtCore import QObject, Signal

import srim
from srim.app.guidance import Guidance
from srim.app.model import History, from_spec, new_document, prepare_for_save, problems, to_spec
from srim.beam.shapes import Histogram
from srim.spec import RunSpec, SpecError


class DocumentModel(QObject):
    """The run setup being edited, with its path, problems and history."""

    changed = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self._doc = new_document()
        self._path: Path | None = None
        self._history = History()
        self._problems: list[Guidance] = []
        self._refresh()

    # --- state -----------------------------------------------------------------

    @property
    def doc(self) -> dict:
        """The current document. Change it only through :meth:`edit`."""
        return self._doc

    @property
    def path(self) -> Path | None:
        return self._path

    @property
    def base_dir(self) -> Path | None:
        """The folder of :attr:`path`, the one relative histogram files are read from."""
        return self._path.parent if self._path is not None else None

    @property
    def problems(self) -> list[Guidance]:
        """What the setup still needs, then what the library refuses or notes."""
        return list(self._problems)

    @property
    def can_undo(self) -> bool:
        return self._history.can_undo

    @property
    def can_redo(self) -> bool:
        return self._history.can_redo

    @property
    def is_modified(self) -> bool:
        """True when the document differs from the last opened or saved state."""
        return self._history.is_modified(self._doc)

    # --- edits -----------------------------------------------------------------

    def edit(self, fn: Callable[[dict], None]) -> None:
        """Apply ``fn`` to a copy of the document and make that the current one.

        ``fn`` changes the dict it is given. When ``fn`` raises, nothing changes (the error
        propagates). Otherwise the state before the edit goes on the undo stack, and ``changed``
        is emitted once.
        """
        new = deepcopy(self._doc)
        fn(new)
        self._history.push(self._doc)
        self._doc = new
        self._refresh()

    def undo(self) -> None:
        previous = self._history.undo(self._doc)
        if previous is not None:
            self._doc = previous
            self._refresh()

    def redo(self) -> None:
        following = self._history.redo(self._doc)
        if following is not None:
            self._doc = following
            self._refresh()

    def new(self) -> None:
        """An empty setup with no path; the history starts again."""
        self._doc = new_document()
        self._path = None
        self._history = History()
        self._refresh()

    # --- open and save ----------------------------------------------------------

    def open(self, path: str | Path) -> None:
        """Read the ``run.yaml`` at ``path``. Raises :class:`~srim.spec.SpecError`.

        On error the document is unchanged. On success the history starts again, so undo
        cannot return to the document that was open before.
        """
        path = Path(path).absolute()
        try:
            spec = RunSpec.from_yaml(path)
        except OSError as error:
            raise SpecError(f"cannot read {path}: {error.strerror or error}") from error
        except UnicodeDecodeError as error:
            raise SpecError(f"{path} is not a text file: {error}") from error
        doc = from_spec(spec)
        self._doc = doc
        self._path = path
        self._history = History()
        self._history.mark_saved(doc)
        self._refresh()

    def save(self, path: str | Path) -> None:
        """Write the setup to ``path`` as a ``run.yaml`` and make it the current path.

        Raises :class:`~srim.spec.SpecError` for the first blocking problem, or for a histogram
        file that does not exist. Histogram files are copied to ``beam/`` next to ``path``, and
        the file records them relative to that folder.
        """
        path = Path(path).absolute()
        blocking = [problem for problem in self._problems if problem.blocking]
        if blocking:
            raise SpecError(blocking[0].text)
        folder = path.parent
        base = self.base_dir or Path.cwd()
        resolved = _with_absolute_histograms(self._doc, base)
        prepared, copies = prepare_for_save(resolved, folder)
        for source, dest in copies:
            dest.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(source, dest)
        spec = to_spec(prepared, folder)
        _relative_histograms(spec, folder)
        spec.to_yaml(path, provenance={"app": f"pysrim2 {srim.__version__}"})
        # The document now refers to the copies, relative to its new folder.
        self._doc = prepared
        self._path = path
        self._history.mark_saved(prepared)
        self._refresh()

    # --- internals ---------------------------------------------------------------

    def _refresh(self) -> None:
        self._problems = problems(self._doc, self.base_dir)
        self.changed.emit()


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
