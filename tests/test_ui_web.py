"""The web view's CLI side (``srim.ui.web``): the events file, the control requests, the server
command line and the notices. The server is never started: ``start_server`` is replaced by a
fake process. Every wait has a timeout."""

import json
import os
import shlex
import signal
import socket
import subprocess
import sys
import threading
import time
from types import SimpleNamespace

import pytest

from srim.scan.events import PointQueued, ScanFinished, ScanStarted
from srim.ui import web

TIMEOUT = 5.0

requires_posix = pytest.mark.skipif(
    os.name == "nt", reason="SIGKILL and process groups are POSIX-only"
)


def split_command(command: str) -> list[str]:
    """The words of a command line as the shell of this platform reads them: sh rules on POSIX,
    cmd.exe-quoted words (as :func:`subprocess.list2cmdline` writes them) on Windows."""
    if os.name != "nt":
        return shlex.split(command)
    return [word.strip('"') for word in shlex.split(command, posix=False)]


class FakeServer:
    """A stand-in for the server process: the signals sent to its process group are recorded
    (``signals``); it exits on SIGTERM unless ``exits_on_term`` is False, and on SIGKILL."""

    def __init__(self, pid=4242, exits_on_term=True):
        self.pid = pid
        self.exits_on_term = exits_on_term
        self.signals = []
        self.exited = False

    @property
    def terminated(self) -> bool:
        return signal.SIGTERM in self.signals

    def send_group_signal(self, pid, sig):
        assert pid == self.pid  # the group is led by the server
        self.signals.append(sig)
        if sig == web.SIGKILL or (sig == signal.SIGTERM and self.exits_on_term):
            self.exited = True

    def poll(self):
        return 0 if self.exited else None

    def wait(self, timeout=None):
        if self.exited:
            return 0
        raise subprocess.TimeoutExpired("server", timeout)


@pytest.fixture
def started(monkeypatch):
    """The argv of each server start, and the fake process it got (``started.server``); the
    group signals of the fake go to it, never to a real process group."""
    calls = []
    fake = FakeServer()

    def fake_start(argv):
        calls.append(argv)
        return fake

    monkeypatch.setattr(web, "start_server", fake_start)
    monkeypatch.setattr(
        web, "_signal_group", lambda server, sig: fake.send_group_signal(server.pid, sig)
    )
    monkeypatch.setattr(web, "STOP_SECONDS", 0.01)
    monkeypatch.setattr(web, "STARTUP_SECONDS", 0.0)
    # the group signals above are the POSIX stop: pinned, so the fake is used on every platform
    monkeypatch.setattr(web, "IS_WINDOWS", False)
    return SimpleNamespace(argv=calls, server=fake)


def make_view(folder, **options):
    stop, pause = threading.Event(), threading.Event()
    options.setdefault("poll", 0.01)
    view = web.WebView(folder, stop_event=stop, pause_event=pause, **options)
    return view, stop, pause


def scan_started(folder):
    return ScanStarted(n_points=2, workers=1, base_seed=7, scan_dir=str(folder))


def test_the_viewer_command_runs_the_viewer_of_the_folder_shell_quoted(tmp_path):
    folder = tmp_path / "my scans"
    folder.mkdir()
    expected = [sys.executable, "-m", "srim.ui.tui.view", str(folder)]
    assert web.viewer_command(folder) == web.command_line(expected)
    assert split_command(web.viewer_command(folder)) == expected
    words = split_command(web.viewer_command(folder, plot="ion.energy_keV,mean"))
    assert words[-2:] == ["--plot", "ion.energy_keV,mean"]


def test_the_server_command_line_runs_this_module_with_host_port_and_command(tmp_path):
    argv = web.server_argv("127.0.0.1", 8123, "viewer --x")
    assert argv == [
        sys.executable,
        "-m",
        "srim.ui.web",
        "--host",
        "127.0.0.1",
        "--port",
        "8123",
        "--command",
        "viewer --x",
    ]


