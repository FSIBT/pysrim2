"""The socket protocol between ``pysrim2 mcp`` and the open app (Qt-free).

Messages are JSON objects, one per line, UTF-8 (written as ASCII escapes). A line may not be
longer than :data:`MAX_LINE` bytes (16 MiB, without its newline): a longer line, a line that is
not a JSON object, or a bad request is a :class:`ProtocolError`, never an unbounded read. After a
protocol error the reader refuses more input, and the connection is closed by whoever read it.

The messages:

- hello (the first line of a connection): ``{"hello": {"token", "client", "protocol": 1}}``,
  answered by ``{"hello": {"ok": true, "version"}}`` or ``{"hello": {"ok": false, "error"}}``;
- request: ``{"id": int, "method": str, "params": dict}`` (``params`` may be left out);
- reply: ``{"id", "result": ...}`` or ``{"id", "error": {"type", "message"}}``, where ``type`` is
  ``"NeedsApp"`` or ``"BackendError"`` (anything else reads as a backend error).

A malformed request is answered with ``id`` null and a ``BackendError`` naming the problem.

Limits and timeouts: before the hello a line may be :data:`HELLO_MAX_LINE` (4 KiB) long; the
server raises the limit with :meth:`LineReader.set_max_line` once the hello is accepted. A
:class:`Channel` with a ``timeout`` raises ``TimeoutError`` when no bytes arrive in time: POSIX
waits with ``select``; Windows polls the named pipe with ``PeekNamedPipe`` and reads only when
bytes are there, so neither leaves a read blocked on an app that has gone quiet.
"""

import json
import os
import select
import socket
import stat
import time
from collections import deque
from typing import Any

from srim.mcp.backend import BackendError, NeedsApp

PROTOCOL_VERSION = 1
MAX_LINE = 16 * 1024 * 1024
#: the longest line before the hello: the app reads nothing bigger from a client it has not checked
HELLO_MAX_LINE = 4 * 1024
MAX_TOKEN = 128
#: how much is asked of the socket at a time
CHUNK = 64 * 1024
MAX_CLIENT = 200
ERROR_TYPES = ("NeedsApp", "BackendError")


class ProtocolError(ValueError):
    """A line or message that the protocol does not allow.

    ``messages`` are the messages read before the bad one, in the same read; they are good.
    """

    def __init__(self, message: str, messages: list | None = None):
        super().__init__(message)
        self.messages = list(messages or [])


def _reject_constant(name: str):
    raise ValueError(f"{name} is not allowed in JSON")


def encode(message: dict, max_line: int = MAX_LINE) -> bytes:
    """``message`` as one JSON line, newline included. Raises :class:`ProtocolError`."""
    try:
        text = json.dumps(message, ensure_ascii=True, separators=(",", ":"), allow_nan=False)
    except (TypeError, ValueError, RecursionError) as error:
        raise ProtocolError(f"the message cannot be sent as JSON: {error}") from None
    data = text.encode("ascii") + b"\n"
    if len(data) - 1 > max_line:
        raise ProtocolError("the message is too long for the socket protocol")
    return data


class LineReader:
    """Turns bytes read from a socket into messages. Keeps a partial line between reads."""

    def __init__(self, max_line: int = MAX_LINE):
        self.max_line = max_line
        self._buffer = bytearray()
        #: how many bytes at the start of ``_buffer`` are known to hold no newline
        self._scanned = 0
        self._broken: ProtocolError | None = None

    def set_max_line(self, max_line: int) -> None:
        """Change the longest line accepted from the next :meth:`feed` on."""
        self.max_line = max_line

    def feed(self, data: bytes) -> list[dict]:
        """The messages completed by ``data``, in order. Raises :class:`ProtocolError`."""
        if self._broken is not None:
            raise ProtocolError("the connection had a protocol error and takes no more input")
        self._buffer += data
        messages: list[dict] = []
        start = 0  # where the line being read begins
        search = self._scanned  # bytes before this hold no newline: they were searched already
        while True:
            end = self._buffer.find(b"\n", search)
            if end < 0:
                if len(self._buffer) - start > self.max_line:
                    self._fail("a line is longer than the protocol allows", messages)
                break
            if end - start > self.max_line:
                self._fail("a line is longer than the protocol allows", messages)
            line = bytes(self._buffer[start:end]).rstrip(b"\r")
            start = search = end + 1
            if line.strip():
                messages.append(self._decode(line, messages))
        del self._buffer[:start]
        self._scanned = len(self._buffer)
        return messages

    def _decode(self, line: bytes, messages: list[dict]) -> dict:
        try:
            value = json.loads(line.decode("utf-8"), parse_constant=_reject_constant)
        except (UnicodeDecodeError, ValueError, RecursionError):
            self._fail("a line is not JSON", messages)
        if not isinstance(value, dict):
            self._fail("a line is not a JSON object", messages)
        return value

    def _fail(self, text: str, messages: list[dict]):
        self._broken = ProtocolError(text, messages)
        self._buffer.clear()
        raise self._broken


