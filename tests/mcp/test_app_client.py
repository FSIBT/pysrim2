"""The app client (:mod:`srim.mcp.app_client`), Qt-free: the connection to a running app, its
requests, the methods the client computes itself, the link that keeps one client between tool
calls, and the server's choice of backend and retries.

The app is a fake: a Unix socket in a short folder, served by a thread that speaks the socket
protocol (:mod:`srim.mcp.protocol`) and answers each request with what the test says. No Qt, no
SRIM, no network. Every wait is bounded.
"""

import asyncio
import json
import os
import shutil
import socket
import tempfile
import threading
import time
from pathlib import Path

import install_fakes
import pytest
from install_fakes import use as use_install_fakes
from run_folders import DONE, finished_scan
from test_headless_status import fake_checks, n15_backend
from test_results import ONE, PNG, run_folder, spec

pytest.importorskip("mcp")  # the server tests use the SDK's in-memory client

from mcp import Client  # noqa: E402

from srim.mcp import app_client, protocol  # noqa: E402
from srim.mcp.activity import ActivityEntry  # noqa: E402
from srim.mcp.app_client import (  # noqa: E402
    APP_CLOSED_TEXT,
    APP_SILENT_TEXT,
    AppGone,
    AppLink,
    connect_app,
)
from srim.mcp.backend import BackendError, NeedsApp  # noqa: E402
from srim.mcp.detached import start_runner_file  # noqa: E402
from srim.mcp.discovery import AppEndpoint, new_token, remove_endpoint, write_endpoint  # noqa: E402
from srim.mcp.headless import HeadlessBackend  # noqa: E402
from srim.mcp.protocol import Channel, Stream  # noqa: E402
from srim.scan import Scan  # noqa: E402

READY = fake_checks({"name": "trim-exe", "ok": True, "detail": "ok", "fix": ""})
#: a status of the app that says SRIM is ready
READY_STATUS = {
    "mode": "app",
    "version": "test",
    "checks": [],
    "srim_ready": True,
    "srim_problem": None,
    "setup": None,
    "running": None,
}


def default_answer(number, method, params):
    """Answers every request with what it was sent, so a test sees the size of what came."""
    return ("result", {"method": method, "size": len(json.dumps(params))})


def setup_echo(number, method, params):
    """Answers a setup request (set_values and the like) with a setup that says how big the
    request was: the app's setup answer, so it passes the client's check."""
    size = len(json.dumps(params))
    return ("result", {"path": None, "setup": {"size": size}, "problems": [], "modified": True})


def refuse_all(number, method, params):
    """A stand-in that refuses every request: a test that uses it sees a request as an error."""
    return ("error", "BackendError", f"the app was asked for {method}")


class FakeApp:
    """The app's socket for a test. ``answer(number, method, params)`` gives each request's
    outcome: ``("result", value)``, ``("error", type, text)``, ``"drop"`` (the connection closes
    with no answer) or ``"silent"`` (no answer until the test ends). ``number`` counts the
    connections from 1, and ``requests`` records ``(number, method, params)`` of each request."""

    def __init__(self, folder: Path, token: str, answer=None, reads: bool = True):
        self.token = token
        self.answer = answer or default_answer
        #: False: the app takes the hello and then never reads again (a stalled peer)
        self.reads = reads
        self.path = str(folder / "app.sock")
        self.requests: list[tuple[int, str, dict]] = []
        self.hellos: list[str] = []
        self.connections = 0
        self._stop = threading.Event()
        self._open: list[socket.socket] = []
        self._threads: list[threading.Thread] = []
        self._server = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        self._server.bind(self.path)
        self._server.listen(8)
        self._server.settimeout(0.05)
        self._acceptor = threading.Thread(target=self._accept, daemon=True)
        self._acceptor.start()

    def endpoint(self) -> AppEndpoint:
        return AppEndpoint(self.path, self.token, os.getpid(), "test")

    def publish(self) -> None:
        """Write the discovery file that points at this app (in the test's data directory)."""
        write_endpoint(self.endpoint())

    def stop(self) -> None:
        self._stop.set()
        for conn in list(self._open):
            try:
                conn.shutdown(socket.SHUT_RDWR)
            except OSError:
                pass
        self._server.close()
        self._acceptor.join(5)
        for thread in self._threads:
            thread.join(5)
        try:
            os.unlink(self.path)
        except OSError:
            pass

    def _accept(self) -> None:
        while not self._stop.is_set():
            try:
                conn, _ = self._server.accept()
            except TimeoutError:
                continue
            except OSError:
                return
            self.connections += 1
            self._open.append(conn)
            thread = threading.Thread(target=self._talk, args=(conn, self.connections), daemon=True)
            self._threads.append(thread)
            thread.start()

    def _talk(self, conn: socket.socket, number: int) -> None:
        channel = Channel(Stream(conn.makefile("rwb", buffering=0), conn), protocol.HELLO_MAX_LINE)
        try:
            token, client = protocol.check_hello(channel.receive())
            self.hellos.append(client)
            if token != self.token:
                channel.send({"hello": {"ok": False, "error": "the token is not this app's"}})
                return
            channel.send({"hello": {"ok": True, "version": "test"}})
            channel.set_max_line(protocol.MAX_LINE)
            if not self.reads:
                self._stop.wait()
                return
            while True:
                request_id, method, params = protocol.parse_request(channel.receive())
                self.requests.append((number, method, params))
                outcome = self.answer(number, method, params)
                if outcome == "drop":
                    return
                if outcome == "silent":
                    self._stop.wait()
                    return
                if outcome[0] == "result":
                    channel.send(protocol.result_reply(request_id, outcome[1]))
                else:
                    channel.send(protocol.error_reply(request_id, outcome[1], outcome[2]))
        except (OSError, protocol.ProtocolError):
            return  # the client went away, or sent what the protocol does not allow
        finally:
            conn.close()

    def calls(self, method: str) -> list[dict]:
        """The params of each request for ``method``, in order."""
        return [params for _, name, params in self.requests if name == method]


