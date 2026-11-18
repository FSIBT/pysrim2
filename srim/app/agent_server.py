"""The app's agent socket: the server that ``pysrim2 mcp`` connects to (Qt).

While the app runs with its agent socket on, it listens on a per-user local socket (``QLocalServer``:
a Unix socket in the temporary folder on POSIX, a named pipe on Windows) and writes its discovery
file (:mod:`srim.mcp.discovery`), only after listening has succeeded. A connection opens with a
hello that carries the token. After that each request goes to the handler, on the GUI thread, one
at a time across all connections; the answers on one connection come back in the order its requests
were sent.

The handler has ``handle(method, params)``. It returns the answer (JSON data), or a
:class:`Deferred` when the answer comes later (a dialog, a load in a thread). The server then waits
for that answer and answers nothing else before it. It raises :class:`BackendError` (its message
goes to the agent as it is) or any other exception (logged; the agent gets its text).

Nothing here waits on the GUI thread: Qt queues the writes. A peer that stops reading is dropped:
when its unsent answers pass :data:`MAX_PENDING_BYTES` in all, or when they make no progress for
:data:`STALL_MS`. The token is never logged, and never put in an answer.
"""

import getpass
import logging
import os
import re
import secrets
from dataclasses import dataclass

from PySide6.QtCore import QDir, QObject, QTimer, Signal
from PySide6.QtNetwork import QLocalServer, QLocalSocket

import srim
from srim.mcp.backend import BackendError, NeedsApp
from srim.mcp.discovery import AppEndpoint, new_token, remove_endpoint, same_token, write_endpoint
from srim.mcp.protocol import (
    HELLO_MAX_LINE,
    MAX_LINE,
    LineReader,
    ProtocolError,
    check_hello,
    encode,
    error_reply,
    parse_request,
    result_reply,
    socket_connect,
    socket_is_ours,
)

logger = logging.getLogger(__name__)

#: how long a connection may stay open without its hello
HELLO_TIMEOUT_MS = 5000
#: a peer whose unsent answers make no progress for this long is dropped
STALL_MS = 10_000
#: a peer whose unsent answers pass this many bytes is dropped at once (two longest lines)
MAX_PENDING_BYTES = 2 * MAX_LINE
#: how long a connection that was asked to close may take to take its last answers
CLOSE_GRACE_MS = 2000
#: connections at once; a further connection is closed as soon as it arrives
MAX_CONNECTIONS = 16
#: requests of one connection that may wait for their turn
MAX_WAITING = 32
#: the reply to a hello that is refused (the token is never in it)
TOKEN_REFUSED_TEXT = "the token is not this app's"


class AgentSocketError(RuntimeError):
    """The agent socket could not start. The message is for the user."""


class Deferred(QObject):
    """An answer that comes later. A handler returns one; the server waits for it.

    :meth:`resolve` or :meth:`reject` settles it once (later calls are ignored), and ``done``
    is emitted with ``(ok, value_or_error)``. Settle it in the GUI thread. ``outcome`` keeps the
    result, so a Deferred settled before the server looks at it is still answered.
    """

    done = Signal(bool, object)

    def __init__(self, parent: QObject | None = None):
        super().__init__(parent)
        self.outcome: tuple[bool, object] | None = None

    @property
    def settled(self) -> bool:
        return self.outcome is not None

    def resolve(self, value) -> None:
        self._settle(True, value)

    def reject(self, error: BackendError) -> None:
        if not isinstance(error, BackendError):
            error = BackendError(str(error))
        self._settle(False, error)

    def _settle(self, ok: bool, payload) -> None:
        if self.outcome is not None:
            return
        self.outcome = (ok, payload)
        self.done.emit(ok, payload)


class UnavailableBackend:
    """The handler while the app has no agent backend: every request is refused."""

    def handle(self, method: str, params: dict):
        raise BackendError("the app is not ready for agents yet")


def new_socket_name() -> str:
    """A socket name for this start: ``pysrim2-<user>-<8 random hex digits>``.

    The name only keeps other users' sockets apart; the token in the hello is the real gate.
    """
    try:
        user = getpass.getuser()
    except Exception:  # no login name in this environment
        user = ""
    user = re.sub(r"[^A-Za-z0-9]", "", user)[:16] or "user"
    return f"pysrim2-{user}-{secrets.token_hex(4)}"