def test_the_server_process_serves_the_command_with_textual_serve(monkeypatch):
    pytest.importorskip("textual_serve")
    served = {}

    class RecordingServer:
        def __init__(self, command, host, port, title):
            served.update(command=command, host=host, port=port, title=title)

        def serve(self):
            served["served"] = True

    import textual_serve.server

    monkeypatch.setattr(textual_serve.server, "Server", RecordingServer)
    assert web.main(["--host", "127.0.0.1", "--port", "8123", "--command", "x y"]) == 0
    assert served["command"] == "x y" and served["port"] == 8123
    assert served["host"] == "127.0.0.1" and served["served"]


def test_a_busy_port_is_reported_and_a_free_one_is_not():
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as busy:
        busy.bind(("127.0.0.1", 0))
        busy.listen(1)
        port = busy.getsockname()[1]
        problem = web.port_problem("127.0.0.1", port)
    assert problem is not None and f"127.0.0.1:{port}" in problem
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as free:
        free.bind(("127.0.0.1", 0))
        port = free.getsockname()[1]
    assert web.port_problem("127.0.0.1", port) is None


def test_events_before_the_scan_starts_are_not_written(tmp_path, started):
    view, _, _ = make_view(tmp_path)
    view.on_event(PointQueued(index=0, id="ab"))
    assert started.argv == [] and not (tmp_path / web.EVENTS_FILE).exists()
    view.close()


def test_the_scan_folder_gets_every_event_as_a_line_and_the_server_starts_once(
    tmp_path, started, capsys
):
    view, _, _ = make_view(tmp_path, port=8123)
    view.on_event(scan_started(tmp_path))
    view.on_event(PointQueued(index=0, id="ab"))
    # flushed per event: a reader sees both lines before the view is closed
    lines = (tmp_path / web.EVENTS_FILE).read_text(encoding="utf-8").splitlines()
    assert [json.loads(line)["kind"] for line in lines] == [
        "run",
        "scan_started",
        "point_queued",
    ]
    assert len(started.argv) == 1
    argv = started.argv[0]
    assert argv[argv.index("--port") + 1] == "8123"
    assert split_command(argv[-1])[1:3] == ["-m", "srim.ui.tui.view"]
    assert "web view: http://127.0.0.1:8123" in capsys.readouterr().out
    view.close()
    assert started.server.terminated


def test_the_viewer_gets_the_plot_option_it_was_given(tmp_path, started):
    view, _, _ = make_view(tmp_path, plot="ion.energy_keV,mean")
    view.on_event(scan_started(tmp_path))
    assert split_command(started.argv[0][-1])[-2:] == ["--plot", "ion.energy_keV,mean"]
    view.close()


def test_requests_from_the_control_file_set_the_scan_flags(tmp_path, started):
    view, stop, pause = make_view(tmp_path)
    view.on_event(scan_started(tmp_path))
    with (tmp_path / web.CONTROL_FILE).open("a", encoding="utf-8") as file:
        file.write("pause\nbogus\n")
    assert view.apply_requests() == ["pause"]
    assert pause.is_set() and not stop.is_set()
    with (tmp_path / web.CONTROL_FILE).open("a", encoding="utf-8") as file:
        file.write("resume\nstop")  # the last line is still being written
    assert view.apply_requests() == ["resume"]
    assert not pause.is_set() and not stop.is_set()
    with (tmp_path / web.CONTROL_FILE).open("a", encoding="utf-8") as file:
        file.write("\n")
    assert view.apply_requests() == ["stop"]
    assert stop.is_set()
    view.close()


def test_the_poll_thread_applies_requests_as_they_are_written(tmp_path, started):
    view, stop, pause = make_view(tmp_path)
    view.on_event(scan_started(tmp_path))
    with (tmp_path / web.CONTROL_FILE).open("a", encoding="utf-8") as file:
        file.write("pause\n")
    assert pause.wait(TIMEOUT)
    view.close()


def test_send_request_writes_a_known_word_and_refuses_any_other(tmp_path):
    web.send_request(tmp_path, "pause")
    web.send_request(tmp_path, "stop")
    assert (tmp_path / web.CONTROL_FILE).read_text(encoding="utf-8") == "pause\nstop\n"
    with pytest.raises(ValueError, match="unknown request 'rm'"):
        web.send_request(tmp_path, "rm")