@pytest.fixture
def folder():
    """A short folder for the sockets (Unix socket paths are limited in length)."""
    path = Path(tempfile.mkdtemp(prefix="p2-"))
    yield path
    shutil.rmtree(path, ignore_errors=True)


@pytest.fixture(autouse=True)
def home(tmp_path, monkeypatch):
    """The discovery file lives in a temporary data directory, never the user's."""
    monkeypatch.setenv("PYSRIM2_HOME", str(tmp_path / "home"))
    return tmp_path


@pytest.fixture
def fake_app(folder):
    """Start fake apps; each is stopped at the end of the test."""
    apps: list[FakeApp] = []

    def make(answer=None, token=None, publish=True, reads=True):
        number = len(apps) + 1
        where = folder / f"app{number}"
        where.mkdir()
        app = FakeApp(where, token or new_token(), answer, reads=reads)
        apps.append(app)
        if publish:
            app.publish()
        return app

    yield make
    for app in apps:
        app.stop()


def setup_answer(path: Path):
    """An app that has the setup at ``path`` open (and nothing else to say)."""

    def answer(number, method, params):
        if method == "get_setup":
            return ("result", {"path": str(path), "setup": {}, "problems": [], "modified": False})
        if method == "folder_in_use":
            return ("result", False)  # no folder is open in this app
        return refuse_all(number, method, params)

    return answer


def call_tool(srv, name: str, args: dict | None = None, client_info=None):
    """One tool call through the SDK's in-memory client (its result, as the SDK gives it)."""

    async def go():
        async with Client(srv, client_info=client_info) as client:
            return await client.call_tool(name, args or {})

    return asyncio.run(go())


def payload(result) -> dict:
    """The dict a tool returned, from its JSON text."""
    assert not result.is_error, result.content
    return json.loads(result.content[0].text)


def error_of(result) -> str:
    assert result.is_error, result
    return result.content[0].text


# --- finding the app and the hello ---------------------------------------------------------


def test_no_app_means_no_client(tmp_path):
    assert connect_app("test", cwd=tmp_path) is None


def test_a_stale_endpoint_means_no_client(tmp_path, folder):
    write_endpoint(AppEndpoint(str(folder / "gone.sock"), new_token(), 999999, "0"))
    assert connect_app("test", cwd=tmp_path) is None


def test_a_wrong_token_means_no_client_and_never_logs_the_token(tmp_path, fake_app, caplog):
    app = fake_app(token=new_token(), publish=False)
    wrong = AppEndpoint(app.path, new_token(), os.getpid(), "test")
    write_endpoint(wrong)
    assert connect_app("test", cwd=tmp_path) is None
    assert wrong.token not in caplog.text
    assert app.calls("status") == []


def test_the_hello_names_the_client_and_is_clipped(tmp_path, fake_app):
    app = fake_app()
    client = connect_app("x" * 300, cwd=tmp_path)
    assert client is not None
    assert app.hellos == ["x" * 200]
    client.close()


# --- the requests -----------------------------------------------------------------------


def test_a_request_goes_to_the_app_and_its_answer_comes_back(tmp_path, fake_app):
    app = fake_app(answer=setup_echo)
    client = connect_app("test", cwd=tmp_path)
    assert client.mode == "app"
    assert client.open_run_replaces_setup is True
    answer = client.set_values(["ion.energy_keV=40"])
    assert answer["setup"]["size"] == len(json.dumps({"assignments": ["ion.energy_keV=40"]}))
    assert app.calls("set_values") == [{"assignments": ["ion.energy_keV=40"]}]
    client.close()


def test_a_large_request_goes_after_the_hello(tmp_path, fake_app):
    # the hello is small; a request after it may be as long as the protocol allows
    app = fake_app(answer=setup_echo)
    client = connect_app("test", cwd=tmp_path)
    big = "x=" + "1" * 2_000_000
    answer = client.set_values([big])
    assert answer["setup"]["size"] > 2_000_000
    assert app.calls("set_values") == [{"assignments": [big]}]
    client.close()