def parse_request(message: dict) -> tuple[int, str, dict]:
    """``(id, method, params)`` of a request. Raises :class:`ProtocolError` when it is not one."""
    request_id = message.get("id")
    if isinstance(request_id, bool) or not isinstance(request_id, int):
        raise ProtocolError("a request needs an integer id")
    method = message.get("method")
    if not isinstance(method, str) or not method:
        raise ProtocolError("a request needs a method name")
    params = message.get("params", {})
    if not isinstance(params, dict):
        raise ProtocolError("the params of a request must be an object")
    return request_id, method, params


def request(request_id: int, method: str, params: dict | None = None) -> dict:
    return {"id": request_id, "method": method, "params": params or {}}


def hello_request(token: str, client: str) -> dict:
    return {"hello": {"token": token, "client": client, "protocol": PROTOCOL_VERSION}}


def check_hello(message: dict) -> tuple[str, str]:
    """``(token, client)`` of a hello. Raises :class:`ProtocolError` when it is not one."""
    hello = message.get("hello") if isinstance(message, dict) else None
    if not isinstance(hello, dict):
        raise ProtocolError("the first message must be a hello")
    token, client = hello.get("token"), hello.get("client")
    if not isinstance(token, str) or not 0 < len(token) <= MAX_TOKEN:
        raise ProtocolError("the hello needs a token of at most 128 characters")
    if not isinstance(client, str) or len(client) > MAX_CLIENT:
        raise ProtocolError("the hello needs a client name of at most 200 characters")
    if hello.get("protocol") != PROTOCOL_VERSION or type(hello.get("protocol")) is not int:
        raise ProtocolError(f"this app speaks protocol {PROTOCOL_VERSION} only")
    return token, client


def result_reply(request_id: Any, value: Any) -> dict:
    return {"id": request_id, "result": value}


def error_reply(request_id: Any, error_type: str, message: str) -> dict:
    return {"id": request_id, "error": {"type": error_type, "message": message}}


def exception_for(error: dict) -> BackendError:
    """The exception an error reply stands for: :class:`NeedsApp` or :class:`BackendError`."""
    message = str(error.get("message", "the app reported an error"))
    if error.get("type") == "NeedsApp":
        return NeedsApp(message)
    return BackendError(message)


class Stream:
    """A connected byte stream to the app: ``read``, ``write``, ``wait_readable`` and ``close``."""

    def __init__(self, handle, sock: socket.socket | None = None):
        self._handle = handle
        self._sock = sock

    def read(self, size: int) -> bytes:
        """Up to ``size`` bytes; ``b""`` at the end of the stream."""
        return self._handle.read(size) or b""

    def write(self, data: bytes) -> int:
        """Write some of ``data``; the count written (the caller writes the rest)."""
        return self._handle.write(data)

    def set_write_timeout(self, seconds: float | None) -> None:
        """Limit how long one write may wait for the peer to take its bytes (None: no limit).

        POSIX sets the socket's timeout, so a write that makes no progress for ``seconds`` raises
        ``TimeoutError`` instead of blocking. Windows named pipes are not limited here (the write
        goes through the file object), so a stalled pipe can still block a write.
        """
        if self._sock is not None:
            self._sock.settimeout(seconds)

    def wait_readable(self, seconds: float | None) -> bool:
        """True when a read will not wait: data, or the end of the stream, is there.

        False when ``seconds`` pass first (None waits for ever). POSIX uses ``select``; on Windows
        the named pipe is polled with ``PeekNamedPipe``, so a read is never started on an empty
        pipe. Neither changes the socket's own timeout.
        """
        if os.name == "nt":
            return _pipe_readable(self._handle, seconds)
        target = self._sock if self._sock is not None else self._handle
        ready, _, _ = select.select([target], [], [], seconds)
        return bool(ready)

    def close(self) -> None:
        try:
            self._handle.close()
        finally:
            if self._sock is not None:
                self._sock.close()


def _pipe_readable(handle, seconds: float | None) -> bool:
    """Windows: poll a named pipe's buffer until it has bytes or ``seconds`` pass.

    ``PeekNamedPipe`` reads nothing. A pipe that is broken counts as readable, so the read that
    follows reports the end of the stream instead of waiting.
    """
    import ctypes
    import msvcrt
    from ctypes import wintypes

    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    peek = kernel32.PeekNamedPipe
    peek.argtypes = [
        wintypes.HANDLE,
        ctypes.c_void_p,
        wintypes.DWORD,
        ctypes.POINTER(wintypes.DWORD),
        ctypes.POINTER(wintypes.DWORD),
        ctypes.POINTER(wintypes.DWORD),
    ]
    peek.restype = wintypes.BOOL
    osfhandle = msvcrt.get_osfhandle(handle.fileno())
    deadline = None if seconds is None else time.monotonic() + seconds
    while True:
        available = wintypes.DWORD(0)
        if not peek(osfhandle, None, 0, None, ctypes.byref(available), None):
            return True
        if available.value:
            return True
        if deadline is not None and time.monotonic() >= deadline:
            return False
        time.sleep(0.01)