def test_a_host_other_than_loopback_gets_a_warning(tmp_path, started, capsys):
    view, _, _ = make_view(tmp_path, host="0.0.0.0", port=8000)
    view.on_event(scan_started(tmp_path))
    err = capsys.readouterr().err
    assert "anyone who can reach port 8000 can pause and stop the scan" in err
    view.close()


def test_loopback_hosts_get_no_warning(tmp_path, started, capsys):
    view, _, _ = make_view(tmp_path, host="localhost")
    view.on_event(scan_started(tmp_path))
    assert "warning" not in capsys.readouterr().err
    view.close()


def test_a_server_that_cannot_start_is_a_notice_and_the_scan_goes_on(tmp_path, monkeypatch, capsys):
    def refuse(argv):
        raise OSError("no such executable")

    monkeypatch.setattr(web, "start_server", refuse)
    view, _, _ = make_view(tmp_path)
    view.on_event(scan_started(tmp_path))
    view.on_event(PointQueued(index=0, id="ab"))  # no error: the view is off
    assert "--ui web: the web view did not start (no such executable)" in (capsys.readouterr().err)
    view.close()


def test_the_view_stays_up_for_the_grace_only_after_a_finished_scan(tmp_path, started):
    view, _, _ = make_view(tmp_path, grace=0.01)
    view.on_event(scan_started(tmp_path))
    view.on_event(ScanFinished(n_done=2, n_failed=0, seconds=1.0, stopped=False))
    graces = []
    original = view.close
    view.close = lambda grace=0.0: (graces.append(grace), original(grace))
    with view:
        pass
    assert graces == [0.01]
    assert started.server.terminated


def test_leaving_before_the_scan_finished_stops_the_server_at_once(tmp_path, started):
    view, _, _ = make_view(tmp_path, grace=60.0)
    view.on_event(scan_started(tmp_path))
    with view:
        pass  # no scan_finished: no grace period
    assert started.server.terminated


def test_the_server_leads_its_own_process_group_on_posix(monkeypatch):
    monkeypatch.setattr(web, "IS_WINDOWS", False)
    seen = {}

    def record(argv, **options):
        seen.update(options)
        return FakeServer()

    monkeypatch.setattr(web.subprocess, "Popen", record)
    web.start_server(["x"])
    assert seen["start_new_session"] is True


@requires_posix
def test_stopping_the_server_signals_its_group_then_kills_it(monkeypatch):
    stuck = FakeServer(exits_on_term=False)
    monkeypatch.setattr(
        web, "_signal_group", lambda server, sig: stuck.send_group_signal(server.pid, sig)
    )
    monkeypatch.setattr(web, "STOP_SECONDS", 0.01)
    web.stop_server(stuck)
    assert stuck.signals == [signal.SIGTERM, web.SIGKILL]  # the viewers die with it


@requires_posix
def test_a_server_that_exits_on_term_is_still_killed_with_its_group(started):
    web.stop_server(started.server)
    assert started.server.signals == [signal.SIGTERM, web.SIGKILL]


def test_stopping_on_windows_ends_the_process_tree_with_taskkill(monkeypatch):
    monkeypatch.setattr(web, "IS_WINDOWS", True)
    ran = []
    monkeypatch.setattr(web.subprocess, "run", lambda argv, **options: ran.append(argv))
    web.stop_server(SimpleNamespace(pid=4242))
    assert ran == [["taskkill", "/T", "/F", "/PID", "4242"]]


def test_stopping_on_windows_never_raises_when_taskkill_fails_or_hangs(monkeypatch):
    monkeypatch.setattr(web, "IS_WINDOWS", True)

    def hangs(argv, **options):
        raise subprocess.TimeoutExpired(argv, options["timeout"])

    def missing(argv, **options):
        raise FileNotFoundError("taskkill")

    for fake in (hangs, missing):
        monkeypatch.setattr(web.subprocess, "run", fake)
        web.stop_server(SimpleNamespace(pid=4242))  # no exception


