"""Notebooks in a run folder: the starter files and what the app needs to start them (Qt-free).

A notebook is ``notebook.py`` (marimo) or ``notebook.ipynb`` (Jupyter) in the run folder. The
starter is copied in only when the file is absent, so an edited notebook is never overwritten.
The app's own files for notebooks go in ``<run folder>/.pysrim2-notebook/`` (see
:mod:`srim.notebook`), where :func:`create_notebook` also creates the ``reload`` file.
"""

import http.client
import importlib.util
import os
import secrets
import shutil
import socket
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request
from collections import deque
from pathlib import Path
from urllib.parse import quote

import srim
from srim.notebook import NOTEBOOK_DIR, RELOAD_FILE

KINDS = ("marimo", "jupyter")
NOTEBOOK_NAMES = {"marimo": "notebook.py", "jupyter": "notebook.ipynb"}
STARTERS = {"marimo": "starter_marimo.py", "jupyter": "starter_jupyter.ipynb"}
#: the package that a notebook kind needs (its top-level name)
TOOLS = {"marimo": "marimo", "jupyter": "jupyterlab"}
REINSTALL_TEXT = "Reinstall the app: pysrim2 install app"

#: seconds that :meth:`NotebookServer.wait_ready` waits by default
READY_SECONDS = 60
#: seconds between two readiness checks
POLL_SECONDS = 0.25
#: the number of log lines that :meth:`NotebookServer.log_tail` returns
LOG_LINES = 20
#: the file in :data:`srim.notebook.NOTEBOOK_DIR` that holds the server's token (mode 0600)
TOKEN_FILE = "token"
#: the file in :data:`srim.notebook.NOTEBOOK_DIR` that receives the server's output
LOG_FILE = "server.log"
#: the environment variable that Jupyter reads its token from (set in the child only)
JUPYTER_TOKEN_ENV = "JUPYTER_TOKEN"
#: seconds that one HTTP readiness check may take at most
PROBE_SECONDS = 2.0
#: a log larger than this is not rewritten to hide the token (it would be read whole)
REDACT_MAX_BYTES = 50 * 1024 * 1024

# a proxy setting in the environment must not take the readiness checks away from the server
_OPENER = urllib.request.build_opener(urllib.request.ProxyHandler({}))


def _check_kind(kind: str) -> None:
    if kind not in KINDS:
        raise ValueError(f"unknown notebook kind {kind!r}; use one of: {', '.join(KINDS)}")


def notebook_path(folder, kind: str) -> Path:
    """The notebook file of ``kind`` in the run folder ``folder`` (it may not exist yet)."""
    _check_kind(kind)
    return Path(folder) / NOTEBOOK_NAMES[kind]


def starter_path(kind: str) -> Path:
    """The starter notebook of ``kind`` that ships with the library."""
    _check_kind(kind)
    return Path(srim.__file__).parent / "data" / "notebooks" / STARTERS[kind]


def create_notebook(folder, kind: str) -> Path:
    """Copy the starter of ``kind`` into the run folder and create the reload file.

    An existing notebook is left as it is. The reload file (``.pysrim2-notebook/reload``) is
    created when absent; Reload touches it. Returns the notebook's path.
    """
    folder = Path(folder)
    path = notebook_path(folder, kind)
    if not path.exists():
        shutil.copyfile(starter_path(kind), path)
    reload = folder / NOTEBOOK_DIR / RELOAD_FILE
    reload.parent.mkdir(parents=True, exist_ok=True)
    reload.touch(exist_ok=True)
    return path


def tool_problem(kind: str) -> str | None:
    """:data:`REINSTALL_TEXT` when the tool that ``kind`` needs is not installed, else None."""
    _check_kind(kind)
    if importlib.util.find_spec(TOOLS[kind]) is None:
        return REINSTALL_TEXT
    return None


class NotebookServerError(RuntimeError):
    """A notebook server that did not start, exited, or did not answer in time."""


def free_port() -> int:
    """A port on 127.0.0.1 that was free when asked (the server binds it a moment later)."""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


