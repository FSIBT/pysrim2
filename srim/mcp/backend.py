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

    def estimate(self, workers: int | None = None, batch_ions: int | None = None) -> dict:
        """``{"seconds", "workers", "cores", "units", "ions"}`` of a run of the setup. The workers
        default to :func:`srim.mcp.limits.default_workers`. Raises :class:`BackendError` for an
        incomplete setup."""

    def start_run(self, workers: int | None = None, batch_ions: int | None = None) -> dict:
        """Start a run of the saved setup in a new run folder, detached. Saves an unsaved change
        to the setup's own file first. ``{"folder", "estimate"}``. Raises :class:`BackendError`
        (for one, when the setup has no file yet)."""

    def run_status(self, folder: str | None = None) -> dict:
        """The state of the run in ``folder``: the latest run this backend started, else the
        newest run of the setup. Raises :class:`BackendError` when there is no run."""

    def control_run(self, action: str, folder: str | None = None) -> dict:
        """Ask the run in ``folder`` (the default as in :meth:`run_status`) to ``pause``,
        ``resume`` or ``stop``. Raises :class:`BackendError` when the run is not going."""

    def list_runs(self, setup_path: str | None = None) -> list[dict]:
        """The run folders of a setup (its ``<stem>-runs`` folder, the current setup when None),
        newest first: ``{"folder", "status"}`` each."""

    def open_run(self, folder: str) -> dict:
        """``{"folder", "finished", "points"}`` of a run folder, read back (finished or not)."""

    def delete_run(self, folder: str) -> dict:
        """Delete a run folder of the setup. Refuses a running one and any folder that is not one
        of the setup's run folders. Raises :class:`BackendError`."""