def test_a_stalled_app_that_stops_reading_is_a_silent_app_not_a_hang(
    tmp_path, fake_app, monkeypatch
):
    # the app takes the hello and then reads nothing: a request too big for the socket's buffers
    # must not block the client while it holds its locks
    monkeypatch.setattr(app_client, "SEND_SECONDS", 0.5)
    fake_app(answer=setup_echo, reads=False)
    client = connect_app("test", cwd=tmp_path)
    started = time.monotonic()
    with pytest.raises(AppGone) as info:
        client.set_values(["x=" + "1" * (protocol.MAX_LINE - 100)])
    assert "get_setup" in str(info.value)  # a setup edit may have been made: check it first
    assert time.monotonic() - started < 20
    assert client.closed
    assert client.ask("start a run") == "denied"  # the lock is free: the closed client answers


def test_a_request_too_large_for_the_protocol_is_refused_and_the_connection_stays(
    tmp_path, fake_app
):
    app = fake_app(answer=setup_echo)
    client = connect_app("test", cwd=tmp_path)
    with pytest.raises(BackendError, match="too long"):
        client.set_values(["x=" + "1" * (protocol.MAX_LINE + 10)])
    assert not client.closed
    assert client.get_setup()["path"] is None
    assert app.calls("set_values") == []
    client.close()


def test_an_error_from_the_app_is_raised_as_it_was_sent(tmp_path, fake_app):
    fake_app(answer=lambda n, m, p: ("error", "NeedsApp", "open the app first"))
    client = connect_app("test", cwd=tmp_path)
    with pytest.raises(NeedsApp, match="open the app first"):
        client.undo()
    assert not client.closed  # an answer, even a refusal, keeps the connection
    client.close()


def test_a_bad_error_type_from_the_app_is_a_backend_error(tmp_path, fake_app):
    fake_app(answer=lambda n, m, p: ("error", "Surprise", "bad"))
    client = connect_app("test", cwd=tmp_path)
    with pytest.raises(BackendError) as info:
        client.redo()
    assert not isinstance(info.value, (NeedsApp, AppGone))
    client.close()


def test_a_dropped_connection_is_app_gone_and_the_client_is_closed(tmp_path, fake_app):
    fake_app(answer=lambda n, m, p: "drop")
    client = connect_app("test", cwd=tmp_path)
    with pytest.raises(AppGone, match="closed") as info:
        client.get_setup()
    assert str(info.value) == APP_CLOSED_TEXT
    assert client.closed
    with pytest.raises(AppGone):
        client.get_setup()  # a closed client asks nothing more


def test_a_silent_app_on_start_run_tells_the_agent_to_check_the_runs_first(
    tmp_path, fake_app, monkeypatch
):
    # the app may have started the run while it was silent: the agent checks before it calls again
    monkeypatch.setattr(app_client, "SLOW_SECONDS", 0.3)
    fake_app(answer=lambda n, m, p: "silent")
    client = connect_app("test", cwd=tmp_path)
    with pytest.raises(AppGone) as info:
        client.start_run()
    text = str(info.value)
    assert "list_runs" in text and "run_status" in text
    assert text.endswith("then call again to continue headless")
    assert client.closed


def test_a_silent_app_on_a_setup_edit_tells_the_agent_to_check_the_setup_first(
    tmp_path, fake_app, monkeypatch
):
    monkeypatch.setattr(app_client, "QUICK_SECONDS", 0.3)
    fake_app(answer=lambda n, m, p: "silent")
    client = connect_app("test", cwd=tmp_path)
    with pytest.raises(AppGone) as info:
        client.set_values(["number_ions=5"])
    assert "get_setup" in str(info.value)
    assert str(info.value).endswith("then call again to continue headless")


def test_a_silent_app_times_out_as_app_gone(tmp_path, fake_app, monkeypatch):
    monkeypatch.setattr(app_client, "QUICK_SECONDS", 0.3)
    fake_app(answer=lambda n, m, p: "silent")
    client = connect_app("test", cwd=tmp_path)
    with pytest.raises(AppGone) as info:
        client.get_setup()
    assert str(info.value) == APP_SILENT_TEXT
    assert client.closed


def test_an_ask_the_user_allows_is_allowed(tmp_path, fake_app):
    fake_app(answer=lambda n, m, p: ("result", "allowed"))
    client = connect_app("test", cwd=tmp_path)
    assert client.ask("start a run") == "allowed"
    client.close()


def test_an_ask_that_times_out_in_the_app_is_timed_out(tmp_path, fake_app, monkeypatch):
    monkeypatch.setattr(app_client, "ASK_SECONDS", 0.3)
    fake_app(answer=lambda n, m, p: "silent")
    client = connect_app("test", cwd=tmp_path)
    assert client.ask("start a run") == "timed out"


