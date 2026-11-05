"""Exclusive inter-process file lock (stdlib only: ``fcntl`` on POSIX, ``msvcrt`` on Windows).

Used so two ``run(srim_directory=...)`` calls on the same SRIM directory (which run
in place) take turns instead of overwriting each other's files.
"""

import os
import sys
import time

LOCK_NAME = ".pysrim2.lock"
MESSAGE_AFTER_SECONDS = 5.0

if sys.platform == "win32":
    import msvcrt

    def _try_lock(fd):
        try:
            os.lseek(fd, 0, os.SEEK_SET)
            msvcrt.locking(fd, msvcrt.LK_NBLCK, 1)
        except OSError:
            return False
        return True

    def _unlock(fd):
        os.lseek(fd, 0, os.SEEK_SET)
        msvcrt.locking(fd, msvcrt.LK_UNLCK, 1)

else:
    import fcntl

    def _try_lock(fd):
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            return False
        return True

    def _unlock(fd):
        fcntl.flock(fd, fcntl.LOCK_UN)


class FileLock:
    """Exclusive lock on ``path`` (created if needed); a context manager.

    The lock is released when the holding process exits, even if it is killed.

    Parameters
    ----------
    path : path-like
        The lock file.
    timeout : float, optional
        Seconds to wait before raising :class:`TimeoutError`; ``None`` waits forever.
    message_after : float, optional
        Seconds after which a "waiting" message is written to ``stderr`` (once).
    poll : float, optional
        Seconds between attempts.
    """

    def __init__(self, path, timeout=None, message_after=MESSAGE_AFTER_SECONDS, poll=0.05):
        self.path = os.fspath(path)
        self.timeout = timeout
        self.message_after = message_after
        self.poll = poll
        self._fd = None

    def acquire(self):
        fd = os.open(self.path, os.O_RDWR | os.O_CREAT, 0o666)
        start = time.monotonic()
        told = False
        while not _try_lock(fd):
            waited = time.monotonic() - start
            if self.timeout is not None and waited >= self.timeout:
                os.close(fd)
                raise TimeoutError(
                    f"could not lock {self.path} within {self.timeout:g} s: another pysrim2 "
                    "run is using this SRIM directory"
                )
            if not told and waited >= self.message_after:
                told = True
                print(
                    f"pysrim2: waiting for another run to finish in {os.path.dirname(self.path)} "
                    f"(lock file {self.path})...",
                    file=sys.stderr,
                    flush=True,
                )
            time.sleep(self.poll)
        self._fd = fd
        return self

    def release(self):
        if self._fd is not None:
            try:
                _unlock(self._fd)
            finally:
                os.close(self._fd)
                self._fd = None

    def __enter__(self):
        return self.acquire()

    def __exit__(self, *exc):
        self.release()
