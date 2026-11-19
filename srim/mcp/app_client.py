"""The client of the open pysrim2 app: ``pysrim2 mcp`` drives the window through it (Qt-free).

:func:`connect_app` finds the app through its discovery file (:mod:`srim.mcp.discovery`), connects
to its socket and checks the token in the hello. It never raises: no usable app is ``None``.

:class:`AppClient` implements :class:`srim.mcp.backend.Backend` with ``mode = "app"``. It sends
each method to the app as a request, except the ones the app does not serve: the results of a run
folder (summary, plot catalogue, tables, plots, export) are computed here from the folder with
:mod:`srim.mcp.results`, a plot is drawn here and then shown by the app, the installer runs in its
own process (through :class:`~srim.mcp.headless.HeadlessBackend`), and a run folder that a
detached runner runs is read and controlled here (:mod:`srim.mcp.detached`).

:class:`AppLink` keeps one client between the tool calls of a server, looks again for the app
after a short wait when it is not there, and tells the agent (in the next result) when the setup
has come back from headless. :class:`AppGone` is raised when the app stops answering: the
connection is closed then, and the next call looks for the app again.

The token is never in an exception, a log line or a repr: the endpoint's repr hides it, and no
error text built here includes it.
"""

import os
import threading
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any, Literal

from srim.mcp import results as results_lib
from srim.mcp.activity import ActivityEntry
from srim.mcp.backend import Backend, BackendError
from srim.mcp.detached import run_state, runner_backend, send_control
from srim.mcp.discovery import read_endpoint
from srim.mcp.headless import HeadlessBackend, delete_run_folder, runs_folder
from srim.mcp.protocol import (
    HELLO_MAX_LINE,
    MAX_CLIENT,
    MAX_LINE,
    Channel,
    ProtocolError,
    exception_for,
    hello_request,
    request,
    socket_connect,
)

#: what the agent is told when the app closed (or its connection failed) during a call
APP_CLOSED_TEXT = "the pysrim2 app closed; call again to continue headless"
#: what the agent is told when the app did not answer in time (it may still have acted)
APP_SILENT_TEXT = "the pysrim2 app did not answer in time; call again to continue headless"
#: the seconds a quick request (a read or an edit of the window) may take to be answered
QUICK_SECONDS = 10.0
#: the seconds a request may take when it reads a run folder, checks the installation or starts
SLOW_SECONDS = 60.0
#: the seconds an ask-first question may wait for the user: the dialog gives up after 120 s, and a
#: question queued behind two others still gets its answer in time
ASK_SECONDS = 360.0
#: the requests that get SLOW_SECONDS
SLOW_METHODS = frozenset(
    {
        "status",
        "run_status",
        "control_run",
        "list_runs",
        "open_run",
        "show_plot",
        "start_run",
        "open_setup",
        "save_setup",
    }
)
#: the requests whose answer is the setup (the app's ``get_setup`` shape), and so are checked
SETUP_METHODS = frozenset(
    {
        "get_setup",
        "set_values",
        "set_section",
        "new_setup",
        "open_setup",
        "save_setup",
        "undo",
        "redo",
    }
)
#: the keys of the app's setup answer
SETUP_KEYS = frozenset({"path", "setup", "problems", "modified"})
#: how long a missing app is not looked for again (seconds)
RETRY_SECONDS = 3.0
#: the name of a client that gave none
DEFAULT_CLIENT = "an agent"
ANSWERS = ("allowed", "denied", "timed out")

Answer = Literal["allowed", "denied", "timed out"]


class AppGone(BackendError):
    """The app stopped answering: it closed, or it did not answer in time. The connection is
    closed by then, and ``silent`` says which of the two it was."""

    def __init__(self, message: str, *, silent: bool = False):
        super().__init__(message)
        self.silent = silent


def connect_app(
    client: str = DEFAULT_CLIENT,
    timeout: float = 2.0,
    cwd: Path | None = None,
    local: HeadlessBackend | None = None,
) -> "AppClient | None":
    """The client of the open app, or None when there is no app that accepts this one.

    ``client`` is the name the app shows (clipped to the protocol's limit). ``cwd`` is the folder
    that relative paths are resolved from (the current one when None). ``local`` replaces the
    headless backend that does the installer steps (tests use fake steps). Never raises.
    """
    try:
        endpoint = read_endpoint()
        if endpoint is None:
            return None
        stream = socket_connect(endpoint.socket, timeout=timeout)
    except Exception:  # no socket, a refused one, one that is not this user's: no app
        return None
    channel = Channel(stream, max_line=HELLO_MAX_LINE, timeout=timeout)
    try:
        channel.send(hello_request(endpoint.token, _client_text(client)))
        reply = channel.receive()
        hello = reply.get("hello") if isinstance(reply, dict) else None
        if not isinstance(hello, dict) or hello.get("ok") is not True:
            raise ValueError("the app refused the hello")
        channel.set_max_line(MAX_LINE)
    except Exception:  # a wrong token, a silent app, a broken line: no usable app
        channel.close()
        return None
    return AppClient(channel, Path(cwd) if cwd is not None else Path.cwd(), local=local)


