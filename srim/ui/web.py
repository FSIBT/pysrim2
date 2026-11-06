"""``--ui web``: the live view of a scan, served to a browser (textual-serve).

The scan runs in the CLI process, as with ``--ui plain``. :class:`WebView` writes every event
to ``<scan folder>/events.jsonl`` (flushed per event) and serves the viewer
(``python -m srim.ui.tui.view <scan folder>``) on a local port. Each browser tab runs its own
viewer, which replays that file from the start and follows it, so several tabs see the same
scan and a tab opened late shows what has happened so far. The viewer's keys write requests
(``pause``, ``resume``, ``stop``) to ``<scan folder>/control.jsonl``; :class:`WebView` polls that
file and sets the scan's ``pause_event`` and ``stop_event``. Nothing else is accepted.

textual-serve has no way to stop its server in the same process, so the server runs in a child
process (:func:`start_server`, ``python -m srim.ui.web``), which exits on SIGTERM. This module
needs only the standard library; textual-serve is imported in the child alone.
"""

from __future__ import annotations

import argparse
import os
import shlex
import signal
import socket
import subprocess
import sys
import threading
import time
from pathlib import Path

#: the events of the scan, one JSON line each (written by the CLI, read by the viewer)
EVENTS_FILE = "events.jsonl"
#: the requests of the viewer, one word per line (written by the viewer, read by the CLI)
CONTROL_FILE = "control.jsonl"
#: the requests the CLI acts on; any other line is ignored
REQUESTS = ("pause", "resume", "stop")
#: seconds between two reads of the control file
POLL_SECONDS = 0.5
#: seconds the server stays up after a finished scan, so that its final state can be seen
GRACE_SECONDS = 5.0
#: seconds the server process gets to exit after SIGTERM to its group, before SIGKILL
STOP_SECONDS = 1.0
#: seconds between two checks that the parent process is still there
PARENT_POLL_SECONDS = 0.5
IS_WINDOWS = os.name == "nt"
#: hosts that only this machine can reach
LOOPBACK_HOSTS = frozenset({"127.0.0.1", "localhost", "::1"})


def command_line(words, windows: bool | None = None) -> str:
    """``words`` as one command line for the shell that textual-serve runs it in: cmd.exe on
    Windows (quoted as :func:`subprocess.list2cmdline` does), sh elsewhere (:func:`shlex.join`)."""
    if windows is None:
        windows = IS_WINDOWS
    return subprocess.list2cmdline(words) if windows else shlex.join(words)


def viewer_command(folder, plot=None) -> str:
    """The shell command that textual-serve runs for each browser tab: the viewer of ``folder``."""
    words = [sys.executable, "-m", "srim.ui.tui.view", str(folder)]
    if plot:
        words += ["--plot", plot]
    return command_line(words)


def server_argv(host: str, port: int, command: str) -> list[str]:
    """The command line of the server process, which serves ``command`` (see :func:`main`)."""
    return [
        sys.executable,
        "-m",
        "srim.ui.web",
        "--host",
        host,
        "--port",
        str(port),
        "--command",
        command,
    ]


def port_problem(host: str, port: int) -> str | None:
    """Why ``host:port`` cannot be served (the address is taken or not valid), or None."""
    family = socket.AF_INET6 if ":" in host else socket.AF_INET
    try:
        with socket.socket(family, socket.SOCK_STREAM) as probe:
            if os.name != "nt":  # as the server does: a port in TIME_WAIT can be bound again
                probe.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            probe.bind((host, port))
    except (OSError, OverflowError) as exc:
        return f"cannot listen on {host}:{port} ({exc})"
    return None


def start_server(argv: list[str]) -> subprocess.Popen:
    """Start the server process of :func:`server_argv`, as the leader of a new process group
    (POSIX) so that :func:`stop_server` can stop it and the viewers it starts. Its output is
    discarded (textual-serve prints its logo and every request there)."""
    options = {} if IS_WINDOWS else {"start_new_session": True}
    return subprocess.Popen(
        argv,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        **options,
    )


