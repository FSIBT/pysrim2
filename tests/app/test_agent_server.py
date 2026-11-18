"""The app's agent socket: hello, tokens, limits, ordering, stale sockets and the window's hooks.

The server runs on the GUI thread, so the tests wait with ``qtbot`` (which runs the event loop).
The clients run in threads and use the Task 1 client (``socket_connect`` and ``Channel``).
"""

import json
import os
import socket
import threading

import pytest

pytest.importorskip("PySide6")
pytest.importorskip("pytestqt")

from PySide6.QtCore import QDir  # noqa: E402

import srim  # noqa: E402
from srim.app import agent_server  # noqa: E402
from srim.app.agent_server import (  # noqa: E402
    AgentServer,
    AgentSocketError,
    Deferred,
)
from srim.app.settings import AppSettings  # noqa: E402
from srim.app.window import MainWindow  # noqa: E402
from srim.mcp import protocol  # noqa: E402
from srim.mcp.backend import BackendError, NeedsApp  # noqa: E402
from srim.mcp.discovery import AppEndpoint, read_endpoint, write_endpoint  # noqa: E402
from srim.mcp.protocol import (  # noqa: E402
    HELLO_MAX_LINE,
    Channel,
    encode,
    hello_request,
    socket_connect,
)

POSIX = os.name == "posix"
posix_only = pytest.mark.skipif(not POSIX, reason="a Unix socket file is POSIX only")


class StandIn:
    """A handler for the tests. Some method names do fixed things (see :meth:`handle`)."""

    def __init__(self):
        self.calls: list[tuple[str, dict]] = []
        #: the Deferreds answered later by the test (method "later")
        self.pending: list[Deferred] = []
        #: how many of those were cancelled because their peer went away
        self.cancels = 0

    def _cancelled(self) -> None:
        self.cancels += 1

    def handle(self, method: str, params: dict):
        self.calls.append((method, params))
        if method == "echo":
            return params
        if method == "later":
            deferred = Deferred()
            deferred.on_cancel(self._cancelled)
            self.pending.append(deferred)
            return deferred
        if method == "fail":
            raise RuntimeError("the handler broke")
        if method == "need":
            raise NeedsApp("open the app first")
        if method == "big":
            return "x" * params["size"]
        raise BackendError(f"unknown method {method}")


class Outcome:
    """What a call made in a thread gave: ``value`` or ``error``, and ``done`` when it ended."""

    def __init__(self):
        self.done = False
        self.value = None
        self.error: BaseException | None = None


def in_thread(fn) -> Outcome:
    outcome = Outcome()

    def run():
        try:
            outcome.value = fn()
        except BaseException as error:  # reported to the test, not lost in the thread
            outcome.error = error
        finally:
            outcome.done = True

    threading.Thread(target=run, daemon=True).start()
    return outcome


def finish(qtbot, outcome: Outcome, timeout: int = 5000):
    """Wait (running the event loop) for a thread's call, and return its value."""
    qtbot.waitUntil(lambda: outcome.done, timeout=timeout)
    if outcome.error is not None:
        raise outcome.error
    return outcome.value


def send_raw(stream, data: bytes) -> None:
    while data:
        sent = stream.write(data)
        if not sent:
            raise ConnectionError("the connection closed while sending")
        data = data[sent:]


def connect(endpoint: AppEndpoint, token: str | None = None, client: str = "test"):
    """Connect and send the hello; return ``(channel, hello_reply)``. Runs in a thread."""
    channel = Channel(socket_connect(endpoint.socket, timeout=3.0), timeout=3.0)
    channel.send(hello_request(endpoint.token if token is None else token, client))
    return channel, channel.receive()


def ends(channel: Channel) -> bool:
    """True when the server closes the connection (a receive gets the end of the stream)."""
    try:
        channel.receive()
    except ConnectionError:
        return True
    return False


@pytest.fixture
def make_server():
    """Start servers for a test; each is stopped at the end."""
    servers = []

    def make(handler=None, **options):
        server = AgentServer(handler if handler is not None else StandIn(), **options)
        servers.append(server)
        server.start()
        return server

    yield make
    for server in servers:
        server.stop()


def test_a_client_with_the_token_gets_its_answers(qtbot, make_server):
    server = make_server()
    names = []
    server.client_connected.connect(names.append)

    def talk():
        channel, hello = connect(read_endpoint())
        channel.send(protocol.request(1, "echo", {"a": 1}))
        reply = channel.receive()
        channel.close()
        return hello, reply

    hello, reply = finish(qtbot, in_thread(talk))
    assert hello == {"hello": {"ok": True, "version": srim.__version__}}
    assert reply == {"id": 1, "result": {"a": 1}}
    assert names == ["test"]