def test_an_ask_whose_app_went_away_is_denied(tmp_path, fake_app):
    # the app closed while the question was open: no allowance was given, so it is a refusal
    fake_app(answer=lambda n, m, p: "drop")
    client = connect_app("test", cwd=tmp_path)
    assert client.ask("start a run") == "denied"
    assert client.closed


@pytest.mark.parametrize(
    "reply",
    [
        ("result", "maybe"),
        ("result", None),
        ("result", True),
        ("result", ["allowed"]),
        ("error", "BackendError", "the window is busy"),
    ],
)
def test_an_ask_answer_that_is_not_one_of_the_three_is_denied(tmp_path, fake_app, reply):
    fake_app(answer=lambda n, m, p: reply)
    client = connect_app("test", cwd=tmp_path)
    assert client.ask("start a run") == "denied"
    assert not client.closed  # an answer, even a bad one, keeps the connection
    client.close()


def test_only_exactly_false_says_a_file_is_no_replacement(tmp_path, fake_app):
    # anything else the app says is a replacement: a malformed answer must not allow an overwrite
    answers = iter([("result", 0), ("result", None), ("result", "no"), ("result", False)])
    fake_app(answer=lambda n, m, p: next(answers))
    client = connect_app("test", cwd=tmp_path)
    other = tmp_path / "other.yaml"
    other.write_text("x", encoding="utf-8")
    assert client.would_replace(str(other)) is True
    assert client.would_replace(str(other)) is True
    assert client.would_replace(str(other)) is True
    assert client.would_replace(str(other)) is False
    client.close()


def test_only_exactly_false_says_nothing_is_unsaved(tmp_path, fake_app):
    answers = iter([("result", 0), ("result", None), ("result", False)])
    fake_app(answer=lambda n, m, p: next(answers))
    client = connect_app("test", cwd=tmp_path)
    assert client.has_unsaved_changes() is True
    assert client.has_unsaved_changes() is True
    assert client.has_unsaved_changes() is False
    client.close()


@pytest.mark.parametrize(
    "reply",
    [
        ("result", "not a setup"),
        ("result", None),
        ("result", {"path": None, "setup": {}, "problems": []}),  # no "modified"
        ("result", {"path": 3, "setup": {}, "problems": [], "modified": False}),
        ("result", {"path": None, "setup": [], "problems": [], "modified": False}),
        ("result", {"path": None, "setup": {}, "problems": {}, "modified": False}),
        ("result", {"path": None, "setup": {}, "problems": [], "modified": "no"}),
    ],
)
def test_a_setup_answer_of_the_wrong_shape_is_a_backend_error(tmp_path, fake_app, reply):
    fake_app(answer=lambda n, m, p: reply)
    client = connect_app("test", cwd=tmp_path)
    with pytest.raises(BackendError, match="setup"):
        client.get_setup()
    assert client.setup_seen is False  # nothing is remembered from a bad answer
    client.close()


def test_the_log_sends_the_whole_entry(tmp_path, fake_app):
    app = fake_app()
    client = connect_app("test", cwd=tmp_path)
    entry = ActivityEntry(
        time=1700000000.0, mode="app", client="codex", tool="set_values", summary="set"
    )
    client.log(entry)
    (sent,) = app.calls("log")
    assert sorted(sent["entry"]) == sorted(
        ["time", "mode", "client", "tool", "summary", "ask", "answer", "error"]
    )
    assert sent["entry"]["mode"] == "app"
    client.close()


# --- what the client computes itself ------------------------------------------------------


def test_summary_and_tables_are_read_from_the_run_folder_not_the_app(tmp_path, fake_app):
    app = fake_app(answer=refuse_all)
    client = connect_app("test", cwd=tmp_path)
    folder = run_folder(tmp_path / "work")
    assert client.summary(str(folder))
    assert client.available_plots(str(folder))
    table = client.table(str(folder), None, "range_profile", max_rows=5)
    assert table["columns"][0] == "depth_nm" and table["rows"]
    assert app.requests == []  # nothing was asked of the app
    client.close()


def test_a_plot_is_drawn_here_and_the_app_is_asked_to_show_it(tmp_path, fake_app):
    app = fake_app(answer=lambda n, m, p: ("result", {"shown": True}))
    client = connect_app("test", cwd=tmp_path)
    folder = run_folder(tmp_path / "work")
    details = client.plot(str(folder), None, "range_profile")
    assert details["shown"] is True
    path = Path(details["path"])
    assert path.parent == folder / "plots" and path.read_bytes().startswith(PNG)
    assert app.calls("show_plot") == [
        {"folder": str(folder), "point": None, "key": "range_profile"}
    ]
    client.close()