def _checked_setup(result) -> dict:
    """The app's setup answer, checked: a dict with the setup's keys and their kinds. Raises
    :class:`BackendError` for anything else (the answer is not trusted then)."""
    if not isinstance(result, dict) or not SETUP_KEYS <= result.keys():
        raise BackendError("the app's answer about the setup is not a setup")
    path = result["path"]
    if path is not None and not isinstance(path, str):
        raise BackendError("the app's answer about the setup has a path that is not text")
    if (
        not isinstance(result["setup"], dict)
        or not isinstance(result["problems"], list)
        or not isinstance(result["modified"], bool)
    ):
        raise BackendError("the app's answer about the setup has a wrong part")
    return result


def _client_text(client: str) -> str:
    text = str(client)[:MAX_CLIENT]
    return text if text.strip() else DEFAULT_CLIENT


def _seconds(method: str) -> float:
    """How long the app may take to answer ``method``. Read when called, so tests can shorten it."""
    if method == "ask":
        return ASK_SECONDS
    if method in SLOW_METHODS:
        return SLOW_SECONDS
    return QUICK_SECONDS


class AppClient:
    """The app's backend, as a client over one connection. See the module docstring.

    Requests on one connection are sent one at a time (a lock), so a call and its answer are
    never mixed with another's.
    """

    mode = "app"
    #: opening a run folder replaces the setup in the app, as the window does
    open_run_replaces_setup = True

    def __init__(self, channel: Channel, cwd: Path, local: HeadlessBackend | None = None):
        self._channel = channel
        self._cwd = Path(cwd).absolute()
        self._local = local if local is not None else HeadlessBackend(self._cwd)
        self._lock = threading.Lock()
        self._ids = 0
        self._closed = False
        #: the setup's file as the app last said (None: the app's setup is new and unsaved)
        self.last_setup_path: str | None = None
        #: True once the app has said what its setup is
        self.setup_seen = False
        #: the real paths of the files this client exported (they are no replacements)
        self._written: set[str] = set()

    @property
    def closed(self) -> bool:
        return self._closed

    def close(self) -> None:
        """Close the connection. Safe to repeat."""
        with self._lock:
            self._close()

    def _close(self) -> None:
        if not self._closed:
            self._closed = True
            self._channel.close()

    # --- the connection -------------------------------------------------------------

    def _call(self, method: str, params: dict | None = None) -> Any:
        """Send one request and return its answer. Raises :class:`AppGone` when the app stops
        answering (the connection is closed), the app's :class:`BackendError` for a refusal, and
        :class:`BackendError` when the request cannot be sent."""
        with self._lock:
            if self._closed:
                raise AppGone(APP_CLOSED_TEXT)
            self._ids += 1
            request_id = self._ids
            try:
                self._channel.send(request(request_id, method, params))
            except ProtocolError as error:
                raise BackendError(f"{method}: the request cannot be sent: {error}") from None
            except OSError:
                raise self._gone(APP_CLOSED_TEXT) from None
            self._channel.timeout = _seconds(method)
            try:
                reply = self._channel.receive()
            except TimeoutError:
                raise self._gone(APP_SILENT_TEXT, silent=True) from None
            except (OSError, ProtocolError):
                raise self._gone(APP_CLOSED_TEXT) from None
            if not isinstance(reply, dict) or reply.get("id") != request_id:
                # an answer that is not to this request: nothing else on this connection is trusted
                raise self._gone(APP_CLOSED_TEXT)
        if "error" in reply:
            error = reply["error"]
            if not isinstance(error, dict):
                raise BackendError("the app reported an error")
            raise exception_for(error)
        result = reply.get("result")
        if method in SETUP_METHODS:
            result = _checked_setup(result)
            self.setup_seen = True
            self.last_setup_path = result["path"]
        return result

    def _gone(self, text: str, *, silent: bool = False) -> AppGone:
        """Close the connection and return the error that says so (raise it)."""
        self._close()
        return AppGone(text, silent=silent)

    def _resolve(self, path: str) -> Path:
        """``path`` as an absolute path, relative to the working directory."""
        return (self._cwd / Path(path).expanduser()).absolute()

    def _abs(self, path: str | None) -> str | None:
        return None if path is None else str(self._resolve(path))

    def _folder(self, folder: str | None) -> Path:
        """The run folder: ``folder`` as a path, or the app's latest run when None."""
        if folder is not None:
            return self._resolve(folder)
        return Path(self.run_status(None)["folder"])

    def _is_detached(self, folder: Path) -> bool:
        """True when a run folder's runner is not the app's (a detached run, read here)."""
        backend = runner_backend(folder)
        return backend is not None and backend != "app"

    # --- the setup (sent to the app) -------------------------------------------------

    def get_setup(self) -> dict:
        return self._call("get_setup")

    def has_unsaved_changes(self) -> bool:
        # only an answer of exactly False says there is nothing unsaved (see would_replace)
        return self._call("has_unsaved_changes") is not False

    def would_replace(self, path: str) -> bool:
        target = self._resolve(path)
        if not os.path.islink(target) and os.path.realpath(target) in self._written:
            return False  # a file this client wrote is this session's
        # only an answer of exactly False says the file is not replaced: anything else is a yes
        return self._call("would_replace", {"path": str(target)}) is not False

    def set_values(self, assignments: list[str]) -> dict:
        return self._call("set_values", {"assignments": assignments})

    def set_section(self, name: str, value: Any) -> dict:
        return self._call("set_section", {"name": name, "value": value})

    def new_setup(self) -> dict:
        return self._call("new_setup")

    def open_setup(self, path: str) -> dict:
        return self._call("open_setup", {"path": self._abs(path)})

    def save_setup(self, path: str | None) -> dict:
        return self._call("save_setup", {"path": self._abs(path)})

    def undo(self) -> dict:
        return self._call("undo")

    def redo(self) -> dict:
        return self._call("redo")

    def status(self) -> dict:
        return self._call("status")

    def estimate(self, workers: int | None = None, batch_ions: int | None = None) -> dict:
        return self._call("estimate", {"workers": workers, "batch_ions": batch_ions})

    def start_run(self, workers: int | None = None, batch_ions: int | None = None) -> dict:
        return self._call("start_run", {"workers": workers, "batch_ions": batch_ions})

    def run_status(self, folder: str | None = None) -> dict:
        if folder is None:
            return self._call("run_status", {"folder": None})
        target = self._resolve(folder)
        if self._is_detached(target):
            return run_state(target)
        return self._call("run_status", {"folder": str(target)})

    def control_run(self, action: str, folder: str | None = None) -> dict:
        if folder is None:
            return self._call("control_run", {"action": action, "folder": None})
        target = self._resolve(folder)
        if self._is_detached(target):
            send_control(target, action)
            return {"folder": str(target), "requested": action}
        return self._call("control_run", {"action": action, "folder": str(target)})

    def list_runs(self, setup_path: str | None = None) -> list[dict]:
        return self._call("list_runs", {"setup_path": self._abs(setup_path)})

    def open_run(self, folder: str) -> dict:
        return self._call("open_run", {"folder": str(self._resolve(folder))})

    def delete_run(self, folder: str) -> dict:
        """Delete a run folder of the setup (the setup's file is asked of the app)."""
        setup = self.get_setup()["path"]
        if setup is None:
            raise BackendError("the setup has no file yet: its run folders are next to its file")
        target = self._resolve(folder)
        delete_run_folder(target, runs_folder(Path(setup)))
        return {"folder": str(target), "deleted": True}

    # --- the results (computed here from the run folder) -----------------------------

    def summary(self, folder: str | None, point: int | None = None) -> list[dict]:
        return results_lib.summary(self._folder(folder), point)

    def available_plots(self, folder: str | None, point: int | None = None) -> list[dict]:
        return results_lib.available_plots(self._folder(folder), point)

    def table(self, folder: str | None, point: int | None, key: str, max_rows: int = 500) -> dict:
        return results_lib.table(self._folder(folder), point, key, max_rows)

    def plot(self, folder: str | None, point: int | None, key: str) -> dict:
        """Draw the plot here, then ask the app to show it. The picture is kept either way: a
        plot the app does not show comes back with ``shown`` False and the reason."""
        target = self._folder(folder)
        path = results_lib.render_plot(target, point, key, target / results_lib.PLOTS_DIR)
        try:
            self._call("show_plot", {"folder": str(target), "point": point, "key": key})
        except BackendError as error:  # the app is gone, or it has not got the run open
            return {"path": str(path), "shown": False, "note": str(error)}
        return {"path": str(path), "shown": True}

    def export(
        self,
        folder: str | None,
        fmt: str,
        dest: str,
        include_collision: bool = False,
        point: int | None = None,
    ) -> dict:
        out = results_lib.export(
            self._folder(folder), fmt, self._resolve(dest), include_collision, point
        )
        self._written.update(os.path.realpath(str(path)) for path in out["written"])
        return out

    def export_files(
        self,
        folder: str | None,
        fmt: str,
        dest: str,
        include_collision: bool = False,
        point: int | None = None,
    ) -> list[str]:
        return results_lib.export_files(
            self._folder(folder), fmt, self._resolve(dest), include_collision, point
        )

    # --- the installer (its own process, here) ----------------------------------------

    def install_plan(self, components: list[str] | None = None) -> list[dict]:
        return self._local.install_plan(components)

    def install_step(self, component: str, expected: list[dict] | None = None) -> dict:
        return self._local.install_step(component, expected)

    # --- the agent's questions and log ------------------------------------------------

    def ask(self, text: str) -> Answer:
        """The user's answer: "allowed", "denied" or "timed out". Anything that is not a clear
        allowance is a refusal: an app that closed, an error or a malformed answer included."""
        try:
            answer = self._call("ask", {"text": text})
        except AppGone as error:
            return "timed out" if error.silent else "denied"
        except BackendError:
            return "denied"
        return answer if answer in ANSWERS else "denied"

    def log(self, entry: ActivityEntry | dict) -> None:
        """Show an entry in the app's Agent activity panel: its whole wire form, eight keys."""
        data = entry.to_dict() if isinstance(entry, ActivityEntry) else dict(entry)
        self._call("log", {"entry": data})