def _answers(path: str) -> bool:
    """True when something accepts a connection at ``path`` (POSIX; the owner was checked)."""
    try:
        stream = socket_connect(path, timeout=1.0)
    except OSError:
        return False
    stream.close()
    return True


def _clear_stale_socket(path: str) -> None:
    """POSIX: remove a socket file at ``path`` only when it is this user's and nothing answers it.

    This must run before ``QLocalServer.listen``: Qt silently replaces a socket file that another
    running app is listening on, so a failed listen is too late to tell the two apart. Any other
    file at ``path``, or a socket that answers, is refused and left alone.
    """
    if not os.path.lexists(path):
        return
    if not socket_is_ours(path) or _answers(path):
        raise AgentSocketError("the agent socket name is in use by another program")
    try:
        os.unlink(path)
    except OSError as error:
        raise AgentSocketError(f"a stale agent socket could not be removed: {error}") from None


def _listen(server: QLocalServer) -> None:
    """Listen on a new name (see :func:`_clear_stale_socket` for POSIX). On Windows a pipe that is
    taken makes ``listen`` fail, and that is refused too: nothing is shared."""
    name = new_socket_name()
    if os.name == "posix":
        _clear_stale_socket(os.path.join(QDir.tempPath(), name))
    if not server.listen(name):
        raise AgentSocketError(f"the agent socket could not start: {server.errorString()}")


def _timer(parent: QObject, single_shot: bool = True) -> QTimer:
    timer = QTimer(parent)
    timer.setSingleShot(single_shot)
    return timer


@dataclass
class _Job:
    """One thing waiting for the handler: a request, or a refusal that closes its connection."""

    peer: "_Peer"
    message: dict | None = None
    refusal: str | None = None


class _Peer:
    """One connection: its socket (None once it is gone), its reader and its timers."""

    def __init__(self, socket: QLocalSocket, parent: QObject):
        self.socket: QLocalSocket | None = socket
        #: a hello is at most HELLO_MAX_LINE long until it is accepted
        self.reader = LineReader(HELLO_MAX_LINE)
        #: the client's name, once the hello is accepted
        self.client: str | None = None
        #: no more input is read (a refusal or a protocol error is queued or sent)
        self.closing = False
        #: the socket has disconnected
        self.gone = False
        #: requests of this connection waiting for their turn
        self.waiting = 0
        self.hello_timer = _timer(parent)
        self.close_timer = _timer(parent)
        self.stall_timer = _timer(parent)


