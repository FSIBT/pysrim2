"""The run setup the window edits: the document, its problems, undo and open and save.

:class:`DocumentModel` holds the current document (a dict, see :mod:`srim.app.model`) and emits
``changed`` after every change. Each edit goes through :meth:`DocumentModel.edit`, so undo and
redo cover all of them. Opening a file starts a new history; saving copies the histogram files
the setup uses next to ``run.yaml`` (see :func:`srim.app.model.prepare_for_save`), so the saved
setup still opens when its folder moves.
"""

from collections.abc import Callable
from copy import deepcopy
from pathlib import Path

from PySide6.QtCore import QObject, Signal

from srim.app.guidance import Guidance
from srim.app.model import History, new_document, problems, read_setup, write_setup
from srim.scan.executor import MARKER_NAME

LOCKED_TEXT = "the setup is locked while a run is going"


class DocumentModel(QObject):
    """The run setup being edited, with its path, problems and history."""

    changed = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self._doc = new_document()
        self._path: Path | None = None
        self._history = History()
        self._problems: list[Guidance] = []
        self._locked = False
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
    def in_run_folder(self) -> bool:
        """True when :attr:`path` is the ``scan.yaml`` of a run folder (its folder holds the scan
        marker). Such a setup is not saved in place: the run's points belong to it, so Save and
        Start ask for a file of their own first."""
        return self._path is not None and (self._path.parent / MARKER_NAME).is_file()

    @property
    def base_dir(self) -> Path | None:
        """The folder of :attr:`path`, the one relative histogram files are read from."""
        return self._path.parent if self._path is not None else None

    @property
    def problems(self) -> list[Guidance]:
        """What the setup still needs, then what the library refuses or notes."""
        return list(self._problems)

    @property
    def locked(self) -> bool:
        """True while a run goes: the setup cannot be edited, undone, redone, New or Open."""
        return self._locked

    @locked.setter
    def locked(self, value: bool) -> None:
        self._locked = bool(value)

    @property
    def can_undo(self) -> bool:
        return self._history.can_undo

    @property
    def can_redo(self) -> bool:
        return self._history.can_redo

    @property
    def undo_label(self) -> str | None:
        """The label of the step :meth:`undo` reverts (an agent's edit), or None."""
        return self._history.undo_label

    @property
    def redo_label(self) -> str | None:
        """The label of the step :meth:`redo` re-applies, or None."""
        return self._history.redo_label

    @property
    def is_modified(self) -> bool:
        """True when the document differs from the last opened or saved state."""
        return self._history.is_modified(self._doc)

    # --- edits -----------------------------------------------------------------

    def edit(self, fn: Callable[[dict], None], label: str | None = None) -> None:
        """Apply ``fn`` to a copy of the document and make that the current one.

        ``fn`` changes the dict it is given. When ``fn`` raises, nothing changes (the error
        propagates). Otherwise the state before the edit goes on the undo stack with ``label``
        (the agent's edits pass ``"agent: <summary>"``; a user edit has none), and ``changed``
        is emitted once. Raises :class:`RuntimeError` while the setup is :attr:`locked`.
        """
        self._check_unlocked()
        new = deepcopy(self._doc)
        fn(new)
        self._history.push(self._doc, label)
        self._doc = new
        self._refresh()

    def undo(self) -> None:
        self._check_unlocked()
        previous = self._history.undo(self._doc)
        if previous is not None:
            self._doc = previous
            self._refresh()

    def redo(self) -> None:
        self._check_unlocked()
        following = self._history.redo(self._doc)
        if following is not None:
            self._doc = following
            self._refresh()

    def new(self) -> None:
        """An empty setup with no path; the history starts again."""
        self._check_unlocked()
        self._doc = new_document()
        self._path = None
        self._history = History()
        self._refresh()

    # --- open and save ----------------------------------------------------------

    def open(self, path: str | Path) -> None:
        """Read the ``run.yaml`` at ``path``. Raises :class:`~srim.spec.SpecError`.

        A ``scan:`` section becomes the document's ``_scan``. On error the document is
        unchanged. On success the history starts again, so undo cannot return to the document
        that was open before.
        """
        self._check_unlocked()
        path = Path(path).absolute()
        doc = read_setup(path)
        self._doc = doc
        self._path = path
        self._history = History()
        self._history.mark_saved(doc)
        self._refresh()

    def save(self, path: str | Path) -> None:
        """Write the setup to ``path`` as a ``run.yaml`` and make it the current path.

        Raises :class:`~srim.spec.SpecError` for the first blocking problem, or for a histogram
        file that does not exist. The file is written by :func:`srim.app.model.write_setup`.
        """
        path = Path(path).absolute()
        prepared = write_setup(self._doc, path, self.base_dir)
        # The document now refers to the histogram copies, relative to its new folder.
        self._doc = prepared
        self._path = path
        self._history.mark_saved(prepared)
        self._refresh()

    # --- internals ---------------------------------------------------------------

    def _check_unlocked(self) -> None:
        if self._locked:
            raise RuntimeError(LOCKED_TEXT)

    def _refresh(self) -> None:
        self._problems = problems(self._doc, self.base_dir)
        self.changed.emit()