def stop_server(server) -> None:
    """Stop the server and every process it started (the viewers of open tabs): on POSIX
    SIGTERM to its process group, a short wait for the leader, then SIGKILL to the group; on
    Windows ``taskkill /T /F`` of its tree. Never raises for a process that is gone."""
    if IS_WINDOWS:
        subprocess.run(
            tree_kill_argv(server.pid),
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            timeout=STOP_SECONDS * 10,
            check=False,
        )
        return
    _signal_group(server, signal.SIGTERM)
    _wait_quietly(server)
    _signal_group(server, signal.SIGKILL)  # the viewers too, whether or not the leader exited
    _wait_quietly(server)


def tree_kill_argv(pid: int) -> list[str]:
    """The command that ends the process tree of ``pid`` on Windows."""
    return ["taskkill", "/T", "/F", "/PID", str(pid)]


def exit_with_parent(on_orphaned=None, poll: float = PARENT_POLL_SECONDS):
    """POSIX: a daemon thread that calls ``on_orphaned()`` (default: exit the process) once the
    parent process has gone, which is when the process is re-parented. Returns the thread, or
    None on Windows, where the parent is not watched."""
    if IS_WINDOWS:
        return None
    parent = os.getppid()
    action = on_orphaned if on_orphaned is not None else (lambda: os._exit(1))

    def watch():
        while True:
            time.sleep(poll)
            if os.getppid() != parent:
                action()
                return

    thread = threading.Thread(target=watch, name="pysrim2-parent-watch", daemon=True)
    thread.start()
    return thread


def _kill_own_group() -> None:
    """The server's orphan action: end its group (the viewers) if it leads one."""
    if os.getpgrp() == os.getpid():
        os.killpg(os.getpid(), signal.SIGKILL)
    os._exit(1)


def _signal_group(server, sig) -> None:
    try:
        os.killpg(server.pid, sig)
    except (ProcessLookupError, PermissionError):  # the group is gone
        pass


def _wait_quietly(server) -> None:
    try:
        server.wait(timeout=STOP_SECONDS)
    except subprocess.TimeoutExpired:
        pass


def send_request(folder, word: str) -> None:
    """Write one request for the scan in ``folder``: a word of :data:`REQUESTS`."""
    if word not in REQUESTS:
        raise ValueError(f"unknown request {word!r} (have: {', '.join(REQUESTS)})")
    with (Path(folder) / CONTROL_FILE).open("a", encoding="utf-8", newline="\n") as file:
        file.write(word + "\n")


def read_requests(folder, offset: int) -> tuple[list[str], int]:
    """The lines written to the control file after byte ``offset``, and the offset after the
    last complete line read (a line still being written is read by the next call)."""
    try:
        with (Path(folder) / CONTROL_FILE).open("rb") as file:
            file.seek(offset)
            data = file.read()
    except FileNotFoundError:
        return [], offset
    end = data.rfind(b"\n")
    if end < 0:
        return [], offset
    complete = data[: end + 1]
    lines = [line.strip() for line in complete.decode("utf-8", errors="replace").splitlines()]
    return lines, offset + len(complete)