def test_a_plot_the_app_does_not_show_is_saved_with_a_note(tmp_path, fake_app):
    fake_app(
        answer=lambda n, m, p: (
            ("error", "BackendError", "the run is not open in the app: open it with open_run first")
            if m == "show_plot"
            else default_answer(n, m, p)
        )
    )
    client = connect_app("test", cwd=tmp_path)
    folder = run_folder(tmp_path / "work")
    details = client.plot(str(folder), None, "range_profile")
    assert details["shown"] is False
    assert "not open" in details["note"]
    assert Path(details["path"]).read_bytes().startswith(PNG)
    client.close()


def test_an_export_is_written_here_and_the_files_it_wrote_are_no_replacement(tmp_path, fake_app):
    app = fake_app(answer=lambda n, m, p: ("result", True))
    client = connect_app("test", cwd=tmp_path)
    folder = run_folder(tmp_path / "work")
    out = client.export(str(folder), "csv", str(tmp_path / "tables"))
    written = out["written"]
    assert written
    assert client.would_replace(written[0]) is False  # this client wrote it
    assert app.calls("would_replace") == []
    other = tmp_path / "other.csv"
    other.write_text("not ours\n", encoding="utf-8")
    assert client.would_replace(str(other)) is True  # the app's answer for a file it did not write
    assert app.calls("would_replace") == [{"path": str(other)}]
    client.close()


def test_delete_run_is_done_here_for_a_run_folder_of_the_setup(tmp_path, fake_app):
    from srim.app.runs import RUNS_SUFFIX

    setup = tmp_path / "n15.yaml"
    n15_backend(tmp_path).save_setup(str(setup))
    runs = tmp_path / f"n15{RUNS_SUFFIX}"
    scan = Scan(spec(), seed=7)
    finished_scan(runs / "20260101-000000Z", scan, [DONE], source=ONE)
    fake_app(answer=setup_answer(setup))
    client = connect_app("test", cwd=tmp_path)
    folder = runs / "20260101-000000Z"
    assert client.delete_run(str(folder)) == {"folder": str(folder), "deleted": True}
    assert not folder.exists()
    client.close()


def test_delete_run_refuses_a_folder_that_is_not_a_run_of_the_setup(tmp_path, fake_app):
    setup = tmp_path / "n15.yaml"
    n15_backend(tmp_path).save_setup(str(setup))
    elsewhere = run_folder(tmp_path / "scratch")
    fake_app(answer=setup_answer(setup))
    client = connect_app("test", cwd=tmp_path)
    with pytest.raises(BackendError, match="not a run folder"):
        client.delete_run(str(elsewhere))
    assert elsewhere.exists()
    client.close()


def test_a_run_of_a_detached_runner_is_read_and_stopped_here(tmp_path, fake_app):
    app = fake_app(answer=refuse_all)
    client = connect_app("test", cwd=tmp_path)
    folder = run_folder(tmp_path / "work")
    start_runner_file(folder, workers=1, backend="local")  # this process: alive, so running
    assert client.run_status(str(folder))["status"] == "running"
    assert client.control_run("pause", str(folder)) == {"folder": str(folder), "requested": "pause"}
    assert "pause" in (folder / "control.jsonl").read_text(encoding="utf-8")
    assert app.requests == []  # nothing was asked of the app
    client.close()


def test_a_run_the_app_started_is_read_and_controlled_in_the_app(tmp_path, fake_app):
    folder = run_folder(tmp_path / "work")
    start_runner_file(folder, workers=1, backend="app")
    app = fake_app(answer=lambda n, m, p: ("result", {"folder": p["folder"], "status": "running"}))
    client = connect_app("test", cwd=tmp_path)
    assert client.run_status(str(folder))["status"] == "running"
    assert client.control_run("stop", str(folder))["folder"] == str(folder)
    assert [m for _, m, _ in app.requests if m != "hello"] == ["run_status", "control_run"]
    client.close()


def test_the_install_plan_is_read_from_the_installer_here(tmp_path, fake_app, monkeypatch):
    fake_app(answer=refuse_all)
    use_install_fakes(monkeypatch, tmp_path, {"srim": [{"name": "fetch", "commands": ["get it"]}]})
    local = HeadlessBackend(tmp_path, plan_steps=install_fakes.plan)
    client = connect_app("test", cwd=tmp_path, local=local)
    rows = client.install_plan(["srim"])
    assert [row["step"] for row in rows] == ["fetch"]
    client.close()


# --- the link: one client kept between calls ---------------------------------------------


def test_a_missing_app_is_looked_for_again_only_after_the_retry_interval(tmp_path):
    attempts = []
    now = [0.0]

    def connect(client, cwd=None):
        attempts.append(client)
        return None

    link = AppLink(connect=connect, clock=lambda: now[0], retry_seconds=3.0, cwd=tmp_path)
    headless = n15_backend(tmp_path)
    assert link.backend("codex", headless) is headless
    assert link.backend("codex", headless) is headless
    assert len(attempts) == 1
    now[0] = 3.5
    assert link.backend("codex", headless) is headless
    assert len(attempts) == 2