def test_a_wrong_token_is_refused_and_never_echoed(qtbot, make_server, caplog):
    handler = StandIn()
    make_server(handler)
    endpoint = read_endpoint()

    def talk():
        channel = Channel(socket_connect(endpoint.socket, timeout=3.0), timeout=3.0)
        channel.send(hello_request("f" * 64, "test"))
        reply = channel.receive()
        return reply, ends(channel)

    reply, closed = finish(qtbot, in_thread(talk))
    assert reply == {"hello": {"ok": False, "error": agent_server.TOKEN_REFUSED_TEXT}}
    assert closed
    assert handler.calls == []
    assert endpoint.token not in json.dumps(reply)
    assert endpoint.token not in caplog.text


def test_a_hello_with_another_protocol_version_is_refused(qtbot, make_server):
    make_server()
    endpoint = read_endpoint()

    def talk():
        channel = Channel(socket_connect(endpoint.socket, timeout=3.0), timeout=3.0)
        message = hello_request(endpoint.token, "test")
        message["hello"]["protocol"] = 2
        channel.send(message)
        return channel.receive()

    reply = finish(qtbot, in_thread(talk))
    assert reply["hello"]["ok"] is False


def test_an_oversized_line_before_the_hello_is_refused(qtbot, make_server):
    make_server()
    endpoint = read_endpoint()

    def talk():
        stream = socket_connect(endpoint.socket, timeout=3.0)
        channel = Channel(stream, timeout=3.0)
        send_raw(stream, b"x" * (HELLO_MAX_LINE + 1) + b"\n")
        return channel.receive(), ends(channel)

    reply, closed = finish(qtbot, in_thread(talk))
    assert reply["hello"]["ok"] is False
    assert "longer" in reply["hello"]["error"]
    assert closed


def test_a_request_before_the_hello_is_refused(qtbot, make_server):
    make_server()
    endpoint = read_endpoint()

    def talk():
        channel = Channel(socket_connect(endpoint.socket, timeout=3.0), timeout=3.0)
        channel.send(protocol.request(1, "echo", {}))
        return channel.receive(), ends(channel)

    reply, closed = finish(qtbot, in_thread(talk))
    assert reply["hello"]["ok"] is False
    assert closed


def test_an_idle_connection_without_a_hello_is_closed(qtbot, make_server):
    make_server(hello_timeout_ms=200)
    endpoint = read_endpoint()

    def talk():
        channel = Channel(socket_connect(endpoint.socket, timeout=3.0), timeout=3.0)
        return ends(channel)

    assert finish(qtbot, in_thread(talk))



def test_pipelined_requests_are_answered_in_order_around_a_later_answer(qtbot, make_server):
    handler = StandIn()
    make_server(handler)
    endpoint = read_endpoint()

    def talk():
        channel, _hello = connect(endpoint)
        first = protocol.request(1, "later", {})
        rest = encode(protocol.request(2, "echo", {"n": 2})) + encode(
            protocol.request(3, "echo", {"n": 3})
        )
        send_raw(channel.stream, encode(first) + rest)
        replies = [channel.receive(), channel.receive(), channel.receive()]
        channel.close()
        return replies

    outcome = in_thread(talk)
    qtbot.waitUntil(lambda: len(handler.pending) > 0, timeout=5000)
    handler.pending[0].resolve({"late": True})
    replies = finish(qtbot, outcome)
    assert [reply["id"] for reply in replies] == [1, 2, 3]
    assert replies[0] == {"id": 1, "result": {"late": True}}
    assert replies[2] == {"id": 3, "result": {"n": 3}}


def test_a_deferred_can_be_rejected(qtbot, make_server):
    handler = StandIn()
    make_server(handler)
    endpoint = read_endpoint()

    def talk():
        channel, _hello = connect(endpoint)
        channel.send(protocol.request(7, "later", {}))
        reply = channel.receive()
        channel.close()
        return reply

    outcome = in_thread(talk)
    qtbot.waitUntil(lambda: len(handler.pending) > 0, timeout=5000)
    handler.pending[0].reject(BackendError("no such sheet"))
    reply = finish(qtbot, outcome)
    assert reply == {"id": 7, "error": {"type": "BackendError", "message": "no such sheet"}}


