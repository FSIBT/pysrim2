"""The backend the MCP tools call: the run setup now; runs, results and installs come later.

Two backends implement :class:`Backend` with the same results: the headless one
(:class:`srim.mcp.headless.HeadlessBackend`, no window) and the app's, which asks the open window
over a socket. Everything passed in and
returned is plain JSON data. A :class:`BackendError` carries a message for the agent, shown as it
is; :class:`NeedsApp` says the action needs the window. Nothing here imports Qt or the MCP SDK.
"""

from typing import Any, Protocol


class BackendError(Exception):
    """An action failed. The message is for the agent and is shown as it is."""


class NeedsApp(BackendError):
    """The action needs the pysrim2 app (its undo history, its window)."""

    def __init__(self, message: str = "needs the pysrim2 app: open it with pysrim2 app"):
        super().__init__(message)


class Backend(Protocol):
    """The setup of a run, edited and checked by the library.

    Every method that changes the setup applies all its changes or none of them, and returns
    :meth:`get_setup` afterwards. ``problems`` are what the setup still needs (blocking) or the
    library's notes (not blocking).
    """

    #: "headless" or "app"
    mode: str

    def get_setup(self) -> dict:
        """``{"path", "setup", "problems", "modified"}``: the setup in the RunSpec dict layout,
        plus ``"scan"`` (the scan section, or None for a single run)."""

    def set_values(self, assignments: list[str]) -> dict:
        """Apply ``dotted.key=value`` assignments (the ``--set`` form). ``scan.*`` keys change the
        scan. Raises :class:`BackendError` with the library's message."""

    def set_section(self, name: str, value: Any) -> dict:
        """Replace a section: ``"ion"``, ``"target"``, ``"run"`` (the run settings) or ``"scan"``
        (None makes a single run). Raises :class:`BackendError`."""

    def new_setup(self) -> dict:
        """An empty setup with no path."""

    def open_setup(self, path: str) -> dict:
        """Open the ``run.yaml`` at ``path`` (relative to the working directory)."""

    def save_setup(self, path: str | None) -> dict:
        """Save to ``path``, or to the current path when None."""

    def would_replace(self, path: str) -> bool:
        """True when ``path`` exists and this backend did not write it in this session."""

    def has_unsaved_changes(self) -> bool:
        """True when the setup differs from the last opened or saved one."""

    def undo(self) -> dict:
        """Undo the last change. Needs the app."""

    def redo(self) -> dict:
        """Redo the last undone change. Needs the app."""