def socket_is_ours(name: str) -> bool:
    """True when ``name`` is a socket that the current user owns (POSIX).

    A client checks this before it sends the token, so a socket that another user made in the
    temporary folder is never told the token. A link is not a socket here (``lstat`` is used),
    and a missing path is not ours. Windows has no owner check here: the pipe is created by
    QLocalServer with its user-access option, which is what keeps other users out.

    The server side must listen with ``QLocalServer`` and ``UserAccessOption``. Qt creates the
    Unix socket in ``QDir.tempPath()`` (``$TMPDIR`` on macOS) under the name it was given.
    """
    if os.name != "posix":
        return True
    try:
        status = os.lstat(name)
    except OSError:
        return False
    return stat.S_ISSOCK(status.st_mode) and status.st_uid == os.getuid()


def socket_connect(name: str, timeout: float = 2.0) -> Stream:
    """Connect to the app's socket ``name``. Raises ``OSError`` when it cannot be reached.

    On POSIX ``name`` is the socket's path (a Unix socket). On Windows it is the pipe's name, or
    the full ``\\\\.\\pipe\\`` path. ``timeout`` limits the connect only; a receive takes its
    own deadline (see :class:`Channel`). On POSIX a socket that is not this user's is refused
    with ``PermissionError`` before anything is sent (see :func:`socket_is_ours`).
    """
    if os.name == "nt":
        path = name if name.startswith("\\\\.\\pipe\\") else rf"\\.\pipe\{name}"
        return Stream(open(path, "r+b", buffering=0))
    if not socket_is_ours(name):
        raise PermissionError(f"{name} is not a socket of this user")
    sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    try:
        sock.settimeout(timeout)
        sock.connect(name)
        sock.settimeout(None)
    except BaseException:
        sock.close()
        raise
    return Stream(sock.makefile("rwb", buffering=0), sock)


class Channel:
    """Messages over a stream: send one, receive the next (reads a chunk at a time)."""

    def __init__(self, stream, max_line: int = MAX_LINE, timeout: float | None = None):
        self.stream = stream
        self._reader = LineReader(max_line)
        self._pending: deque[dict] = deque()
        self._error: ProtocolError | None = None
        #: seconds a :meth:`receive` may wait for the next message (None waits for ever)
        self.timeout = timeout
        #: seconds a :meth:`send` may wait for the peer to take the bytes (None waits for ever)
        self.send_timeout: float | None = None

    def send(self, message: dict) -> None:
        """Send ``message``. Raises :class:`ProtocolError` (not sent), ``ConnectionError``, or
        ``TimeoutError`` when the peer takes no bytes for ``send_timeout`` seconds (what was sent
        before is not taken back, so the caller closes the connection)."""
        data = encode(message, self._reader.max_line)
        # a stream with no write limit (a plain file object) is written as it is
        limit = getattr(self.stream, "set_write_timeout", None)
        if limit is not None:
            limit(self.send_timeout)
        try:
            while data:
                sent = self.stream.write(data)
                if not sent:
                    raise ConnectionError("the connection closed while sending")
                data = data[sent:]
        finally:
            if limit is not None:
                limit(None)

    def set_max_line(self, max_line: int) -> None:
        """Change the longest line accepted from now on (a small limit before the hello)."""
        self._reader.set_max_line(max_line)

    def receive(self) -> dict:
        """The next message. Raises ``ConnectionError`` at the end of the stream,
        :class:`ProtocolError` for a bad line (after the good ones read with it), and
        ``TimeoutError`` when ``timeout`` passes with nothing to read. After a timeout the
        stream may hold part of a message, so the caller closes it."""
        deadline = None if self.timeout is None else time.monotonic() + self.timeout
        while not self._pending:
            if self._error is not None:
                raise self._error
            if deadline is not None:
                remaining = deadline - time.monotonic()
                if remaining <= 0 or not self.stream.wait_readable(remaining):
                    raise TimeoutError("the app did not answer in time")
            data = self.stream.read(CHUNK)
            if not data:
                raise ConnectionError("the connection closed")
            try:
                self._pending.extend(self._reader.feed(data))
            except ProtocolError as error:
                self._pending.extend(error.messages)
                self._error = error
        return self._pending.popleft()

    def close(self) -> None:
        self.stream.close()