def test_a_needs_app_answer_keeps_its_type(qtbot, make_server):
    make_server()
    endpoint = read_endpoint()

    def talk():
        channel, _hello = connect(endpoint)
        channel.send(protocol.request(2, "need", {}))
        reply = channel.receive()
        channel.close()
        return reply

    reply = finish(qtbot, in_thread(talk))
    assert reply == {"id": 2, "error": {"type": "NeedsApp", "message": "open the app first"}}


def test_a_failing_handler_answers_an_error_and_the_server_keeps_running(qtbot, make_server):
    server = make_server()
    endpoint = read_endpoint()

    def talk():
        channel, _hello = connect(endpoint)
        channel.send(protocol.request(1, "fail", {}))
        failed = channel.receive()
        channel.send(protocol.request(2, "echo", {"x": 1}))
        answered = channel.receive()
        channel.close()
        return failed, answered

    failed, answered = finish(qtbot, in_thread(talk))
    assert failed == {"id": 1, "error": {"type": "BackendError", "message": "the handler broke"}}
    assert answered == {"id": 2, "result": {"x": 1}}
    assert server.listening


def test_a_bad_line_after_good_requests_is_answered_after_them_then_closed(qtbot, make_server):
    make_server()
    endpoint = read_endpoint()

    def talk():
        channel, _hello = connect(endpoint)
        send_raw(
            channel.stream, encode(protocol.request(1, "echo", {"k": 1})) + b"this is not json\n"
        )
        first = channel.receive()
        second = channel.receive()
        closed = ends(channel)
        return first, second, closed

    first, second, closed = finish(qtbot, in_thread(talk))
    assert first == {"id": 1, "result": {"k": 1}}
    assert second["id"] is None and second["error"]["type"] == "BackendError"
    assert closed


def test_requests_from_two_connections_are_handled_one_at_a_time(qtbot, make_server):
    handler = StandIn()
    make_server(handler)
    endpoint = read_endpoint()

    def first_client():
        channel, _hello = connect(endpoint)
        channel.send(protocol.request(1, "later", {}))
        reply = channel.receive()
        channel.close()
        return reply

    def second_client():
        channel, _hello = connect(endpoint)
        channel.send(protocol.request(1, "echo", {"from": "second"}))
        reply = channel.receive()
        channel.close()
        return reply

    first = in_thread(first_client)
    qtbot.waitUntil(lambda: len(handler.pending) > 0, timeout=5000)
    second = in_thread(second_client)
    qtbot.wait(300)
    assert not second.done  # the second connection waits for the first answer
    handler.pending[0].resolve("first")
    assert finish(qtbot, first) == {"id": 1, "result": "first"}
    assert finish(qtbot, second) == {"id": 1, "result": {"from": "second"}}


def test_a_peer_that_stops_reading_is_dropped_past_the_byte_limit(qtbot, make_server):
    server = make_server(max_pending=64 * 1024)
    endpoint = read_endpoint()
    held = []  # the client's channel stays open, so only the server can end the connection

    def talk():
        channel, _hello = connect(endpoint)
        held.append(channel)
        try:
            for number in range(20):
                channel.send(protocol.request(number, "big", {"size": 200_000}))
        except OSError:
            pass  # the server dropped the connection while the requests went out
        return None

    finish(qtbot, in_thread(talk))
    qtbot.waitUntil(lambda: server.connection_count == 0, timeout=10000)
    assert server.listening  # only the peer is gone


def test_a_peer_that_takes_no_answer_for_too_long_is_dropped(qtbot, make_server):
    server = make_server(stall_ms=200)
    endpoint = read_endpoint()
    held = []

    def talk():
        channel, _hello = connect(endpoint)
        held.append(channel)
        channel.send(protocol.request(1, "big", {"size": 4_000_000}))
        return None

    finish(qtbot, in_thread(talk))
    qtbot.waitUntil(lambda: server.connection_count == 0, timeout=10000)
    assert server.listening


def test_the_discovery_file_is_written_on_listen_and_removed_on_stop(qtbot, make_server):
    server = AgentServer(StandIn())
    assert read_endpoint() is None
    server.start()
    endpoint = read_endpoint()
    assert endpoint is not None
    assert endpoint.pid == os.getpid()
    assert endpoint.version == srim.__version__
    assert server.listening
    server.stop()
    assert not server.listening
    assert read_endpoint() is None
    if POSIX:
        assert not os.path.exists(endpoint.socket)


