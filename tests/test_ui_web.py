"""The web view's CLI side (``srim.ui.web``): the events file, the control requests, the server
command line and the notices. The server is never started: ``start_server`` is replaced by a
fake process. Every wait has a timeout."""

import json
import shlex
import socket
import sys
import threading
from types import SimpleNamespace

import pytest

from srim.scan.events import PointQueued, ScanFinished, ScanStarted
from srim.ui import web

TIMEOUT = 5.0


class FakeServer:
    """A stand-in for the server process: records what was done to it."""

    def __init__(self):
        self.terminated = False
        self.killed = False

    def poll(self):
        return 0 if self.terminated or self.killed else None

    def terminate(self):
        self.terminated = True

    def kill(self):
        self.killed = True

    def wait(self, timeout=None):
        return 0


@pytest.fixture
def started(monkeypatch):
    """The argv of each server start, and the fake process it got (``started.server``)."""
    calls = []
    fake = FakeServer()

    def fake_start(argv):
        calls.append(argv)
        return fake

    monkeypatch.setattr(web, "start_server", fake_start)
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
    words = shlex.split(web.viewer_command(folder))
    assert words == [sys.executable, "-m", "srim.ui.tui.view", str(folder)]
    words = shlex.split(web.viewer_command(folder, plot="ion.energy_keV,mean"))
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
    assert [json.loads(line)["kind"] for line in lines] == ["scan_started", "point_queued"]
    assert len(started.argv) == 1
    argv = started.argv[0]
    assert argv[argv.index("--port") + 1] == "8123"
    assert shlex.split(argv[-1])[1:3] == ["-m", "srim.ui.tui.view"]
    assert "web view: http://127.0.0.1:8123" in capsys.readouterr().out
    view.close()
    assert started.server.terminated


def test_the_viewer_gets_the_plot_option_it_was_given(tmp_path, started):
    view, _, _ = make_view(tmp_path, plot="ion.energy_keV,mean")
    view.on_event(scan_started(tmp_path))
    assert shlex.split(started.argv[0][-1])[-2:] == ["--plot", "ion.energy_keV,mean"]
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