class AgentServer(QObject):
    """Listens for agent connections and hands their requests to ``handler``.

    ``client_seen(name)`` and the ``client_connected`` signal are called when a hello is accepted.
    ``hello_timeout_ms``, ``stall_ms`` and ``max_pending`` are the limits above (tests shorten them).
    """

    client_connected = Signal(str)

    def __init__(
        self,
        handler,
        client_seen=None,
        parent: QObject | None = None,
        *,
        hello_timeout_ms: int = HELLO_TIMEOUT_MS,
        stall_ms: int = STALL_MS,
        max_pending: int = MAX_PENDING_BYTES,
    ):
        super().__init__(parent)
        self._handler = handler
        self._client_seen = client_seen
        self._hello_timeout_ms = hello_timeout_ms
        self._stall_ms = stall_ms
        self._max_pending = max_pending
        self._server: QLocalServer | None = None
        self._token: str | None = None
        #: bumped by start and stop, so an answer from an earlier start is dropped
        self._session = 0
        self._peers: set[_Peer] = set()
        self._queue: list[_Job] = []
        self._waiting: Deferred | None = None
        self._pumping = False

    @property
    def listening(self) -> bool:
        return self._server is not None and self._server.isListening()

    @property
    def connection_count(self) -> int:
        """The connections still open (a connection that is closing counts until it is gone)."""
        return len(self._peers)

    def start(self) -> None:
        """Listen, then write the discovery file. Raises :class:`AgentSocketError`."""
        if self._server is not None:
            return
        server = QLocalServer(self)
        server.setSocketOptions(QLocalServer.SocketOption.UserAccessOption)
        token = new_token()
        try:
            _listen(server)
            endpoint = AppEndpoint(server.fullServerName(), token, os.getpid(), srim.__version__)
            write_endpoint(endpoint)
        except AgentSocketError:
            server.close()
            server.deleteLater()
            raise
        except (OSError, ValueError) as error:
            server.close()
            server.deleteLater()
            raise AgentSocketError(f"the discovery file could not be written: {error}") from None
        self._server = server
        self._token = token
        self._session += 1
        server.newConnection.connect(self._accept_all)

    def stop(self) -> None:
        """Close the server and every connection, and remove the discovery file. Safe to repeat."""
        server, token = self._server, self._token
        if server is None:
            return
        self._server = None
        self._token = None
        self._session += 1
        server.close()
        for peer in list(self._peers):
            self._drop(peer)
        self._queue.clear()
        self._waiting = None
        try:
            remove_endpoint(token)
        except OSError:
            logger.warning("the agent discovery file could not be removed")
        server.deleteLater()

    # --- connections -----------------------------------------------------------

    def _accept_all(self) -> None:
        while self._server is not None and self._server.hasPendingConnections():
            socket = self._server.nextPendingConnection()
            if len(self._peers) >= MAX_CONNECTIONS:
                socket.abort()
                continue
            self._adopt(socket)

    def _adopt(self, socket: QLocalSocket) -> None:
        peer = _Peer(socket, self)
        self._peers.add(peer)
        socket.readyRead.connect(lambda: self._read(peer))
        socket.disconnected.connect(lambda: self._gone(peer))
        socket.bytesWritten.connect(lambda _count: self._written(peer))
        peer.hello_timer.timeout.connect(lambda: self._hello_expired(peer))
        peer.close_timer.timeout.connect(lambda: self._drop(peer))
        peer.stall_timer.timeout.connect(lambda: self._stalled(peer))
        peer.hello_timer.start(self._hello_timeout_ms)

    def _read(self, peer: _Peer) -> None:
        if peer.closing or peer.gone or peer.socket is None:
            return
        data = bytes(peer.socket.readAll())
        error: ProtocolError | None = None
        try:
            messages = peer.reader.feed(data)
        except ProtocolError as caught:
            messages, error = caught.messages, caught
        for message in messages:
            if peer.client is None:
                self._hello(peer, message)
            else:
                self._add_request(peer, message)
            if peer.closing:
                break
        if error is not None and not peer.closing:
            if peer.client is None:
                self._refuse(peer, str(error))
            else:
                # after the requests read with it, so its answer comes last
                self._queue.append(_Job(peer, refusal=str(error)))
                peer.closing = True
        self._pump()

    def _hello(self, peer: _Peer, message: dict) -> None:
        try:
            token, client = check_hello(message)
        except ProtocolError as error:
            self._refuse(peer, str(error))
            return
        if not same_token(token, self._token):
            logger.warning("an agent connection with a wrong token was closed")
            self._refuse(peer, TOKEN_REFUSED_TEXT)
            return
        peer.client = client
        peer.hello_timer.stop()
        self._send(peer, {"hello": {"ok": True, "version": srim.__version__}})
        # only now a request may be longer than the hello's limit
        peer.reader.set_max_line(MAX_LINE)
        if self._client_seen is not None:
            self._client_seen(client)
        self.client_connected.emit(client)

    def _refuse(self, peer: _Peer, text: str) -> None:
        self._send(peer, {"hello": {"ok": False, "error": text}})
        self._close(peer)

    def _add_request(self, peer: _Peer, message: dict) -> None:
        if peer.waiting >= MAX_WAITING:
            self._queue.append(_Job(peer, refusal="too many requests are waiting for the app"))
            peer.closing = True
            return
        peer.waiting += 1
        self._queue.append(_Job(peer, message=message))

    def _hello_expired(self, peer: _Peer) -> None:
        if peer.client is None:
            self._drop(peer)

    def _close(self, peer: _Peer) -> None:
        """Stop reading and disconnect once the queued answers are written; a peer that still
        takes nothing after CLOSE_GRACE_MS is dropped."""
        peer.closing = True
        peer.hello_timer.stop()
        if peer.socket is not None and not peer.gone:
            peer.socket.disconnectFromServer()
            peer.close_timer.start(CLOSE_GRACE_MS)

    def _drop(self, peer: _Peer) -> None:
        if peer.socket is not None:
            peer.socket.abort()
        self._gone(peer)

    def _gone(self, peer: _Peer) -> None:
        if peer.gone:
            return
        peer.gone = True
        peer.closing = True
        for timer in (peer.hello_timer, peer.close_timer, peer.stall_timer):
            timer.stop()
        self._peers.discard(peer)
        # a request that is still queued from a peer that has gone is never handled
        self._queue = [job for job in self._queue if job.peer is not peer]
        socket, peer.socket = peer.socket, None
        if socket is not None:
            socket.deleteLater()

    def _stalled(self, peer: _Peer) -> None:
        logger.warning("an agent stopped reading its answers; its connection was closed")
        self._drop(peer)

    def _written(self, peer: _Peer) -> None:
        if peer.socket is None:
            return
        if peer.socket.bytesToWrite():
            peer.stall_timer.start(self._stall_ms)  # progress: the wait starts again
        else:
            peer.stall_timer.stop()

    def _send(self, peer: _Peer, message: dict) -> None:
        if peer.socket is None or peer.gone:
            return
        try:
            data = encode(message)
        except ProtocolError:
            data = encode(
                error_reply(
                    message.get("id"), "BackendError", "the answer cannot be sent to the agent"
                )
            )
        peer.socket.write(data)
        pending = peer.socket.bytesToWrite()
        if pending > self._max_pending:
            logger.warning("an agent stopped reading its answers; its connection was closed")
            self._drop(peer)
        elif pending and not peer.stall_timer.isActive():
            peer.stall_timer.start(self._stall_ms)

    # --- requests --------------------------------------------------------------

    def _pump(self) -> None:
        """Hand the queued jobs to the handler, in order, until one is still waiting for its
        answer."""
        if self._pumping:
            return
        self._pumping = True
        try:
            while self._queue and self._waiting is None:
                job = self._queue.pop(0)
                if job.peer.gone:
                    continue
                if job.refusal is not None:
                    self._send(job.peer, error_reply(None, "BackendError", job.refusal))
                    self._close(job.peer)
                    continue
                job.peer.waiting -= 1
                self._answer(job.peer, job.message)
        finally:
            self._pumping = False

    def _answer(self, peer: _Peer, message: dict) -> None:
        raw_id = message.get("id")
        request_id = raw_id if type(raw_id) is int else None
        try:
            request_id, method, params = parse_request(message)
        except ProtocolError as error:
            self._send(peer, error_reply(request_id, "BackendError", str(error)))
            return
        try:
            value = self._handler.handle(method, params)
        except NeedsApp as error:
            self._send(peer, error_reply(request_id, "NeedsApp", str(error)))
        except BackendError as error:
            self._send(peer, error_reply(request_id, "BackendError", str(error)))
        except Exception as error:
            logger.exception("an agent request (%s) failed", method)
            text = str(error) or "the request failed"
            self._send(peer, error_reply(request_id, "BackendError", text))
        else:
            if isinstance(value, Deferred):
                self._wait_for(peer, request_id, value)
            else:
                self._send(peer, result_reply(request_id, value))

    def _wait_for(self, peer: _Peer, request_id, deferred: Deferred) -> None:
        if deferred.settled:
            ok, payload = deferred.outcome
            self._reply_outcome(peer, request_id, ok, payload)
            return
        session = self._session
        self._waiting = deferred
        deferred.done.connect(
            lambda ok, payload: self._answered(session, peer, request_id, ok, payload)
        )

    def _answered(self, session: int, peer: _Peer, request_id, ok: bool, payload) -> None:
        if session != self._session:
            return  # the server stopped (or started again) since
        self._waiting = None
        self._reply_outcome(peer, request_id, ok, payload)
        self._pump()

    def _reply_outcome(self, peer: _Peer, request_id, ok: bool, payload) -> None:
        if ok:
            self._send(peer, result_reply(request_id, payload))
            return
        error_type = "NeedsApp" if isinstance(payload, NeedsApp) else "BackendError"
        self._send(peer, error_reply(request_id, error_type, str(payload)))
