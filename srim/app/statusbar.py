"""The status bar that tells the user what to do next, one line of guidance at a time."""

import html

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QTextDocument
from PySide6.QtWidgets import QLabel, QStatusBar

from srim.app.guidance import Guidance

READY = "Ready to set up a run"


class GuidanceBar(QStatusBar):
    """Shows the first guidance line; a click on its link emits ``jump`` with its target."""

    jump = Signal(str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._label = QLabel(self)
        self._label.setTextFormat(Qt.TextFormat.RichText)
        self._label.setTextInteractionFlags(Qt.TextInteractionFlag.TextBrowserInteraction)
        self._label.linkActivated.connect(self.jump.emit)
        self.addWidget(self._label, 1)
        self.set_messages([])

    def set_messages(self, messages: list[Guidance]) -> None:
        """Show ``messages``: the first as a link, ``(+N more)`` for the rest, all in the tip."""
        if not messages:
            self._label.setText(html.escape(READY))
            self._label.setToolTip("")
            return
        first = messages[0]
        text = html.escape(first.text)
        if first.target is not None:
            text = f'<a href="{html.escape(first.target)}">{text}</a>'
        if len(messages) > 1:
            text += f" (+{len(messages) - 1} more)"
        self._label.setText(text)
        # Qt reads a tooltip as rich text only when it looks like markup, so the <qt> wrapper
        # keeps escaped text (for example "&amp;") from showing its entities.
        tip = "<br>".join(html.escape(message.text) for message in messages)
        self._label.setToolTip(f"<qt>{tip}</qt>")

    def shown_text(self) -> str:
        """The visible text, without markup (for tests)."""
        document = QTextDocument()
        document.setHtml(self._label.text())
        return document.toPlainText()