def test_the_setup_comes_from_the_saved_file_when_the_app_closes(tmp_path, fake_app):
    headless = n15_backend(tmp_path)
    saved = tmp_path / "n15.yaml"
    headless.save_setup(str(saved))
    saved_ions = headless.get_setup()["setup"]["number_ions"]
    headless.set_values(["number_ions=99"])  # the headless setup differs from the saved file
    app = fake_app(
        answer=lambda n, m, p: (
            "result",
            {"path": str(saved), "setup": {}, "problems": [], "modified": True},
        )
    )
    link = AppLink(cwd=tmp_path, retry_seconds=0.0)
    client = link.backend("codex", headless)
    assert client.mode == "app"
    client.get_setup()
    app.stop()
    remove_endpoint(app.token)
    with pytest.raises(AppGone):
        client.get_setup()
    assert link.backend("codex", headless) is headless
    notice = link.take_notice()
    assert notice is not None and str(saved) in notice and "not in it" in notice
    assert headless.get_setup()["path"] == str(saved)
    assert headless.get_setup()["setup"]["number_ions"] == saved_ions
    assert link.take_notice() is None


def test_a_new_setup_in_the_app_is_an_empty_setup_after_it_closes(tmp_path, fake_app):
    headless = n15_backend(tmp_path)
    app = fake_app(
        answer=lambda n, m, p: (
            "result",
            {"path": None, "setup": {}, "problems": [], "modified": True},
        )
    )
    link = AppLink(cwd=tmp_path, retry_seconds=0.0)
    client = link.backend("codex", headless)
    client.get_setup()
    app.stop()
    remove_endpoint(app.token)
    with pytest.raises(AppGone):
        client.get_setup()
    link.backend("codex", headless)
    assert "empty setup" in link.take_notice()
    assert headless.get_setup()["path"] is None


def test_a_setup_the_app_never_showed_leaves_headless_as_it_was(tmp_path, fake_app):
    headless = n15_backend(tmp_path)
    app = fake_app()
    link = AppLink(cwd=tmp_path, retry_seconds=0.0)
    link.backend("codex", headless)
    app.stop()
    remove_endpoint(app.token)
    with pytest.raises(AppGone):
        link.backend("codex", headless).get_setup()
    link.backend("codex", headless)
    assert "before the app" in link.take_notice()
    assert headless.get_setup()["setup"]["ion"]["symbol"] == "N"


# --- the server: backend choice, retries, reporting, ask-first and the log ----------------


def server_for(tmp_path, headless, retry_seconds=0.0):
    from srim.mcp import server

    link = AppLink(cwd=tmp_path, retry_seconds=retry_seconds)
    return server.build_server(tmp_path, headless=headless, link=link)


def test_a_read_the_app_drops_is_answered_headless_with_a_note(tmp_path, fake_app):
    token = new_token()

    def answer(number, method, params):
        if method == "get_setup" and number == 1:
            remove_endpoint(token)  # the app is gone: no second connection to it
            return "drop"
        return default_answer(number, method, params)

    fake_app(answer=answer, token=token)
    headless = n15_backend(tmp_path)
    srv = server_for(tmp_path, headless)
    result = payload(call_tool(srv, "get_setup"))
    assert result["mode"] == "headless"
    assert "closed" in result["note"]


def test_a_read_the_app_answers_slowly_is_answered_headless(tmp_path, fake_app, monkeypatch):
    monkeypatch.setattr(app_client, "QUICK_SECONDS", 0.3)
    token = new_token()

    def answer(number, method, params):
        if method == "get_setup":
            remove_endpoint(token)
            return "silent"
        return default_answer(number, method, params)

    fake_app(answer=answer, token=token)
    headless = n15_backend(tmp_path)
    srv = server_for(tmp_path, headless)
    result = payload(call_tool(srv, "get_setup"))
    assert result["mode"] == "headless"


def test_an_edit_the_app_drops_is_reported_and_not_retried(tmp_path, fake_app):
    token = new_token()

    def answer(number, method, params):
        if method == "set_values":
            remove_endpoint(token)
            return "drop"
        return default_answer(number, method, params)

    app = fake_app(answer=answer, token=token)
    headless = n15_backend(tmp_path)
    before = headless.get_setup()["setup"]
    srv = server_for(tmp_path, headless)
    result = call_tool(srv, "set_values", {"assignments": ["number_ions=77"]})
    assert APP_CLOSED_TEXT in error_of(result)  # the SDK adds "Error executing tool" in front
    assert headless.get_setup()["setup"] == before  # nothing was changed in the headless setup
    assert len(app.calls("set_values")) == 1  # the edit was not sent a second time


def test_the_app_answers_while_it_is_open(tmp_path, fake_app):
    fake_app(answer=setup_echo)
    headless = n15_backend(tmp_path)
    srv = server_for(tmp_path, headless)
    result = payload(call_tool(srv, "get_setup"))
    assert result["mode"] == "app"  # the app's answer, with the mode added
    assert result["setup"] == {"size": 2}  # the echo of an empty request: {} is 2 characters