class WebView:
    """The browser view of the scan that runs in this process (see the module docstring).

    Use it as the consumer of the scan's events (:meth:`on_event`) and as a context manager:
    on exit the server stops, after :data:`GRACE_SECONDS` if the scan has finished. The
    server starts when the scan starts (``scan_started``), once its folder is locked and its
    files are fresh; before that nothing is written. A server that cannot start is a notice,
    and the scan goes on without the view.
    """

    def __init__(
        self,
        folder,
        *,
        host: str = "127.0.0.1",
        port: int = 8000,
        plot: str | None = None,
        stop_event,
        pause_event,
        poll: float | None = None,
        grace: float | None = None,
    ):
        self.folder = Path(folder)
        self.host = host
        self.port = port
        self.plot = plot
        self.stop_event = stop_event
        self.pause_event = pause_event
        self.poll = POLL_SECONDS if poll is None else poll
        self.grace = GRACE_SECONDS if grace is None else grace
        #: True once the scan has emitted ``scan_finished``
        self.finished = False
        self._log = None
        self._server = None
        self._disabled = False  # the server could not start: the view takes no more events
        self._offset = 0  # bytes of the control file already read
        self._closing = threading.Event()
        self._poller = None

    @property
    def url(self) -> str:
        host = f"[{self.host}]" if ":" in self.host else self.host
        return f"http://{host}:{self.port}"

    def on_event(self, event) -> None:
        """Take one event of the scan: start the view with ``scan_started``, then append
        every event to the events file."""
        if self._disabled:
            return
        if self._log is None:
            if event.kind != "scan_started":
                return
            self._start(event)
            if self._disabled:
                return
        self._log.write(event.to_json() + "\n")
        self._log.flush()
        if event.kind == "scan_finished":
            self.finished = True

    def apply_requests(self) -> list[str]:
        """Act on the requests written to the control file since the last call; returns the
        ones acted on (unknown lines are skipped)."""
        lines, self._offset = read_requests(self.folder, self._offset)
        applied = []
        for word in lines:
            if word == "pause":
                self.pause_event.set()
            elif word == "resume":
                self.pause_event.clear()
            elif word == "stop":
                self.stop_event.set()
            else:
                continue
            applied.append(word)
        return applied

    def close(self, grace: float = 0.0) -> None:
        """Stop the server (after ``grace`` seconds, so a finished scan stays visible) and the
        control poll. A second Ctrl-C ends the grace period."""
        try:
            if grace > 0:
                time.sleep(grace)
        except KeyboardInterrupt:
            pass
        finally:
            self._closing.set()
            if self._server is not None:
                stop_server(self._server)
                self._server = None
            if self._poller is not None:
                self._poller.join(timeout=STOP_SECONDS)
                self._poller = None
            if self._log is not None:
                self._log.close()
                self._log = None

    def __enter__(self) -> WebView:
        return self

    def __exit__(self, *exc_info) -> None:
        self.close(grace=self.grace if self.finished else 0.0)

    # -- the start of the view -------------------------------------------------------------

    def _start(self, event) -> None:
        try:
            self._log = (self.folder / EVENTS_FILE).open("w", encoding="utf-8", newline="\n")
            (self.folder / CONTROL_FILE).write_text("", encoding="utf-8")
            command = viewer_command(self.folder.resolve(), self.plot)
            self._server = start_server(server_argv(self.host, self.port, command))
        except OSError as exc:
            _notice(f"--ui web: the web view did not start ({exc}); the scan goes on")
            self._disabled = True
            if self._log is not None:
                self._log.close()
                self._log = None
            return
        if self.host not in LOOPBACK_HOSTS:
            _notice(
                f"warning: --host {self.host} is not a loopback address: anyone who can reach "
                f"port {self.port} can pause and stop the scan (the web view has no "
                "authentication)"
            )
        _notice(f"web view: {self.url} (the scan goes on; p pause or resume, q stop)", stdout=True)
        self._poller = threading.Thread(
            target=self._poll_until_closed, name="pysrim2-web-control", daemon=True
        )
        self._poller.start()

    def _poll_until_closed(self) -> None:
        while not self._closing.wait(self.poll):
            self.apply_requests()


def _notice(text: str, stdout: bool = False) -> None:
    print(text, file=sys.stdout if stdout else sys.stderr, flush=True)


def main(argv: list[str] | None = None) -> int:
    """``python -m srim.ui.web --host HOST --port PORT --command CMD``: serve ``CMD`` with
    textual-serve until SIGTERM or Ctrl-C. Run by :func:`start_server`, not by hand."""
    parser = argparse.ArgumentParser(prog="python -m srim.ui.web")
    parser.add_argument("--host", required=True)
    parser.add_argument("--port", type=int, required=True)
    parser.add_argument("--command", required=True)
    args = parser.parse_args(argv)
    from textual_serve.server import Server

    exit_with_parent(on_orphaned=_kill_own_group)  # the CLI is gone: so are the viewers
    Server(args.command, host=args.host, port=args.port, title="pysrim2 scan").serve()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
