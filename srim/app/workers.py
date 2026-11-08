"""Run a function in a thread and hand its result back in the GUI thread.

The function runs in a ``QThread`` with no parent; its value (or error) reaches ``done`` (or
``failed``) through a queued signal, so those callbacks run in the GUI thread. The thread is kept
alive by the module set ``_live`` until it has finished, and a finished thread is removed from
that set again. A window that closes while a thread still runs simply ignores the late result.
"""

from collections.abc import Callable
from typing import TypeVar

from PySide6.QtCore import Qt, QThread, Signal

T = TypeVar("T")
_FORGET_WAIT_MS = 5000

_live: set["_Worker"] = set()


class _Worker(QThread):
    succeeded = Signal(object)
    errored = Signal(object)

    def __init__(self, fn: Callable[[], object]):
        super().__init__()
        self._fn = fn

    def run(self) -> None:
        try:
            value = self._fn()
        except Exception as error:
            self.errored.emit(error)
        else:
            self.succeeded.emit(value)

    def forget(self) -> None:
        """Called in the GUI thread once the thread has finished; drops the last reference."""
        self.wait(_FORGET_WAIT_MS)  # finished is emitted just before the thread exits
        _live.discard(self)


def run_in_thread(
    fn: Callable[[], T],
    done: Callable[[T], None],
    failed: Callable[[BaseException], None],
) -> None:
    """Run ``fn`` in a thread; call ``done`` with its value or ``failed`` with its error."""
    thread = _Worker(fn)
    thread.succeeded.connect(done, Qt.ConnectionType.QueuedConnection)
    thread.errored.connect(failed, Qt.ConnectionType.QueuedConnection)
    _live.add(thread)
    thread.finished.connect(thread.forget, Qt.ConnectionType.QueuedConnection)
    thread.start()


def wait_for_threads(msecs: int) -> None:
    """Wait up to ``msecs`` for each thread still running; call it once, at application exit."""
    for thread in list(_live):
        try:
            thread.wait(msecs)
        except RuntimeError:  # already deleted
            _live.discard(thread)
