"""The app's confirm dialog for the agent's ask-first requests (Qt).

When an agent asks for something that needs the user's OK (a long run, an overwrite, a discard),
:class:`ApprovalQueue` shows one :class:`ApprovalDialog` at a time: "<client> wants to: <text>",
with Allow and Deny and a countdown. The answer is ``"allowed"``, ``"denied"`` or ``"timed out"``
(after ``timeout_s`` seconds without one), which is what :mod:`srim.mcp.tools` expects.

The dialog is shown with ``open()``, never ``exec()``: the GUI and the agent socket keep running
while it is up. ``open()`` makes it window-modal, so the window itself takes no input meanwhile.
Requests that come while a dialog is up wait their turn, each with its own answer.

The rules of the answers:

- Deny is the default button, so Enter by accident is a refusal. Esc and the window's close button
  are refusals too, as is closing the app's window (every open request is denied then).
- The request's text is plain text: an agent cannot put markup or links in the dialog.
- A cancel (the server's: the agent's connection has gone) closes the dialog, or removes the
  request from the queue, and nothing is answered to anyone. A Deferred that is cancelled ignores
  any later answer.
"""

import math
import unicodedata
from collections import deque
from collections.abc import Callable
from time import monotonic

from PySide6.QtCore import QObject, Qt, QTimer
from PySide6.QtWidgets import (
    QDialog,
    QHBoxLayout,
    QLabel,
    QPlainTextEdit,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from srim.app.agent_server import Deferred

#: the answers of a request (what the agent's ask gets back)
ALLOWED = "allowed"
DENIED = "denied"
TIMED_OUT = "timed out"
#: the dialog's title
TITLE = "Agent request"
#: the longest client name shown (the rest is cut with an ellipsis)
CLIENT_CAP = 60
#: the most characters of the request shown (the rest is said to be left out, not silently cut)
TEXT_CAP = 2000
#: the tallest the text area gets; a longer request scrolls in it
TEXT_HEIGHT = 160


def _plain(text: str) -> str:
    """``text`` as one line of plain characters: every whitespace (a newline too) becomes a space,
    the control and format characters (bidi overrides included) are dropped, and runs of spaces
    are one space."""
    kept = []
    for ch in text:
        if ch.isspace():
            kept.append(" ")
        elif unicodedata.category(ch) not in ("Cc", "Cf"):
            kept.append(ch)
    return " ".join("".join(kept).split())


def display_client(client: str) -> str:
    """The client's name as the dialog shows it: one line, at most :data:`CLIENT_CAP` characters."""
    name = _plain(client) or "an agent"
    if len(name) > CLIENT_CAP:
        name = name[: CLIENT_CAP - 1] + "…"
    return name


def display_text(text: str) -> str:
    """The request as the dialog shows it: one line, at most :data:`TEXT_CAP` characters, then
    how many more there are."""
    plain = _plain(text)
    if len(plain) <= TEXT_CAP:
        return plain
    return plain[:TEXT_CAP] + f"… ({len(plain) - TEXT_CAP} more characters)"


class ApprovalDialog(QDialog):
    """One request on screen. ``on_done(choice)`` is called once, when the dialog is done."""

    def __init__(
        self,
        client: str,
        text: str,
        timeout_s: int,
        on_done: Callable[[str], None] | None,
        parent: QWidget | None = None,
        allow_delay_s: float = 0.0,
    ):
        super().__init__(parent)
        self._on_done = on_done
        #: what the dialog answers when it is done: a refusal unless Allow was pressed
        self._choice = DENIED
        self.setWindowTitle(TITLE)
        self.setMinimumWidth(360)

        self.text_label = QLabel(f"{display_client(client)} wants to:", self)
        # plain text: an agent's markup is shown as it is, and no link is made
        self.text_label.setTextFormat(Qt.TextFormat.PlainText)
        self.text_label.setWordWrap(True)
        self.text_label.setOpenExternalLinks(False)
        # the request itself: read-only, bounded, scrolling; it takes no keyboard focus, so Enter
        # goes to the default button and never into the text
        self.text_area = QPlainTextEdit(self)
        self.text_area.setPlainText(display_text(text))
        self.text_area.setReadOnly(True)
        self.text_area.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.text_area.setLineWrapMode(QPlainTextEdit.LineWrapMode.WidgetWidth)
        self.text_area.setMaximumHeight(TEXT_HEIGHT)
        self.countdown_label = QLabel(self)
        self.set_remaining(timeout_s)

        self.allow_button = QPushButton("Allow", self)
        self.allow_button.setAutoDefault(False)
        self.allow_button.setDefault(False)
        self.deny_button = QPushButton("Deny", self)
        self.deny_button.setAutoDefault(True)
        self.deny_button.setDefault(True)

        self.allow_button.clicked.connect(self._allow)
        self.deny_button.clicked.connect(self.reject)
        # Allow is off for a moment after the dialog appears, so a late or double click on the
        # place where the previous dialog's Allow was does not allow this request by accident
        self._allow_until = monotonic() + max(allow_delay_s, 0.0)
        self._allow_clock = QTimer(self)
        self._allow_clock.setInterval(100)
        self._allow_clock.timeout.connect(self._update_allow)
        self._update_allow()
        if not self.allow_button.isEnabled():
            self._allow_clock.start()

        buttons = QHBoxLayout()
        buttons.addStretch(1)
        buttons.addWidget(self.allow_button)
        buttons.addWidget(self.deny_button)
        layout = QVBoxLayout(self)
        layout.addWidget(self.text_label)
        layout.addWidget(self.text_area)
        layout.addWidget(self.countdown_label)
        layout.addLayout(buttons)

    def _update_allow(self) -> None:
        """Show the seconds left before Allow works, and enable it once they have passed."""
        remaining = self._allow_until - monotonic()
        if remaining > 0:
            self.allow_button.setText(f"Allow ({math.ceil(remaining)})")
            self.allow_button.setEnabled(False)
        else:
            self.allow_button.setText("Allow")
            self.allow_button.setEnabled(True)
            self._allow_clock.stop()

    def set_remaining(self, seconds: int) -> None:
        self.countdown_label.setText(f"Time left: {seconds} s")

    def expire(self) -> None:
        """No answer in time: the dialog is done with "timed out"."""
        self._choice = TIMED_OUT
        self.reject()

    def detach(self) -> None:
        """The dialog is no longer anyone's: when it is done, nothing is reported."""
        self._on_done = None

    def _allow(self) -> None:
        self._choice = ALLOWED
        self.accept()

    def done(self, result: int) -> None:
        # Esc, the close button and Deny all end in reject(), which is DENIED unless set otherwise
        super().done(result)
        on_done, self._on_done = self._on_done, None
        if on_done is not None:
            on_done(self._choice)


class _Request:
    """One ask: who asked, what for, and the Deferred that answers it."""

    def __init__(self, client: str, text: str, deferred: Deferred):
        self.client = client
        self.text = text
        self.deferred = deferred


class ApprovalQueue(QObject):
    """The requests for the user's OK, one dialog at a time, in the order they came."""

    def __init__(
        self,
        parent_window: QWidget,
        timeout_s: int = 120,
        allow_delay_s: float = 1.0,
        gap_s: float = 0.5,
    ):
        """``allow_delay_s``: how long Allow stays off after a dialog appears. ``gap_s``: how long
        the screen stays empty between one dialog and the next queued one."""
        super().__init__(parent_window)
        self._window = parent_window
        self._timeout_s = timeout_s
        self._allow_delay_s = allow_delay_s
        self._gap_s = gap_s
        self._gap = QTimer(self)
        self._gap.setSingleShot(True)
        self._gap.timeout.connect(self._show_next)
        self._waiting: deque[_Request] = deque()
        self._current: _Request | None = None
        self._dialog: ApprovalDialog | None = None
        self._remaining = timeout_s
        #: one tick a second: the countdown, and the timeout when it runs out
        self._clock = QTimer(self)
        self._clock.setInterval(1000)
        self._clock.timeout.connect(self._tick)
        self._closed = False

    @property
    def shown(self) -> ApprovalDialog | None:
        """The dialog on screen, or None."""
        return self._dialog

    @property
    def waiting(self) -> int:
        """The requests waiting behind the one on screen."""
        return len(self._waiting)

    def request(self, client: str, text: str) -> Deferred:
        """Ask the user about ``text`` for ``client``. The Deferred resolves to the answer."""
        deferred = Deferred()
        if self._closed:
            deferred.resolve(DENIED)
            return deferred
        req = _Request(client, text, deferred)
        deferred.on_cancel(lambda: self._cancel(req))
        self._waiting.append(req)
        self._show_next()
        return deferred

    def close(self) -> None:
        """The window is closing: every open request is denied, and new ones are denied at once."""
        self._closed = True
        self._gap.stop()
        if self._current is not None:
            self._finish(DENIED)
        while self._waiting:
            self._waiting.popleft().deferred.resolve(DENIED)

    # --- internals ----------------------------------------------------------------------------

    def _show_next(self) -> None:
        if self._current is not None or not self._waiting or self._closed or self._gap.isActive():
            return
        req = self._waiting.popleft()
        self._current = req
        self._remaining = self._timeout_s
        dialog = ApprovalDialog(
            req.client,
            req.text,
            self._timeout_s,
            on_done=lambda choice: self._answered(req, choice),
            parent=self._window,
            allow_delay_s=self._allow_delay_s,
        )
        self._dialog = dialog
        self._clock.start()
        self._window.raise_()
        self._window.activateWindow()
        dialog.open()
        dialog.deny_button.setFocus()

    def _answered(self, req: _Request, choice: str) -> None:
        """The dialog is done with ``choice`` (after its own hide)."""
        if self._current is req:
            self._finish(choice)

    def _finish(self, choice: str | None) -> None:
        """Close the request on screen and answer it (None: it was cancelled, nothing to answer),
        then show the next one."""
        req = self._current
        dialog = self._dialog
        self._current = None
        self._dialog = None
        self._clock.stop()
        if dialog is not None:
            dialog.detach()
            dialog.close()
            dialog.deleteLater()
        if req is not None and choice is not None:
            req.deferred.resolve(choice)
        self._after_dialog()

    def _after_dialog(self) -> None:
        """The next queued dialog comes after the gap (at once when the gap is zero)."""
        if self._closed or not self._waiting:
            return
        if self._gap_s > 0:
            self._gap.start(round(self._gap_s * 1000))
        else:
            self._show_next()

    def _tick(self) -> None:
        if self._dialog is None:
            self._clock.stop()
            return
        self._remaining -= 1
        if self._remaining <= 0:
            self._dialog.expire()
        else:
            self._dialog.set_remaining(self._remaining)

    def _cancel(self, req: _Request) -> None:
        """The request's asker has gone: its dialog closes, or it leaves the queue."""
        if self._current is req:
            self._finish(None)
        else:
            try:
                self._waiting.remove(req)
            except ValueError:
                pass