def test_each_call_is_logged_to_the_app_with_its_whole_entry(tmp_path, fake_app):
    app = fake_app(
        answer=lambda n, m, p: (
            ("result", {"path": None, "setup": {}, "problems": [], "modified": False})
            if m == "get_setup"
            else default_answer(n, m, p)
        )
    )
    srv = server_for(tmp_path, n15_backend(tmp_path))
    payload(call_tool(srv, "get_setup"))
    (sent,) = app.calls("log")
    assert sent["entry"]["tool"] == "get_setup"
    assert sent["entry"]["mode"] == "app"
    assert len(sent["entry"]) == 8


def test_an_ask_about_a_start_goes_to_the_app_dialog(tmp_path, fake_app):
    setup = tmp_path / "n15.yaml"
    n15_backend(tmp_path).save_setup(str(setup))

    def answer(number, method, params):
        if method == "status":
            return ("result", READY_STATUS)
        if method == "get_setup":
            return ("result", {"path": str(setup), "setup": {}, "problems": [], "modified": False})
        if method == "has_unsaved_changes":
            return ("result", False)
        if method == "estimate":
            return ("result", {"seconds": 7200.0, "workers": 1, "cores": 4, "units": 1, "ions": 10})
        if method == "ask":
            return ("result", "denied")
        return refuse_all(number, method, params)

    app = fake_app(answer=answer)
    srv = server_for(tmp_path, n15_backend(tmp_path))
    result = call_tool(srv, "start_run", {})
    assert "did not allow" in error_of(result)
    (asked,) = app.calls("ask")
    assert "start the run" in asked["text"]
    assert app.calls("start_run") == []


def test_the_hello_names_the_mcp_client(tmp_path, fake_app):
    from mcp.types import Implementation

    app = fake_app()
    srv = server_for(tmp_path, n15_backend(tmp_path))
    call_tool(srv, "get_setup", client_info=Implementation(name="codex", version="1.0"))
    assert app.hellos == ["codex"]


def test_a_failed_app_connection_is_not_repeated_at_every_call(tmp_path, monkeypatch):
    from srim.mcp import server

    attempts = []

    def connect(client, cwd=None):
        attempts.append(client)
        return None

    link = AppLink(connect=connect, cwd=tmp_path, retry_seconds=60.0)
    srv = server.build_server(tmp_path, headless=n15_backend(tmp_path), link=link)
    for _ in range(3):
        assert payload(call_tool(srv, "get_setup"))["mode"] == "headless"
    assert len(attempts) == 1


def test_a_cancelled_app_ask_starts_nothing_and_later_calls_go_on_when_the_user_answers(
    tmp_path, fake_app
):
    # the call is cancelled while the question is open: its late "allowed" starts nothing, and
    # later calls are answered once the app has its answer (the app answers one request at a time)
    setup = tmp_path / "n15.yaml"
    n15_backend(tmp_path).save_setup(str(setup))
    gate = threading.Event()
    asked = threading.Event()

    def answer(number, method, params):
        if method == "status":
            return ("result", READY_STATUS)
        if method == "get_setup":
            return ("result", {"path": str(setup), "setup": {}, "problems": [], "modified": False})
        if method == "has_unsaved_changes":
            return ("result", False)
        if method == "estimate":
            return ("result", {"seconds": 7200.0, "workers": 1, "cores": 4, "units": 1, "ions": 10})
        if method == "ask":
            asked.set()
            gate.wait(20)  # the user answers when the test says so
            return ("result", "allowed")
        if method == "start_run":
            return ("error", "BackendError", "nothing should start")
        return default_answer(number, method, params)

    app = fake_app(answer=answer)
    srv = server_for(tmp_path, n15_backend(tmp_path))

    async def scenario():
        async with Client(srv) as client:
            pending = asyncio.create_task(client.call_tool("start_run", {}))
            while not asked.is_set():
                await asyncio.sleep(0.01)
            pending.cancel()  # the agent gives up on the start while the question is open
            try:
                await pending
            except asyncio.CancelledError:
                pass
            gate.set()  # the user answers "allowed", too late for the cancelled call
            return await asyncio.wait_for(client.call_tool("get_setup", {}), 20)

    later = asyncio.run(scenario())
    assert payload(later)["path"] == str(setup)
    assert app.calls("start_run") == []  # the cancelled start was not sent


def test_the_server_lock_is_free_while_the_app_is_asked(tmp_path):
    # the MCP server's lock is released around the question: other calls of the server are not
    # held up by the user's answer
    from srim.mcp import server

    lock = threading.Lock()
    seen = {}

    class Backend:
        mode = "app"

        def ask(self, text):
            seen["locked"] = lock.locked()
            return "allowed"

    lock.acquire()  # the worker holds it, as _State.run does
    try:
        ask = server._app_ask(Backend(), threading.Event(), lock)
        assert ask("start a run") == "allowed"
        assert lock.locked()  # taken back once the answer is in
    finally:
        lock.release()
    assert seen["locked"] is False


