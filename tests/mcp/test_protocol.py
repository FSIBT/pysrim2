"""The app socket's protocol: JSON lines, limits, requests and replies (Qt-free)."""

import os
import shutil
import socket
import tempfile
import threading

import pytest

from srim.mcp import protocol
from srim.mcp.backend import BackendError, NeedsApp
from srim.mcp.protocol import (
    HELLO_MAX_LINE,
    MAX_LINE,
    PROTOCOL_VERSION,
    Channel,
    LineReader,
    ProtocolError,
    check_hello,
    encode,
    error_reply,
    exception_for,
    hello_request,
    parse_request,
    result_reply,
    socket_connect,
    socket_is_ours,
)

TOKEN = "e" * 64


def decode_all(data: bytes) -> list[dict]:
    return LineReader().feed(data)


def test_a_message_is_one_json_line_and_reads_back():
    data = encode({"id": 1, "method": "get_setup", "params": {"text": "a\nb"}})
    assert data.endswith(b"\n") and data.count(b"\n") == 1
    assert decode_all(data) == [{"id": 1, "method": "get_setup", "params": {"text": "a\nb"}}]


def test_a_line_split_over_three_reads_is_one_message():
    data = encode({"id": 7, "method": "status", "params": {}})
    reader = LineReader()
    assert reader.feed(data[:5]) == []
    assert reader.feed(data[5:9]) == []
    assert reader.feed(data[9:]) == [{"id": 7, "method": "status", "params": {}}]


def test_several_messages_in_one_read_come_in_order():
    data = encode({"id": 1}) + encode({"id": 2}) + encode({"id": 3})
    assert [m["id"] for m in decode_all(data)] == [1, 2, 3]


def test_crlf_and_blank_lines_are_accepted():
    assert decode_all(b'\r\n{"id": 1}\r\n\n{"id": 2}\n') == [{"id": 1}, {"id": 2}]


def test_a_17_mib_line_raises_without_being_read_to_its_end():
    reader = LineReader()
    chunk = b"x" * (1024 * 1024)
    with pytest.raises(ProtocolError):
        for _ in range(17):
            reader.feed(chunk)  # no newline anywhere: the limit stops it


def test_the_limit_is_16_mib():
    assert MAX_LINE == 16 * 1024 * 1024


def test_a_line_over_a_small_limit_raises_even_with_its_newline():
    reader = LineReader(max_line=64)
    with pytest.raises(ProtocolError):
        reader.feed(b'{"id": 1, "method": "' + b"m" * 80 + b'"}\n')


def test_messages_before_a_bad_line_are_kept_on_the_error():
    reader = LineReader()
    with pytest.raises(ProtocolError) as caught:
        reader.feed(b'{"id": 1}\nnot json\n{"id": 3}\n')
    assert caught.value.messages == [{"id": 1}]


@pytest.mark.parametrize("line", [b"[1, 2]", b"42", b'"text"', b'{"id": NaN}', b"\xff\xfe"])
def test_a_line_that_is_not_a_json_object_raises(line):
    with pytest.raises(ProtocolError):
        LineReader().feed(line + b"\n")


def test_a_reader_refuses_more_input_after_an_error():
    reader = LineReader()
    with pytest.raises(ProtocolError):
        reader.feed(b"nope\n")
    with pytest.raises(ProtocolError):
        reader.feed(b'{"id": 1}\n')


def test_a_message_that_cannot_be_encoded_raises():
    with pytest.raises(ProtocolError):
        encode({"value": float("nan")})
    with pytest.raises(ProtocolError):
        encode({"value": object()})


def test_a_message_over_the_limit_is_not_sent():
    with pytest.raises(ProtocolError):
        encode({"text": "x" * (MAX_LINE + 10)})


def test_parse_request_reads_id_method_and_params():
    assert parse_request({"id": 4, "method": "status", "params": {"a": 1}}) == (
        4,
        "status",
        {"a": 1},
    )


def test_parse_request_defaults_params_to_an_empty_dict():
    assert parse_request({"id": 4, "method": "status"}) == (4, "status", {})


@pytest.mark.parametrize(
    "message",
    [
        {"method": "status"},
        {"id": True, "method": "status"},
        {"id": 1.5, "method": "status"},
        {"id": 1},
        {"id": 1, "method": ""},
        {"id": 1, "method": "status", "params": [1]},
    ],
)
def test_a_bad_request_is_a_protocol_error(message):
    with pytest.raises(ProtocolError):
        parse_request(message)