def test_stop_twice_is_harmless(make_server):
    server = make_server()
    server.stop()
    server.stop()
    assert read_endpoint() is None


def test_the_discovery_file_is_not_written_when_listening_fails(monkeypatch):
    server = AgentServer(StandIn())

    def refuse(_server):
        raise AgentSocketError("in use")

    monkeypatch.setattr(agent_server, "_listen", refuse)
    with pytest.raises(AgentSocketError):
        server.start()
    assert read_endpoint() is None
    assert not server.listening


def test_a_stale_discovery_file_is_replaced_on_start(make_server):
    write_endpoint(AppEndpoint("gone", "ab" * 32, 1, "0"))
    make_server()
    endpoint = read_endpoint()
    assert endpoint is not None
    assert endpoint.token != "ab" * 32


@posix_only
def test_a_stale_socket_file_nobody_answers_is_replaced(qtbot, make_server, monkeypatch):
    name = "pysrim2-test-stale1"
    monkeypatch.setattr(agent_server, "new_socket_name", lambda: name)
    path = os.path.join(QDir.tempPath(), name)
    leftover = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    leftover.bind(path)
    leftover.close()  # the file stays, and nothing listens on it
    assert os.path.exists(path)

    make_server()
    endpoint = read_endpoint()
    assert endpoint.socket == path

    def talk():
        channel, hello = connect(endpoint)
        channel.close()
        return hello

    assert finish(qtbot, in_thread(talk))["hello"]["ok"] is True


@posix_only
def test_a_socket_another_program_answers_is_not_replaced(make_server, monkeypatch):
    name = "pysrim2-test-live1"
    monkeypatch.setattr(agent_server, "new_socket_name", lambda: name)
    first = make_server()
    first_endpoint = read_endpoint()

    second = AgentServer(StandIn())
    with pytest.raises(AgentSocketError, match="in use"):
        second.start()
    assert not second.listening
    # the first app's discovery file is still its own
    assert read_endpoint().token == first_endpoint.token
    assert first.listening


def test_a_failing_discovery_write_closes_the_socket(monkeypatch):
    server = AgentServer(StandIn())

    def fail(endpoint):
        raise OSError("disk full")

    monkeypatch.setattr(agent_server, "write_endpoint", fail)
    with pytest.raises(AgentSocketError, match="disk full"):
        server.start()
    assert not server.listening


def test_the_window_starts_and_stops_the_socket(qtbot):
    window = MainWindow(AppSettings(route="wine"), checks_fn=lambda route: [])
    qtbot.addWidget(window)
    window.start_agent_socket()
    assert window.agent.listening
    assert read_endpoint() is not None
    window.stop_background()
    assert not window.agent.listening
    assert read_endpoint() is None


def test_a_socket_that_cannot_start_is_shown_in_the_status_bar(qtbot, monkeypatch):
    window = MainWindow(AppSettings(route="wine"), checks_fn=lambda route: [])
    qtbot.addWidget(window)

    def refuse(self):
        raise AgentSocketError("the agent socket name is in use by another program")

    monkeypatch.setattr(AgentServer, "start", refuse)
    window.start_agent_socket()
    assert not window.agent.listening
    assert "Agents cannot connect" in window.status.shown_text()


def test_a_window_that_is_closing_does_not_start_the_socket(qtbot):
    window = MainWindow(AppSettings(route="wine"), checks_fn=lambda route: [])
    qtbot.addWidget(window)
    window._closing = True
    window.start_agent_socket()
    assert not window.agent.listening




def test_a_write_or_read_error_on_a_live_socket_does_not_stop_the_server(qtbot, make_server):
    server = make_server()
    endpoint = read_endpoint()

    def talk():
        channel, _hello = connect(endpoint)
        channel.stream.close()  # the client goes away without a word
        return None

    finish(qtbot, in_thread(talk))
    qtbot.waitUntil(lambda: server.connection_count == 0, timeout=5000)
    assert server.listening
    assert read_endpoint() is not None


def test_a_full_server_closes_a_further_connection(qtbot, make_server, monkeypatch):
    monkeypatch.setattr(agent_server, "MAX_CONNECTIONS", 2)
    server = make_server()
    endpoint = read_endpoint()
    held = []  # the two accepted clients stay open

    def talk_held():
        channel, hello = connect(endpoint)
        held.append(channel)
        return hello

    def talk_extra():
        try:
            connect(endpoint)
        except OSError:
            return "refused"
        return "answered"

    assert finish(qtbot, in_thread(talk_held))["hello"]["ok"] is True
    assert finish(qtbot, in_thread(talk_held))["hello"]["ok"] is True
    assert finish(qtbot, in_thread(talk_extra)) == "refused"
    assert server.connection_count == 2