def test_the_link_serves_one_caller_at_a_time_so_the_app_is_connected_once(tmp_path):
    # two calls at once (the server's lock is released while the user is asked): the second waits
    # for the first one's connection, instead of connecting a second client
    entered, release = threading.Event(), threading.Event()
    connects = []

    class Connected:
        mode = "app"
        closed = False
        setup_seen = False
        last_setup_path = None

        def close(self):
            self.closed = True

    fake = Connected()

    def connect(client, cwd=None):
        connects.append(client)
        entered.set()
        release.wait(10)
        return fake

    link = AppLink(connect=connect, cwd=tmp_path)
    headless = n15_backend(tmp_path)
    results = []
    first = threading.Thread(target=lambda: results.append(link.backend("a", headless)))
    first.start()
    assert entered.wait(5)
    second = threading.Thread(target=lambda: results.append(link.backend("b", headless)))
    second.start()
    second.join(0.3)
    assert second.is_alive()  # it waits; it does not connect again
    release.set()
    first.join(5)
    second.join(5)
    assert results == [fake, fake]
    assert len(connects) == 1


def test_the_app_coming_back_after_a_headless_fall_back_is_said_in_the_next_result(
    tmp_path, fake_app
):
    headless = n15_backend(tmp_path)
    saved = tmp_path / "n15.yaml"
    headless.save_setup(str(saved))
    first = fake_app(
        answer=lambda n, m, p: (
            "result",
            {"path": str(saved), "setup": {}, "problems": [], "modified": False},
        )
    )
    link = AppLink(cwd=tmp_path, retry_seconds=0.0)
    client = link.backend("codex", headless)
    first.stop()
    remove_endpoint(first.token)
    with pytest.raises(AppGone):
        client.get_setup()  # the call finds the app gone
    link.backend("codex", headless)  # headless now, and the closing is said
    assert "closed" in link.take_notice()
    assert link.backend("codex", headless) is headless  # still no app
    assert link.take_notice() is None  # nothing new while it stays away

    fake_app()  # the app is open again
    client = link.backend("codex", headless)
    assert client.mode == "app"
    notice = link.take_notice()
    assert notice is not None and "open again" in notice
    assert "not from the headless edits" in notice
    assert link.take_notice() is None  # once
    client.close()


def test_the_app_coming_back_without_a_headless_call_in_between_is_not_said(tmp_path, fake_app):
    headless = n15_backend(tmp_path)
    first = fake_app()
    link = AppLink(cwd=tmp_path, retry_seconds=0.0)
    link.backend("codex", headless)
    first.stop()
    remove_endpoint(first.token)
    with pytest.raises(AppGone):
        link.backend(
            "codex", headless
        ).get_setup()  # the client noticed the loss: no headless call yet
    fake_app()
    assert link.backend("codex", headless).mode == "app"
    assert link.take_notice() is None


def test_delete_run_is_refused_for_a_run_folder_the_app_has_open_or_runs(tmp_path, fake_app):
    from srim.app.runs import RUNS_SUFFIX

    setup = tmp_path / "n15.yaml"
    n15_backend(tmp_path).save_setup(str(setup))
    runs = tmp_path / f"n15{RUNS_SUFFIX}"
    folder = runs / "20260101-000000Z"
    finished_scan(folder, Scan(spec(), seed=7), [DONE], source=ONE)
    in_use = {"answer": True}

    def answer(number, method, params):
        if method == "folder_in_use":
            return ("result", in_use["answer"])
        return setup_answer(setup)(number, method, params)

    fake_app(answer=answer)
    client = connect_app("test", cwd=tmp_path)
    with pytest.raises(BackendError, match="open or running in the pysrim2 app"):
        client.delete_run(str(folder))
    assert folder.exists()
    in_use["answer"] = False
    assert client.delete_run(str(folder))["deleted"] is True
    assert not folder.exists()
    client.close()


def test_a_code_issued_headless_is_not_redeemed_for_another_folder_once_the_app_is_open(
    tmp_path, fake_app
):
    # the code is bound to its action (the tool, its arguments, the setup): after the app opens, a
    # code for one folder does not allow the delete of another, which the app asks about
    import re

    headless = n15_backend(tmp_path)
    srv = server_for(tmp_path, headless)
    first = call_tool(srv, "delete_run", {"folder": str(tmp_path / "a-run")})
    text = error_of(first)
    code = re.search(r'confirm_code="([2346789ABCDEFGHJKMNPQRTUVWXYZ]{6})"', text).group(1)

    app = fake_app(
        answer=lambda n, m, p: (
            ("result", "denied") if m == "ask" else setup_answer(tmp_path / "n15.yaml")(n, m, p)
        )
    )
    result = call_tool(srv, "delete_run", {"folder": str(tmp_path / "b-run"), "confirm_code": code})
    assert "did not allow" in error_of(result)
    (asked,) = app.calls("ask")
    assert "b-run" in asked["text"]
    assert app.calls("delete_run") == []