def test_hello_carries_the_token_client_and_protocol():
    message = hello_request(TOKEN, "Codex 1.2")
    assert message == {
        "hello": {"token": TOKEN, "client": "Codex 1.2", "protocol": PROTOCOL_VERSION}
    }
    assert check_hello(message) == (TOKEN, "Codex 1.2")


@pytest.mark.parametrize(
    "message",
    [
        {"id": 1, "method": "status"},
        {"hello": "text"},
        {"hello": {"client": "c", "protocol": 1}},
        {"hello": {"token": TOKEN, "client": "c", "protocol": 2}},
        {"hello": {"token": TOKEN, "client": "c" * 300, "protocol": 1}},
        {"hello": {"token": 5, "client": "c", "protocol": 1}},
    ],
)
def test_a_bad_hello_is_a_protocol_error(message):
    with pytest.raises(ProtocolError):
        check_hello(message)


def test_replies_have_the_documented_shapes():
    assert result_reply(3, {"x": 1}) == {"id": 3, "result": {"x": 1}}
    assert error_reply(3, "NeedsApp", "open the app") == {
        "id": 3,
        "error": {"type": "NeedsApp", "message": "open the app"},
    }


def test_an_error_reply_becomes_the_matching_exception():
    assert type(exception_for({"type": "NeedsApp", "message": "m"})) is NeedsApp
    plain = exception_for({"type": "BackendError", "message": "m"})
    assert type(plain) is BackendError and str(plain) == "m"
    # an unknown type is still a backend error for the agent, never a crash
    unknown = exception_for({"type": "Whatever", "message": "m"})
    assert type(unknown) is BackendError and str(unknown) == "m"


@pytest.fixture
def short_dir():
    # AF_UNIX paths are about 104 bytes at most; pytest's tmp_path can be longer than that.
    path = tempfile.mkdtemp(prefix="p2-")
    yield path
    shutil.rmtree(path, ignore_errors=True)


@pytest.mark.skipif(os.name == "nt", reason="the POSIX socket test")
def test_a_channel_round_trip_over_a_local_socket(short_dir):
    path = os.path.join(short_dir, "s.sock")
    server = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    server.bind(path)
    server.listen(1)

    def serve():
        conn, _ = server.accept()
        with conn:
            channel = Channel(conn.makefile("rwb", buffering=0))
            for _ in range(2):
                message = channel.receive()
                channel.send(result_reply(message["id"], {"echo": message["method"]}))

    thread = threading.Thread(target=serve)
    thread.start()
    try:
        stream = socket_connect(path, timeout=2.0)
        channel = Channel(stream)
        channel.send(request_for(1, "status"))
        assert channel.receive() == {"id": 1, "result": {"echo": "status"}}
        channel.send(request_for(2, "get_setup"))
        assert channel.receive() == {"id": 2, "result": {"echo": "get_setup"}}
        stream.close()
    finally:
        thread.join(5)
        server.close()


def request_for(request_id, method):
    return {"id": request_id, "method": method, "params": {}}


@pytest.mark.skipif(os.name == "nt", reason="the POSIX socket test")
def test_connecting_to_a_missing_socket_raises_oserror(short_dir):
    with pytest.raises(OSError):
        socket_connect(os.path.join(short_dir, "missing.sock"), timeout=1.0)


@pytest.mark.skipif(os.name == "nt", reason="the POSIX socket test")
def test_a_closed_peer_is_a_connection_error(short_dir):
    path = os.path.join(short_dir, "c.sock")
    server = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    server.bind(path)
    server.listen(1)
    stream = socket_connect(path, timeout=2.0)
    conn, _ = server.accept()
    conn.close()
    channel = Channel(stream)
    with pytest.raises(ConnectionError):
        channel.receive()
    stream.close()
    server.close()


def test_a_channel_returns_every_message_of_one_read_before_an_error():
    class Scripted:
        def __init__(self, chunks):
            self.chunks = list(chunks)
            self.written = []

        def read(self, size):
            return self.chunks.pop(0) if self.chunks else b""

        def write(self, data):
            self.written.append(data)
            return len(data)

        def close(self):
            pass

    stream = Scripted([b'{"id": 1}\n{"id": 2}\nbroken\n'])
    channel = Channel(stream)
    assert channel.receive() == {"id": 1}
    assert channel.receive() == {"id": 2}
    with pytest.raises(ProtocolError):
        channel.receive()


def test_the_channel_sends_encoded_lines():
    class Sink:
        def __init__(self):
            self.written = b""

        def write(self, data):
            self.written += data
            return len(data)

        def read(self, size):
            return b""

        def close(self):
            pass

    sink = Sink()
    Channel(sink).send({"id": 1})
    assert sink.written == encode({"id": 1})