def server_command(kind: str, path, port: int, token_file) -> list[str]:
    """The command that serves the notebook ``path`` of ``kind`` on 127.0.0.1 at ``port``.

    The token is never on the command line: marimo reads it from ``token_file``, and Jupyter
    reads the ``JUPYTER_TOKEN`` environment variable that :meth:`NotebookServer.start` sets.
    Checked against ``marimo edit --help`` (marimo 0.25.2) and ``jupyterlab --help-all``
    (jupyterlab 4.6.6).
    """
    _check_kind(kind)
    path = Path(path)
    if kind == "marimo":
        return [
            sys.executable,
            "-m",
            "marimo",
            "edit",
            str(path),
            "--headless",
            "--host",
            "127.0.0.1",
            "--port",
            str(port),
            "--token-password-file",
            str(token_file),
            "--skip-update-check",
        ]
    return [
        sys.executable,
        "-m",
        "jupyterlab",
        "--no-browser",
        "--ip=127.0.0.1",
        f"--port={port}",
        "--ServerApp.port_retries=0",
        f"--ServerApp.root_dir={path.parent}",
    ]


def notebook_url(kind: str, path, port: int, token: str) -> str:
    """The URL that opens the notebook ``path`` with ``token`` (the token is in the query)."""
    _check_kind(kind)
    path = Path(path)
    if kind == "marimo":
        return f"http://127.0.0.1:{port}/?access_token={quote(token)}"
    return f"http://127.0.0.1:{port}/lab/tree/{quote(path.name)}?token={quote(token)}"


def _is_windows() -> bool:
    """True on Windows (a function so that the tests can take the Windows branch anywhere)."""
    return os.name == "nt"


def _open_private(path: Path, binary: bool):
    """Open a new file that only the owner can read (mode 0600 on POSIX) for writing."""
    path.unlink(missing_ok=True)
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    if binary:
        return os.fdopen(fd, "wb")
    return os.fdopen(fd, "w", encoding="utf-8")


def _remove_quietly(path: Path) -> None:
    """Remove ``path`` if it is there; a failure is not an error (best effort)."""
    try:
        path.unlink(missing_ok=True)
    except OSError:
        pass


def _redact(path: Path, secret: str) -> None:
    """Replace ``secret`` in the file at ``path`` with ``<token>``, best effort.

    The new text goes to a temporary file in the same folder, which then replaces the log, so a
    reader never sees a part-written file and the mode stays 0600. A file larger than
    :data:`REDACT_MAX_BYTES` is left as it is, and so is a file that cannot be written.
    """
    try:
        if path.stat().st_size > REDACT_MAX_BYTES:
            return
        data = path.read_bytes()
        needle = secret.encode("utf-8")
        if needle not in data:
            return
        fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=path.name + ".", suffix=".tmp")
        try:
            with os.fdopen(fd, "wb") as handle:
                handle.write(data.replace(needle, b"<token>"))
            os.replace(tmp, path)
        except BaseException:
            _remove_quietly(Path(tmp))
            raise
    except OSError:
        return


def _taskkill_tree(pid: int) -> None:
    """Kill the process ``pid`` and every process it started (Windows), output discarded."""
    try:
        subprocess.run(
            ["taskkill", "/T", "/F", "/PID", str(pid)],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            check=False,
            timeout=30,
        )
    except (OSError, subprocess.SubprocessError):
        pass


def _stop_process(process: subprocess.Popen, grace: float) -> None:
    """End the server process: on POSIX terminate, then kill after ``grace`` seconds; on Windows
    kill its whole tree with taskkill (terminate would stop only the python process), then kill
    after ``grace`` seconds when it is still there. Never raises."""
    if process.poll() is not None:
        return
    try:
        if _is_windows():
            _taskkill_tree(process.pid)
        else:
            process.terminate()
        process.wait(timeout=grace)
        return
    except (OSError, subprocess.TimeoutExpired):
        pass
    try:
        process.kill()
        process.wait(timeout=grace)
    except (OSError, subprocess.TimeoutExpired):
        pass


def _answers(url: str, timeout: float = PROBE_SECONDS) -> bool:
    """True when ``url`` gets an HTTP answer with a status below 500 (no answer is False)."""
    try:
        with _OPENER.open(url, timeout=timeout) as response:
            return response.status < 500
    except urllib.error.HTTPError as error:
        return error.code < 500
    except (OSError, ValueError, http.client.HTTPException):
        return False