def test_requests_beyond_the_waiting_limit_are_refused_after_the_earlier_ones(
    qtbot, make_server, monkeypatch
):
    monkeypatch.setattr(agent_server, "MAX_WAITING", 2)
    handler = StandIn()
    make_server(handler)
    endpoint = read_endpoint()

    def talk():
        channel, _hello = connect(endpoint)
        send_raw(
            channel.stream,
            encode(protocol.request(1, "later", {}))
            + encode(protocol.request(2, "echo", {}))
            + encode(protocol.request(3, "echo", {})),
        )
        replies = [channel.receive(), channel.receive(), channel.receive()]
        closed = ends(channel)
        return replies, closed

    outcome = in_thread(talk)
    qtbot.waitUntil(lambda: len(handler.pending) > 0, timeout=5000)
    handler.pending[0].resolve("late")
    replies, closed = finish(qtbot, outcome)
    assert [reply.get("id") for reply in replies[:2]] == [1, 2]
    assert replies[2]["id"] is None and "waiting" in replies[2]["error"]["message"]
    assert closed
    assert [method for method, _ in handler.calls] == ["later", "echo"]


def test_a_deferred_settles_once():
    deferred = Deferred()
    seen = []
    deferred.done.connect(lambda ok, payload: seen.append((ok, payload)))
    deferred.resolve("first")
    deferred.resolve("second")
    deferred.reject(BackendError("third"))
    assert seen == [(True, "first")]
    assert deferred.outcome == (True, "first")


def test_a_deferred_settled_after_the_server_stopped_is_ignored(qtbot, make_server):
    handler = StandIn()
    server = make_server(handler)
    endpoint = read_endpoint()

    def talk():
        channel, _hello = connect(endpoint)
        channel.send(protocol.request(1, "later", {}))
        return channel

    held = finish(qtbot, in_thread(talk))  # the client stays open
    qtbot.waitUntil(lambda: len(handler.pending) > 0, timeout=5000)
    server.stop()
    handler.pending[0].resolve("late")  # nothing is left to answer, and nothing raises
    assert not server.listening
    assert [method for method, _ in handler.calls] == ["later"]
    assert held is not None


@posix_only
def test_a_symlink_at_the_socket_name_is_refused_and_kept(tmp_path, monkeypatch):
    name = "pysrim2-test-link1"
    monkeypatch.setattr(agent_server, "new_socket_name", lambda: name)
    path = os.path.join(QDir.tempPath(), name)
    target = tmp_path / "elsewhere.sock"
    target.write_text("not ours to touch")
    os.symlink(target, path)
    try:
        with pytest.raises(AgentSocketError, match="in use"):
            AgentServer(StandIn()).start()
        assert os.path.islink(path)
        assert target.read_text() == "not ours to touch"
    finally:
        os.unlink(path)


@posix_only
def test_a_regular_file_at_the_socket_name_is_refused_and_kept(monkeypatch):
    name = "pysrim2-test-file2"
    monkeypatch.setattr(agent_server, "new_socket_name", lambda: name)
    path = os.path.join(QDir.tempPath(), name)
    with open(path, "w", encoding="utf-8") as handle:
        handle.write("not a socket")
    try:
        with pytest.raises(AgentSocketError, match="in use"):
            AgentServer(StandIn()).start()
        with open(path, encoding="utf-8") as handle:
            assert handle.read() == "not a socket"
    finally:
        os.unlink(path)


def test_a_closed_peers_queued_requests_never_reach_the_handler(qtbot, make_server):
    handler = StandIn()
    server = make_server(handler)
    endpoint = read_endpoint()

    def talk():
        channel, _hello = connect(endpoint)
        send_raw(
            channel.stream,
            encode(protocol.request(1, "later", {})) + encode(protocol.request(2, "echo", {})),
        )
        return channel

    held = finish(qtbot, in_thread(talk))
    qtbot.waitUntil(lambda: len(handler.pending) > 0, timeout=5000)
    held.close()  # the peer goes away while its first answer is still pending
    qtbot.waitUntil(lambda: server.connection_count == 0, timeout=5000)
    handler.pending[0].resolve("late")
    assert [method for method, _ in handler.calls] == ["later"]


