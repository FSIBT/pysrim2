"""The MCP server of ``pysrim2 mcp``: the pysrim2 tools for an AI agent, over stdio.

This is the only module that imports the MCP SDK (``mcp``, version 2.x). ``srim.cli`` imports it
lazily, once :func:`srim.mcp.mcp_available` says the SDK is installed.

Each MCP tool calls one method of :class:`srim.mcp.tools.Tools` on a worker thread (so a slow run
check never blocks the protocol). The client's name goes into the activity log, and the client's
elicitation answers the ask-first questions; a client without elicitation gets the one-time code
flow instead (see :mod:`srim.mcp.tools`). A :class:`BackendError` becomes a tool error with its
message, which the agent shows to the user.

Standard output is the protocol's channel while the server runs: nothing here prints. The SDK's
stdio transport moves descriptor 1 to stderr while it serves, so stray output cannot corrupt the
stream, and logging goes to stderr.
"""

import json
import sys
import threading
import traceback
from collections.abc import Callable
from importlib import resources
from pathlib import Path
from typing import Any

import anyio
from mcp.server.mcpserver import AcceptedElicitation, Context, Image, MCPServer
from mcp.server.mcpserver.exceptions import ToolError
from mcp.shared.exceptions import MCPError, NoBackChannelError
from mcp.types import CallToolResult, TextContent
from pydantic import BaseModel, Field

import srim
from srim.app.settings import AppSettings, load_settings
from srim.installer.skill import skill_text
from srim.mcp.activity import ActivityLog
from srim.mcp.approval import ApprovalCodes
from srim.mcp.backend import Backend, BackendError
from srim.mcp.headless import HeadlessBackend
from srim.mcp.tools import Answer, Ask, Tools

#: how long the user has to answer an ask before it counts as "timed out" (a refusal)
ASK_SECONDS = 300.0
UNKNOWN_CLIENT = "unknown client"
SKILL_URI = "pysrim2://skill"


class Allow(BaseModel):
    """The form of an ask: one yes or no."""

    allow: bool = Field(description="True to allow the action")


def choose_backend(cwd: Path, headless: HeadlessBackend) -> Backend:
    """The backend that answers a call. Only the headless one exists so far."""
    del cwd  # the app backend will look for a running app from here
    return headless


class _State:
    """What one server keeps between calls: the backend, the log, the codes, one call at a time."""

    def __init__(self, cwd: Path, headless: HeadlessBackend, settings: AppSettings):
        self.cwd = cwd
        self.headless = headless
        self.settings = settings
        self.log = ActivityLog()
        self.approvals = ApprovalCodes()
        self._one_at_a_time = threading.Lock()

    async def run(self, ctx: Context, action: Callable[[Tools], Any]) -> Any:
        """``action`` on a worker thread, with this call's tools. A :class:`BackendError` is
        returned to the agent as a tool error with its message."""
        tools = Tools(
            choose_backend(self.cwd, self.headless),
            self.log,
            self.approvals,
            self.settings,
            client=_client_name(ctx),
            ask=_ask_for(ctx, self._one_at_a_time),
            cores=None,  # the backend's own core count: the limit is the library's
        )

        def work() -> Any:
            # one call at a time, except while a question waits for the user (see _ask_for)
            self._one_at_a_time.acquire()
            try:
                return action(tools)
            finally:
                self._one_at_a_time.release()

        try:
            return await anyio.to_thread.run_sync(work)
        except BackendError as error:
            raise ToolError(str(error)) from error
        except Exception as error:  # a bug: the agent gets a short message, the log the traceback
            traceback.print_exception(error, file=sys.stderr)
            raise ToolError(f"{type(error).__name__}: {error}") from error