def test_the_parent_watch_calls_its_action_once_the_parent_changes(monkeypatch):
    parents = iter([7, 7, 7])
    current = {"pid": 7}

    def getppid():
        current["pid"] = next(parents, 1)  # the parent is gone from the third check on
        return current["pid"]

    monkeypatch.setattr(web, "IS_WINDOWS", False)
    monkeypatch.setattr(web.os, "getppid", getppid)
    gone = threading.Event()
    thread = web.exit_with_parent(on_orphaned=gone.set, poll=0.001)
    assert gone.wait(TIMEOUT)
    thread.join(TIMEOUT)
    assert not thread.is_alive()


def test_the_parent_watch_is_not_started_on_windows(monkeypatch):
    monkeypatch.setattr(web, "IS_WINDOWS", True)
    assert web.exit_with_parent(on_orphaned=lambda: None) is None


def test_the_served_command_is_quoted_for_the_shell_that_runs_it():
    words = ["C:\\Program Files\\Python\\python.exe", "-m", "srim.ui.tui.view", "my scans"]
    assert web.command_line(words, windows=True) == subprocess.list2cmdline(words)
    assert web.command_line(words, windows=False) == shlex.join(words)
    posix = ["/opt/py thon/bin/python", "-m", "srim.ui.tui.view", "a b"]
    assert shlex.split(web.command_line(posix, windows=False)) == posix


def test_the_viewer_command_uses_the_quoting_of_the_platform(tmp_path, monkeypatch):
    monkeypatch.setattr(web, "IS_WINDOWS", True)
    folder = tmp_path / "my scans"
    assert web.viewer_command(folder) == subprocess.list2cmdline(
        [sys.executable, "-m", "srim.ui.tui.view", str(folder)]
    )


def test_each_run_starts_its_events_file_with_a_run_header(tmp_path, started):
    view, _, _ = make_view(tmp_path)
    view.on_event(scan_started(tmp_path))
    view.close()
    lines = (tmp_path / web.EVENTS_FILE).read_text(encoding="utf-8").splitlines()
    header = json.loads(lines[0])
    assert header == {"kind": "run", "run_id": view.run_id}
    assert json.loads(lines[1])["kind"] == "scan_started"
    assert len(view.run_id) == 32


def test_a_control_file_that_cannot_be_read_is_warned_once_and_polling_goes_on(
    tmp_path, started, monkeypatch, capsys
):
    view, _, _ = make_view(tmp_path)
    view.on_event(scan_started(tmp_path))
    calls = []

    def flaky():
        calls.append(1)
        if len(calls) <= 3:
            raise OSError("disk trouble")
        return []

    monkeypatch.setattr(view, "apply_requests", flaky)
    deadline = time.monotonic() + TIMEOUT
    while len(calls) < 5 and time.monotonic() < deadline:
        time.sleep(0.01)
    view.close()
    err = capsys.readouterr().err
    assert len(calls) >= 5  # still polling after the failures
    assert err.count("the control file cannot be read (disk trouble)") == 1


def test_an_events_file_that_cannot_be_written_turns_the_view_off_with_one_notice(
    tmp_path, started, capsys
):
    class Broken:
        def write(self, text):
            raise OSError("disk full")

        def flush(self):
            pass

        def close(self):
            pass

    view, _, _ = make_view(tmp_path)
    view.on_event(scan_started(tmp_path))
    view._log = Broken()
    view.on_event(PointQueued(index=0, id="ab"))  # does not raise into the scan
    view.on_event(PointQueued(index=1, id="cd"))
    err = capsys.readouterr().err
    assert err.count("the events file cannot be written (disk full)") == 1
    assert started.server.terminated  # the server goes with the view
    view.close()


def test_a_server_that_exits_at_once_is_a_notice_and_the_scan_goes_on(
    tmp_path, monkeypatch, capsys
):
    dead = FakeServer()
    dead.exited = True
    monkeypatch.setattr(web, "start_server", lambda argv: dead)
    monkeypatch.setattr(web, "STARTUP_SECONDS", 0.0)
    view, _, _ = make_view(tmp_path, port=8123)
    view.on_event(scan_started(tmp_path))
    view.on_event(PointQueued(index=0, id="ab"))  # no error
    err = capsys.readouterr().err
    assert "the web server exited at once (code 0; is port 8123 in use?)" in err
    assert "the scan goes on without the web view" in err
    view.close()