def test_a_dropped_peers_queued_requests_never_reach_the_handler(qtbot, make_server):
    # the byte-limit drop: "big" overflows the pending limit, so the peer is dropped while "echo"
    # is still queued behind it
    handler = StandIn()
    server = make_server(handler, max_pending=64 * 1024)
    endpoint = read_endpoint()
    held = []

    def talk():
        channel, _hello = connect(endpoint)
        held.append(channel)
        send_raw(
            channel.stream,
            encode(protocol.request(1, "big", {"size": 200_000}))
            + encode(protocol.request(2, "echo", {})),
        )
        return None

    finish(qtbot, in_thread(talk))
    qtbot.waitUntil(lambda: server.connection_count == 0, timeout=5000)
    assert [method for method, _ in handler.calls] == ["big"]


def test_the_server_works_after_a_stop_inside_a_handler(qtbot):
    class StopInside:
        def __init__(self):
            self.server = None

        def handle(self, method, params):
            if method == "stop_then_wait":
                self.server.stop()
                return Deferred()  # never settled: the server is gone with its peer
            return params

    handler = StopInside()
    server = AgentServer(handler)
    handler.server = server
    server.start()
    try:
        endpoint = read_endpoint()

        def first():
            channel, _hello = connect(endpoint)
            channel.send(protocol.request(1, "stop_then_wait", {}))
            channel.close()
            return None

        finish(qtbot, in_thread(first))
        qtbot.waitUntil(lambda: not server.listening, timeout=5000)

        server.start()
        endpoint = read_endpoint()

        def second():
            channel, _hello = connect(endpoint)
            channel.send(protocol.request(2, "echo", {"again": True}))
            reply = channel.receive()
            channel.close()
            return reply

        assert finish(qtbot, in_thread(second)) == {"id": 2, "result": {"again": True}}
    finally:
        server.stop()


def test_a_deferred_cancel_callback_runs_once_and_a_late_callback_runs_at_once():
    deferred = Deferred()
    calls = []
    deferred.on_cancel(lambda: calls.append("first"))
    deferred._cancel()
    deferred._cancel()
    deferred.on_cancel(lambda: calls.append("late"))
    assert calls == ["first", "late"]
    assert deferred.cancelled


def test_a_cancelled_deferred_ignores_a_later_settling():
    deferred = Deferred()
    seen = []
    deferred.done.connect(lambda ok, payload: seen.append(ok))
    deferred._cancel()
    deferred.resolve("too late")
    assert seen == [] and deferred.outcome is None


def test_a_pending_answer_is_cancelled_when_its_peer_goes(qtbot, make_server):
    handler = StandIn()
    make_server(handler)
    endpoint = read_endpoint()

    def talk_first():
        channel, _hello = connect(endpoint)
        channel.send(protocol.request(1, "later", {}))
        return channel

    first = finish(qtbot, in_thread(talk_first))
    qtbot.waitUntil(lambda: len(handler.pending) > 0, timeout=5000)

    def talk_second():
        channel, _hello = connect(endpoint)
        channel.send(protocol.request(1, "echo", {"from": "second"}))
        reply = channel.receive()
        channel.close()
        return reply

    second = in_thread(talk_second)
    first.close()  # the owner of the pending answer is gone; nobody settles it
    assert finish(qtbot, second) == {"id": 1, "result": {"from": "second"}}
    assert handler.cancels == 1
    assert handler.pending[0].cancelled
    handler.pending[0].resolve("late")
    assert handler.pending[0].outcome is None


def test_queued_request_bytes_are_capped_per_connection(qtbot, make_server):
    handler = StandIn()
    make_server(handler, max_queued_bytes=10_000)
    endpoint = read_endpoint()
    blob = "x" * 6000

    def talk():
        channel, _hello = connect(endpoint)
        send_raw(
            channel.stream,
            encode(protocol.request(1, "later", {}))
            + encode(protocol.request(2, "echo", {"blob": blob}))
            + encode(protocol.request(3, "echo", {"blob": blob})),
        )
        replies = [channel.receive(), channel.receive(), channel.receive()]
        closed = ends(channel)
        return replies, closed

    outcome = in_thread(talk)
    qtbot.waitUntil(lambda: len(handler.pending) > 0, timeout=5000)
    handler.pending[0].resolve("late")
    replies, closed = finish(qtbot, outcome)
    assert [reply.get("id") for reply in replies[:2]] == [1, 2]
    assert replies[2]["id"] is None and "too large" in replies[2]["error"]["message"]
    assert closed
    assert [method for method, _ in handler.calls] == ["later", "echo"]
