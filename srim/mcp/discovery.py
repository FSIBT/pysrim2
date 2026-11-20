"""The discovery file: where the open pysrim2 app listens, and the token that opens it (Qt-free).

While the app runs with its agent socket on, it writes ``<data dir>/app/agent.json``: the name of
its local socket, a random token (32 bytes in hex), its process id and the pysrim2 version. The
file is private to the user (mode 0o600 on POSIX) and is written in one step through a temporary
file, so a reader never sees half of it. A file left behind by an app that crashed is not
removed here: a failed connection is what tells ``pysrim2 mcp`` that no app is there, and the
next app start replaces the file.

The token never goes on a command line or into a log, and it is not part of the endpoint's
repr. The file is read only when it is a regular file that its owner alone can read (POSIX: no
group or other bits, owned by the current uid, not a link or a FIFO); the folders this module
creates are private (0o700), and an existing folder is left as it is.

Windows: the mode bits do nothing there. The file is protected by the user's profile folder
(its ACL), and the socket by QLocalServer's user-access option; the token in the hello is the
real gate on both platforms.
"""

import hmac
import json
import os
import secrets
import stat
import tempfile
import time
from dataclasses import dataclass, field
from pathlib import Path

from srim.paths import data_dir

FILE_NAME = "agent.json"
#: a discovery file is a few hundred bytes; anything bigger is not ours
MAX_BYTES = 64 * 1024
TOKEN_HEX_LENGTH = 64  # 32 random bytes
MAX_SOCKET_NAME = 1024
MAX_VERSION = 64


@dataclass(frozen=True)
class AppEndpoint:
    """Where the app is: ``socket`` is the name ``QLocalServer`` reports, ``token`` opens it."""

    socket: str
    #: never in the repr, so a log line or an error message cannot show it
    token: str = field(repr=False)
    pid: int
    version: str

    def to_dict(self) -> dict:
        return {
            "socket": self.socket,
            "token": self.token,
            "pid": self.pid,
            "version": self.version,
        }


def discovery_path() -> Path:
    """``<data dir>/app/agent.json``, resolved at call time."""
    return data_dir() / "app" / FILE_NAME


def new_token() -> str:
    """A new random token: 32 bytes, as 64 hex characters."""
    return secrets.token_hex(32)


def same_token(given, expected) -> bool:
    """True when ``given`` is the same token as ``expected``, compared in constant time."""
    if not isinstance(given, str) or not isinstance(expected, str):
        return False
    return hmac.compare_digest(given.encode("utf-8"), expected.encode("utf-8"))


def _valid_token(value) -> bool:
    if not isinstance(value, str) or len(value) != TOKEN_HEX_LENGTH:
        return False
    return all(char in "0123456789abcdef" for char in value)


def _from_dict(data) -> AppEndpoint | None:
    """The endpoint in ``data``, or None when a field is missing or of the wrong kind."""
    if not isinstance(data, dict):
        return None
    socket_name, token = data.get("socket"), data.get("token")
    pid, version = data.get("pid"), data.get("version")
    if not isinstance(socket_name, str) or not 0 < len(socket_name) <= MAX_SOCKET_NAME:
        return None
    if not _valid_token(token):
        return None
    if isinstance(pid, bool) or not isinstance(pid, int) or pid <= 0:
        return None
    if not isinstance(version, str) or len(version) > MAX_VERSION:
        return None
    return AppEndpoint(socket_name, token, pid, version)


#: opened without following a link and without waiting for a writer (a FIFO), where the OS has them
_READ_FLAGS = (
    os.O_RDONLY
    | getattr(os, "O_NOFOLLOW", 0)
    | getattr(os, "O_NONBLOCK", 0)
    | getattr(os, "O_BINARY", 0)
)


def _private(status: os.stat_result) -> bool:
    """True for a regular file that only its owner can read, on POSIX (any file on Windows).

    On Windows the mode bits do nothing: the file is protected by its folder's ACL, the user's
    profile, and the token in the hello is the real gate (see the agent section of AGENTS.md).
    """
    if not stat.S_ISREG(status.st_mode):
        return False
    if os.name != "posix":
        return True
    return status.st_uid == os.getuid() and not status.st_mode & 0o077


def _read_data(path: Path):
    """The parsed JSON of ``path``, or None when it is missing, not a private regular file,
    too big, or not JSON."""
    try:
        fd = os.open(path, _READ_FLAGS)
    except OSError:
        return None
    try:
        with os.fdopen(fd, "rb") as handle:
            if not _private(os.fstat(handle.fileno())):
                return None
            raw = handle.read(MAX_BYTES + 1)
    except OSError:
        return None
    if len(raw) > MAX_BYTES:
        return None
    try:
        return json.loads(raw.decode("utf-8"))
    except (ValueError, RecursionError):
        return None


def read_endpoint(path: Path | None = None) -> AppEndpoint | None:
    """The app's endpoint, or None when the file is missing, broken or not a discovery file.

    None does not say the app is gone: it says there is no usable endpoint to try.
    """
    path = path if path is not None else discovery_path()
    return _from_dict(_read_data(path))


def write_endpoint(endpoint: AppEndpoint, path: Path | None = None) -> None:
    """Write the endpoint, replacing any file there, readable only by the user (POSIX).

    The data goes to a temporary file in the same folder, which then replaces the old file in
    one step. A failure leaves the old file as it was and no temporary file behind.
    """
    path = path if path is not None else discovery_path()
    if _from_dict(endpoint.to_dict()) is None:
        raise ValueError("the endpoint is not valid: a reader would not accept the file")
    _make_folders(path.parent)
    fd, temporary = tempfile.mkstemp(dir=path.parent, prefix=".agent-", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(endpoint.to_dict(), handle)
            handle.write("\n")
        os.chmod(temporary, 0o600)
        _replace(temporary, path)
    except BaseException:
        Path(temporary).unlink(missing_ok=True)
        raise


def _make_folders(folder: Path) -> None:
    """Create ``folder`` and any missing parents. A folder created here is private (0o700);
    one that already exists is not touched."""
    missing = []
    current = folder
    while not current.exists() and current != current.parent:
        missing.append(current)
        current = current.parent
    for made in reversed(missing):
        try:
            made.mkdir(mode=0o700)
        except FileExistsError:
            pass


def _replace(source: str, target: Path) -> None:
    """``os.replace``, retried a few times on Windows, where a reader can hold the old file open."""
    attempts = 5 if os.name == "nt" else 1
    for attempt in range(attempts):
        try:
            os.replace(source, target)
            return
        except PermissionError:
            if attempt == attempts - 1:
                raise
            time.sleep(0.05)


def remove_endpoint(token: str, path: Path | None = None) -> None:
    """Delete the discovery file, but only when it holds ``token``: a newer app's file stays."""
    path = path if path is not None else discovery_path()
    data = _read_data(path)
    if not isinstance(data, dict) or not same_token(data.get("token"), token):
        return
    path.unlink(missing_ok=True)