def build_server(cwd: Path | None = None, *, headless: HeadlessBackend | None = None) -> MCPServer:
    """The pysrim2 MCP server, with its tools, the instructions and the skill resource.

    ``cwd`` is the working directory that relative paths are resolved from (the current one
    when None). ``headless`` replaces the backend, so tests can pass fake checks.
    """
    cwd = Path(cwd) if cwd is not None else Path.cwd()
    settings = load_settings()
    if headless is None:
        headless = HeadlessBackend(cwd, settings=settings)
    state = _State(cwd, headless, settings)
    server = MCPServer(
        "pysrim2",
        instructions=resources.files("srim.mcp")
        .joinpath("instructions.md")
        .read_text(encoding="utf-8"),
        version=srim.__version__,
    )

    @server.tool()
    async def status(ctx: Context) -> dict:
        """Whether SRIM is ready, the doctor's checks, the setup's file and the latest run."""
        return await state.run(ctx, lambda t: t.status())

    @server.tool()
    async def get_setup(ctx: Context) -> dict:
        """The current run setup (the run.yaml layout), its file and what it still needs."""
        return await state.run(ctx, lambda t: t.get_setup())

    @server.tool()
    async def lookup_element(ctx: Context, text: str) -> dict:
        """Look up an element or isotope by its symbol or name (e.g. "N", "N15")."""
        return await state.run(ctx, lambda t: t.lookup_element(text))

    @server.tool()
    async def find_material(ctx: Context, text: str) -> dict:
        """Look up a compound or material with its formula and density (e.g. "SiO2")."""
        return await state.run(ctx, lambda t: t.find_material(text))

    @server.tool()
    async def estimate(
        ctx: Context, workers: int | None = None, batch_ions: int | None = None
    ) -> dict:
        """The time, workers and cores a run of the setup would take. Changes nothing."""
        return await state.run(ctx, lambda t: t.estimate(workers, batch_ions))

    @server.tool()
    async def set_values(ctx: Context, assignments: list[str]) -> dict:
        """Set values of the setup by key, as in the command line: "ion.energy_keV=40",
        "number_ions=1000", "scan.grid.ion.energy_keV=[20, 40]". All or nothing."""
        return await state.run(ctx, lambda t: t.set_values(assignments))

    @server.tool()
    async def set_section(ctx: Context, name: str, value: Any) -> dict:
        """Replace a whole section: "ion", "target" (a list of layers), "run" or "scan"
        (None makes it a single run). All or nothing."""
        return await state.run(ctx, lambda t: t.set_section(name, value))

    @server.tool()
    async def open_setup(ctx: Context, path: str, confirm_code: str | None = None) -> dict:
        """Open a run.yaml (relative paths are from the working directory)."""
        return await state.run(ctx, lambda t: t.open_setup(path, confirm_code))

    @server.tool()
    async def new_setup(ctx: Context, confirm_code: str | None = None) -> dict:
        """Start an empty setup (asks first when the current one has unsaved changes)."""
        return await state.run(ctx, lambda t: t.new_setup(confirm_code))

    @server.tool()
    async def save_setup(
        ctx: Context, path: str | None = None, confirm_code: str | None = None
    ) -> dict:
        """Save the setup to a run.yaml (its own file when no path is given). A run needs one."""
        return await state.run(ctx, lambda t: t.save_setup(path, confirm_code))

    @server.tool()
    async def start_run(
        ctx: Context,
        workers: int | None = None,
        batch_ions: int | None = None,
        confirm_code: str | None = None,
    ) -> dict:
        """Start a run of the saved setup in its own run folder. Asks first when the run is
        estimated over 30 minutes or wants more than half the cores."""
        return await state.run(ctx, lambda t: t.start_run(workers, batch_ions, confirm_code))

    @server.tool()
    async def run_status(ctx: Context, folder: str | None = None) -> dict:
        """The state of a run (its progress and points). Default: the latest run."""
        return await state.run(ctx, lambda t: t.run_status(folder))

    @server.tool()
    async def control_run(ctx: Context, action: str, folder: str | None = None) -> dict:
        """Pause, resume or stop a run. action is "pause", "resume" or "stop"."""
        return await state.run(ctx, lambda t: t.control_run(action, folder))

    @server.tool()
    async def list_runs(ctx: Context, setup_path: str | None = None) -> dict:
        """The run folders of a setup, newest first, with their status."""
        return await state.run(ctx, lambda t: t.list_runs(setup_path))

    @server.tool()
    async def open_run(ctx: Context, folder: str) -> dict:
        """Read an earlier run folder: whether it finished and its points."""
        return await state.run(ctx, lambda t: t.open_run(folder))

    @server.tool()
    async def delete_run(ctx: Context, folder: str, confirm_code: str | None = None) -> dict:
        """Delete a run folder of the setup (always asks first). A running one is refused."""
        return await state.run(ctx, lambda t: t.delete_run(folder, confirm_code))

    @server.tool()
    async def summary(ctx: Context, folder: str | None = None, point: int | None = None) -> dict:
        """The implant summary of a run: mean depth and straggle per layer. A scan needs a point."""
        return await state.run(ctx, lambda t: t.summary(folder, point))

    @server.tool()
    async def available_plots(
        ctx: Context, folder: str | None = None, point: int | None = None
    ) -> dict:
        """The plots of a run, with their keys, and whether each one is available."""
        return await state.run(ctx, lambda t: t.available_plots(folder, point))

    @server.tool()
    async def table(
        ctx: Context,
        folder: str | None = None,
        point: int | None = None,
        key: str = "",
        max_rows: int = 500,
    ) -> dict:
        """The data behind a plot key, at most max_rows rows (5000 at most)."""
        return await state.run(ctx, lambda t: t.table(folder, point, key, max_rows))

    @server.tool(structured_output=False)
    async def plot(
        ctx: Context, folder: str | None = None, point: int | None = None, key: str = ""
    ) -> CallToolResult:
        """Draw a plot of a run. Returns the picture and its details (the saved file's path)."""
        details = await state.run(ctx, lambda t: t.plot(folder, point, key))
        png = await anyio.to_thread.run_sync(Path(details["path"]).read_bytes)
        return CallToolResult(
            content=[
                Image(data=png, format="png").to_image_content(),
                TextContent(type="text", text=json.dumps(details, sort_keys=True)),
            ]
        )

    @server.tool()
    async def export(
        ctx: Context,
        folder: str | None,
        fmt: str,
        dest: str,
        include_collision: bool = False,
        point: int | None = None,
        confirm_code: str | None = None,
    ) -> dict:
        """Export a run's tables as csv, parquet or hdf5 to dest. Asks first before it replaces
        a file this session did not write."""
        return await state.run(
            ctx,
            lambda t: t.export(folder, fmt, dest, include_collision, point, confirm_code),
        )

    @server.tool()
    async def install_plan(ctx: Context, components: list[str] | None = None) -> dict:
        """The installer steps still to do for the components (the default ones when none),
        with their commands. Admin steps are listed, never run."""
        return await state.run(ctx, lambda t: t.install_plan(components))

    @server.tool()
    async def install_step(ctx: Context, component: str, confirm_code: str | None = None) -> dict:
        """Run the non-admin installer steps of one component (always asks first)."""
        return await state.run(ctx, lambda t: t.install_step(component, confirm_code))

    @server.tool()
    async def undo(ctx: Context) -> dict:
        """Undo the last change to the setup. Needs the pysrim2 app."""
        return await state.run(ctx, lambda t: t.undo())

    @server.tool()
    async def redo(ctx: Context) -> dict:
        """Redo the last undone change to the setup. Needs the pysrim2 app."""
        return await state.run(ctx, lambda t: t.redo())

    @server.resource(
        SKILL_URI,
        name="skill",
        title="The pysrim2 skill",
        description="How to use pysrim2 and its tools: the guide for agents.",
        mime_type="text/markdown",
    )
    def skill() -> str:
        return skill_text()

    return server


