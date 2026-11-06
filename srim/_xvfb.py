"""Claim a private X display number for each SRIM run under ``xvfb-run``.

``xvfb-run -a`` probes for a free display and races when dozens of runs start at the
same moment. Instead each run claims a number by holding an exclusive lock on
``<tmp>/.pysrim2-xvfb-<N>.lock`` and starts ``xvfb-run -n <N>``.
"""

import os
import random
import tempfile
import time

from ._lock import FileLock

FIRST_DISPLAY = 100
LAST_DISPLAY = 1100
MAX_START_ATTEMPTS = 3
STARTUP_FAILURE_MARKERS = (
    "xvfb failed to start",
    "server is already active for display",
    "cannot establish any listening sockets",
    "unable to start x server",
    "fatal server error",
)
_sleep = time.sleep


def claim_display(start=FIRST_DISPLAY):
    """Return ``(number, lock)`` for the first free display number >= ``start``.

    The caller keeps ``lock`` (a held :class:`~srim._lock.FileLock`) until its X server
    has exited, then calls ``lock.release()``.
    """
    directory = tempfile.gettempdir()
    for number in range(start, LAST_DISPLAY):
        if os.path.exists(f"/tmp/.X{number}-lock") or os.path.exists(f"/tmp/.X11-unix/X{number}"):
            continue  # an X server of someone else
        lock = FileLock(os.path.join(directory, f".pysrim2-xvfb-{number}.lock"), timeout=0)
        try:
            lock.acquire()
        except (TimeoutError, OSError):  # taken, or a lock file of another user
            continue
        return number, lock
    raise RuntimeError("no free X display number between 100 and 1100")


def is_startup_failure(text):
    """Whether ``text`` (stderr of ``xvfb-run``) says that the X server did not start."""
    lowered = (text or "").lower()
    return any(marker in lowered for marker in STARTUP_FAILURE_MARKERS)


def backoff(attempt):
    """Sleep a jittered, growing time before start attempt ``attempt`` (1 = first retry)."""
    _sleep(random.uniform(0.2, 0.6) * attempt)


def with_display(command, number):
    """Return ``command`` (``xvfb-run ...``) with ``-a`` replaced by ``-n <number>``."""
    rest = [part for part in command[1:] if part != "-a"]
    return [command[0], "-n", str(number), *rest]