class AppLink:
    """The app's connection, kept between the tool calls of one server.

    :meth:`backend` is the backend for a call: the app's client while it answers, else the
    headless backend. A missing app is looked for again after ``retry_seconds`` (at once after a
    lost connection). When the app closes, the next headless call is told (see :meth:`take_notice`)
    where the setup now comes from: the setup file the app last named, reopened in headless.
    """

    def __init__(
        self,
        connect: Callable[..., AppClient | None] | None = None,
        clock: Callable[[], float] = time.monotonic,
        retry_seconds: float = RETRY_SECONDS,
        cwd: Path | None = None,
    ):
        self._connect = connect if connect is not None else connect_app
        self._clock = clock
        self._retry_seconds = retry_seconds
        self._cwd = Path(cwd) if cwd is not None else Path.cwd()
        self._app: AppClient | None = None
        self._next_try = 0.0
        #: the app's setup when it was lost: (known, path), None while no app was lost
        self._lost_setup: tuple[bool, str | None] | None = None
        self._notice: str | None = None

    def backend(self, client: str, headless: Backend) -> Backend:
        """The backend for a call: the app's client, or ``headless`` when there is no app."""
        if self._app is not None and self._app.closed:
            self._drop()
        if self._app is None and self._clock() >= self._next_try:
            app = self._connect(client, cwd=self._cwd)
            if app is not None:
                self._app = app
                self._lost_setup = None  # the app's setup is the setup now
                return app
            self._next_try = self._clock() + self._retry_seconds
        if self._app is not None:
            return self._app
        return self._headless(headless)

    def lost(self) -> None:
        """The app stopped answering: its connection is dropped, and the next call looks again."""
        if self._app is not None:
            self._drop()

    def take_notice(self) -> str | None:
        """The text for the agent about a change of backend, once (None when there is none)."""
        notice, self._notice = self._notice, None
        return notice

    def close(self) -> None:
        if self._app is not None:
            self._drop()

    def _drop(self) -> None:
        app, self._app = self._app, None
        self._next_try = 0.0
        if app is not None:
            app.close()
            self._lost_setup = (app.setup_seen, app.last_setup_path)

    def _headless(self, headless: Backend) -> Backend:
        if self._lost_setup is None:
            return headless
        known_path = self._lost_setup
        self._lost_setup = None
        self._notice = _reopen(headless, known_path)
        return headless


def _reopen(headless: Backend, lost: tuple[bool, str | None]) -> str:
    """Give the headless backend the setup the app had when it closed; the text the agent is
    told about it. A file that cannot be opened leaves the headless setup as it was."""
    known, path = lost
    if not known:
        return (
            "The pysrim2 app closed. The setup now comes from headless: it is the one the headless "
            "server had before the app (the app's setup was never read)."
        )
    if path is None:
        headless.new_setup()
        return (
            "The pysrim2 app closed. The setup now comes from headless: an empty setup, as the "
            "app's setup was never saved."
        )
    try:
        headless.open_setup(path)
    except Exception as error:  # a file that cannot be read is a note for the agent, not a failure
        return (
            f"The pysrim2 app closed. The setup {path} could not be opened ({error}), so the setup "
            "is the one the headless server had before the app."
        )
    return (
        f"The pysrim2 app closed. The setup now comes from headless: the last saved file, {path}. "
        "Changes made only in the app are not in it."
    )