def _client_name(ctx: Context) -> str:
    """The client's name and version from its initialize request, for the activity log."""
    params = ctx.session.client_params
    info = params.client_info if params is not None else None
    if info is None:
        return UNKNOWN_CLIENT
    return f"{info.name} {info.version}".strip()


def _ask_for(ctx: Context, one_at_a_time: threading.Lock) -> Ask | None:
    """The question to ask the user through the client, or None when the client cannot answer
    questions (it did not declare elicitation). Called on the event loop; the question itself
    runs in the worker thread that is making the call. The lock is released while the user
    answers, so the other tools go on; the caller checks the setup again once the answer is in."""
    capabilities = ctx.client_capabilities
    if capabilities is None or capabilities.elicitation is None:
        return None

    def ask(text: str) -> Answer | None:
        one_at_a_time.release()
        try:
            return anyio.from_thread.run(_answer, ctx, text)
        finally:
            one_at_a_time.acquire()

    return ask


async def _answer(ctx: Context, text: str) -> Answer | None:
    """The user's answer to ``text``, through the client's elicitation. Only an explicit yes
    allows; a refusal, a cancel, a malformed answer or no answer in time does not. None when
    the protocol cannot carry the question mid-call (2026-07-28 and later have no back channel
    for server requests): the caller then uses the one-time code."""
    with anyio.move_on_after(ASK_SECONDS):
        try:
            result = await ctx.elicit(
                message=f"pysrim2 agent request: {text}. Allow it?", schema=Allow
            )
        except NoBackChannelError:
            return None
        except (MCPError, ValueError):  # the client refused the form, or answered badly: no
            return "denied"
        if isinstance(result, AcceptedElicitation) and result.data.allow:
            return "allowed"
        return "denied"
    return "timed out"


def main() -> int:
    """Serve the tools over stdio until the client goes away. Returns 0."""
    build_server(Path.cwd()).run("stdio")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
