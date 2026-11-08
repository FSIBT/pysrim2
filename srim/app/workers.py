"""Run a function in a thread and hand its result back in the GUI thread.

The function runs in a ``QThread``; its value (or error) reaches ``done`` (or ``failed``) through
a queued signal, so those callbacks run in the GUI thread. The thread object is kept in a set
until it has finished, so it is not garbage-collected while it runs.
"""

from collections.abc import Callable
from typing import TypeVar

from PySide6.QtCore import QObject, Qt, QThread, Signal

T = TypeVar("T")

_live: set[QThread] = set()


class _Worker(QThread):
    succeeded = Signal(object)
    errored = Signal(object)

    def __init__(self, parent: QObject, fn: Callable[[], object]):
        super().__init__(parent)
        self._fn = fn

    def run(self) -> None:
        try:
            value = self._fn()
        except Exception as error:
            self.errored.emit(error)
        else:
            self.succeeded.emit(value)


def run_in_thread(
    parent: QObject,
    fn: Callable[[], T],
    done: Callable[[T], None],
    failed: Callable[[BaseException], None],
) -> None:
    """Run ``fn`` in a thread; call ``done`` with its value or ``failed`` with its error."""
    thread = _Worker(parent, fn)
    thread.succeeded.connect(done, Qt.ConnectionType.QueuedConnection)
    thread.errored.connect(failed, Qt.ConnectionType.QueuedConnection)
    _live.add(thread)
    thread.finished.connect(lambda: _live.discard(thread))
    thread.start()


def wait_for_threads(msecs: int) -> None:
    """Wait up to ``msecs`` for each thread started by :func:`run_in_thread` to finish."""
    for thread in list(_live):
        try:
            thread.wait(msecs)
        except RuntimeError:  # already deleted by its parent
            _live.discard(thread)