def test_deep_nesting_on_the_wire_is_a_protocol_error():
    with pytest.raises(ProtocolError):
        LineReader().feed(b"[" * 100000 + b"\n")
    with pytest.raises(ProtocolError):
        LineReader().feed(b'{"id": 1, "params": ' + b"[" * 100000 + b"\n")


def test_deep_nesting_cannot_be_encoded():
    deep = []
    for _ in range(100000):
        deep = [deep]
    with pytest.raises(ValueError):
        encode({"value": deep})


@pytest.mark.skipif(os.name == "nt", reason="the POSIX socket test")
def test_a_receive_with_no_answer_times_out(short_dir):
    import time

    path = os.path.join(short_dir, "t.sock")
    server = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    server.bind(path)
    server.listen(1)
    stream = socket_connect(path, timeout=2.0)
    conn, _ = server.accept()  # the peer is there, and says nothing
    channel = Channel(stream, timeout=0.2)
    started = time.monotonic()
    with pytest.raises(TimeoutError):
        channel.receive()
    assert time.monotonic() - started < 5
    channel.close()
    conn.close()
    server.close()


def test_a_receive_with_a_timeout_still_returns_what_is_there():
    class Ready:
        def __init__(self):
            self.chunks = [b'{"id": 1}\n']

        def read(self, size):
            return self.chunks.pop(0) if self.chunks else b""

        def wait_readable(self, timeout):
            return True

        def close(self):
            pass

    assert Channel(Ready(), timeout=0.2).receive() == {"id": 1}


def test_before_the_hello_a_line_is_limited_to_4_kib():
    assert HELLO_MAX_LINE == 4 * 1024
    reader = LineReader(max_line=HELLO_MAX_LINE)
    with pytest.raises(ProtocolError):
        reader.feed(b'{"hello": "' + b"x" * 5000 + b'"}\n')


def test_after_the_hello_the_limit_can_be_raised():
    reader = LineReader(max_line=HELLO_MAX_LINE)
    reader.set_max_line(MAX_LINE)
    big = {"id": 1, "method": "set_values", "params": {"text": "x" * 5000}}
    assert reader.feed(encode(big)) == [big]


def test_a_channel_takes_the_raised_limit_after_the_hello():
    class Pipe:
        def __init__(self, data):
            self.data = data

        def read(self, size):
            chunk, self.data = self.data[:size], self.data[size:]
            return chunk

        def write(self, data):
            return len(data)

        def close(self):
            pass

    line = encode({"id": 2, "method": "m", "params": {"text": "x" * 5000}})
    channel = Channel(Pipe(line), max_line=HELLO_MAX_LINE)
    with pytest.raises(ProtocolError):
        channel.receive()
    channel = Channel(Pipe(line), max_line=HELLO_MAX_LINE)
    channel.set_max_line(MAX_LINE)
    assert channel.receive()["id"] == 2


def test_a_token_over_128_characters_is_refused_in_the_hello():
    check_hello(hello_request("a" * 128, "client"))
    with pytest.raises(ProtocolError):
        check_hello(hello_request("a" * 129, "client"))


@pytest.mark.skipif(os.name == "nt", reason="the POSIX socket test")
def test_a_socket_we_own_is_ours(short_dir):
    path = os.path.join(short_dir, "own.sock")
    server = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    server.bind(path)
    try:
        assert socket_is_ours(path)
        assert not socket_is_ours(os.path.join(short_dir, "missing.sock"))
    finally:
        server.close()


@pytest.mark.skipif(os.name == "nt", reason="the POSIX socket test")
def test_a_regular_file_at_the_socket_path_is_not_ours(short_dir):
    path = os.path.join(short_dir, "file.sock")
    with open(path, "w", encoding="utf-8") as handle:
        handle.write("not a socket")
    assert not socket_is_ours(path)
    with pytest.raises(OSError):
        socket_connect(path, timeout=1.0)


@pytest.mark.skipif(os.name == "nt", reason="the POSIX socket test")
def test_a_socket_of_another_user_is_refused_before_connecting(short_dir, monkeypatch):
    path = os.path.join(short_dir, "other.sock")
    server = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    server.bind(path)
    server.listen(1)
    other = os.getuid() + 1  # the real uid is read before it is patched
    monkeypatch.setattr(protocol.os, "getuid", lambda: other)
    try:
        assert not socket_is_ours(path)
        with pytest.raises(PermissionError):
            socket_connect(path, timeout=1.0)
    finally:
        server.close()