class NotebookServer:
    """A notebook server process for one notebook file, started by the app.

    ``command_for(kind, path, port, token_file)`` gives the command (:func:`server_command` by
    default). :meth:`start` writes the token to ``NOTEBOOK_DIR/token`` (mode 0600), sends the
    server's output to ``NOTEBOOK_DIR/server.log`` and starts the process. ``url`` is set by
    :meth:`start` and opens the notebook with the token.
    """

    def __init__(self, kind: str, path, command_for=server_command):
        _check_kind(kind)
        self.kind = kind
        self.path = Path(path)
        self.command_for = command_for
        self.folder = self.path.parent / NOTEBOOK_DIR
        self.log_path = self.folder / LOG_FILE
        self.token_file = self.folder / TOKEN_FILE
        self.port: int | None = None
        self.token: str | None = None
        self.url: str | None = None
        self.process: subprocess.Popen | None = None

    @property
    def running(self) -> bool:
        """True while the server process is alive."""
        return self.process is not None and self.process.poll() is None

    def start(self) -> None:
        """Start the server. Raises :class:`NotebookServerError` when it is already running or
        the process cannot be started (the token file is then removed)."""
        if self.running:
            raise NotebookServerError("the notebook server is already running")
        self.folder.mkdir(parents=True, exist_ok=True)
        self.port = free_port()
        self.token = secrets.token_urlsafe(32)
        self.url = notebook_url(self.kind, self.path, self.port, self.token)
        with _open_private(self.token_file, binary=False) as handle:
            handle.write(self.token)
        try:
            command = self.command_for(self.kind, self.path, self.port, self.token_file)
            env = None
            if self.kind == "jupyter":
                env = dict(os.environ)
                env[JUPYTER_TOKEN_ENV] = self.token
            extra = {}
            if _is_windows():
                # a process group of its own, so that taskkill /T can reach its children
                extra["creationflags"] = subprocess.CREATE_NEW_PROCESS_GROUP
            # the log holds the token (marimo prints its URL), so it is private too; the child
            # gets its own copy of the file, and closing ours leaves no open handle
            with _open_private(self.log_path, binary=True) as log:
                self.process = subprocess.Popen(
                    command,
                    stdin=subprocess.DEVNULL,
                    stdout=log,
                    stderr=subprocess.STDOUT,
                    env=env,
                    **extra,
                )
        except OSError as error:
            _remove_quietly(self.token_file)
            raise NotebookServerError(f"could not start the notebook server: {error}") from error
        except BaseException:
            _remove_quietly(self.token_file)
            raise

    def wait_ready(self, timeout: float = READY_SECONDS) -> None:
        """Wait until the server answers HTTP (any status below 500) at its address.

        Raises :class:`NotebookServerError` with the end of the log when the process exits
        first, or when ``timeout`` seconds pass. A server that exited or timed out is stopped
        first, so its token file is removed and its log loses the token.
        """
        if self.process is None:
            raise NotebookServerError("the notebook server has not been started")
        base = f"http://127.0.0.1:{self.port}/"
        deadline = time.monotonic() + timeout
        while True:
            code = self.process.poll()
            if code is not None:
                tail = self.log_tail()
                self.stop()
                raise NotebookServerError(f"the notebook server exited with code {code}:\n{tail}")
            remaining = deadline - time.monotonic()
            if _answers(base, timeout=min(PROBE_SECONDS, max(remaining, 0.1))):
                return
            if time.monotonic() >= deadline:
                tail = self.log_tail()
                self.stop()
                raise NotebookServerError(
                    f"the notebook server did not answer within {timeout} s:\n{tail}"
                )
            time.sleep(POLL_SECONDS)

    def stop(self, grace: float = 5.0) -> None:
        """Stop the server (see :func:`_stop_process`), then remove its token from the folder.

        Never raises: the cleanup is best effort. Safe to call more than once, and on a server
        that never started.
        """
        process = self.process
        if process is None:
            return
        _stop_process(process, grace)
        # the server is gone, so the token is no use any more: take it out of the files
        _remove_quietly(self.token_file)
        if self.token:
            _redact(self.log_path, self.token)

    def log_tail(self, lines: int = LOG_LINES) -> str:
        """The last ``lines`` lines of the server's log ('' when there is no log)."""
        try:
            with open(self.log_path, "rb") as handle:
                tail = deque(handle, maxlen=lines)
        except OSError:
            return ""
        text = b"".join(tail).decode("utf-8", errors="replace").rstrip()
        if self.token:
            text = text.replace(self.token, "<token>")
        return text
