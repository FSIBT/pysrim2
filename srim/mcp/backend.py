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
    #: True when :meth:`open_run` replaces the setup (and its unsaved changes), as the app's does
    open_run_replaces_setup: bool

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

    def summary(self, folder: str | None, point: int | None = None) -> list[dict]:
        """The implant summary of a point, as one row: mean depth and straggle (Å), the lateral
        and radial ranges, and ``capped``. A scan needs ``point``. None for ``folder`` is the
        run of :meth:`run_status`. Raises :class:`BackendError`."""

    def available_plots(self, folder: str | None, point: int | None = None) -> list[dict]:
        """Every plot: ``{"key", "title", "available", "reason"}`` (the reason when it is not
        available). Raises :class:`BackendError`."""

    def table(self, folder: str | None, point: int | None, key: str, max_rows: int = 500) -> dict:
        """The data of a plot: ``{"columns", "rows", "total_rows", "capped"}``, at most
        ``max_rows`` rows (5000 at most). Raises :class:`BackendError`."""

    def plot(self, folder: str | None, point: int | None, key: str) -> dict:
        """Draw a plot to a PNG in the run folder's ``plots`` folder: ``{"path", "shown"}``
        (``shown`` is False here). Raises :class:`BackendError` (matplotlib missing, a bad key)."""

    def export(
        self,
        folder: str | None,
        fmt: str,
        dest: str,
        include_collision: bool = False,
        point: int | None = None,
    ) -> dict:
        """Export the tables to ``csv``, ``parquet`` (folders) or ``hdf5`` (a file) at ``dest``,
        relative to the working directory: ``{"written": [paths]}``. Raises
        :class:`BackendError`. Replacing an existing file is for the caller to ask about."""

    def delete_run(self, folder: str) -> dict:
        """Delete a run folder of the setup. Refuses a running one and any folder that is not one
        of the setup's run folders. Raises :class:`BackendError`."""

    def export_files(
        self,
        folder: str | None,
        fmt: str,
        dest: str,
        include_collision: bool = False,
        point: int | None = None,
    ) -> list[str]:
        """The files :meth:`export` would write, listed without writing anything, so the caller
        can ask before one that exists and was not written in this session is replaced."""

    def status(self) -> dict:
        """``{"mode", "version", "checks", "srim_ready", "srim_problem", "setup", "running"}``:
        the doctor's checks, whether SRIM is usable (and the first problem when it is not), the
        setup's file and the latest run (None when there is none)."""

    def install_plan(self, components: list[str] | None = None) -> list[dict]:
        """One row per installer step of ``components`` (the default ones of this system when
        None): ``{"component", "step", "description", "admin", "commands", "done"}``. Raises
        :class:`BackendError` for an unknown component."""

    def install_step(self, component: str, expected: list[dict] | None = None) -> dict:
        """Run the steps of ``component`` that need no admin rights, in a process of their own.
        Admin steps are never run: their commands come back in ``commands``. ``expected`` are the
        steps the user was shown (``{"step", "commands"}``); a different plan is refused.
        ``{"component", "ran", "commands", "failed", "interrupted", "log"}``. Raises
        :class:`BackendError` for an unknown component or a child that could not report."""
