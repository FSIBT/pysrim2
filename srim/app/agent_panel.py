"""The Agent activity panel: what agents did in the open app, and the answers to their asks (Qt).

A dock at the bottom of the window with one row per entry: the time, the client, what was done
(with the ask and its answer, or the error) and the answer. The entries come from the agent over
the socket, so they are untrusted: every text is shown as plain text (markup is shown as it is),
line breaks and control characters are dropped, and a text longer than :data:`CELL_CAP` characters
is cut with an ellipsis. The panel keeps at most :data:`ROW_CAP` rows and drops the oldest first.

:meth:`AgentPanel.load_recent` shows the newest entries of the activity log when the window opens.
Only the tail of the file is read (see :class:`srim.mcp.activity.ActivityLog`), and an entry that
does not check is skipped.
"""

import time
import unicodedata

from PySide6.QtGui import QBrush, QColor, QPalette
from PySide6.QtWidgets import (
    QAbstractItemView,
    QApplication,
    QDockWidget,
    QTableWidget,
    QTableWidgetItem,
)

from srim.mcp.activity import ActivityEntry, ActivityLog, entry_from_untrusted

TITLE = "Agent activity"
COLUMNS = ("Time", "Client", "Action", "Answer")
ACTION_COLUMN = COLUMNS.index("Action")
#: the most rows the panel keeps; the oldest is dropped first
ROW_CAP = 1000
#: the most characters of one cell's text (the rest is cut with an ellipsis)
CELL_CAP = 300
#: how many of the log's newest entries the panel shows when the window opens
LOAD_COUNT = 50
#: the error colour on a light and on a dark background
ERROR_LIGHT = "#b00020"
ERROR_DARK = "#ff6b6b"
UNKNOWN_TIME = "--:--:--"


def error_colour() -> QColor:
    """The colour of an error in the panel: dark on a light background, light on a dark one."""
    dark = QApplication.palette().color(QPalette.ColorRole.Window).lightness() < 128
    return QColor(ERROR_DARK if dark else ERROR_LIGHT)


def cell_text(text: str, cap: int = CELL_CAP) -> str:
    """``text`` as one line of plain characters, at most ``cap`` characters.

    Every whitespace (a line break too) becomes a space, control and format characters (bidi
    overrides included) are dropped, and runs of spaces are one space.
    """
    kept = []
    for char in text:
        if char.isspace():
            kept.append(" ")
        elif unicodedata.category(char) not in ("Cc", "Cf"):
            kept.append(char)
    line = " ".join("".join(kept).split())
    if len(line) > cap:
        line = line[: cap - 1] + "…"
    return line


def time_text(stamp: float) -> str:
    """The local time of ``stamp`` as ``HH:MM:SS``; a moment the platform cannot show is dashes."""
    try:
        return time.strftime("%H:%M:%S", time.localtime(stamp))
    except (OverflowError, OSError, ValueError):
        return UNKNOWN_TIME


def action_text(entry: ActivityEntry) -> str:
    """What was done: the summary, then the ask and the error when there are any."""
    parts = [cell_text(entry.summary)]
    if entry.ask:
        parts.append(f"(asked: {cell_text(entry.ask)})")
    if entry.error:
        parts.append(f"— failed: {cell_text(entry.error)}")
    return " ".join(parts)


class AgentPanel(QDockWidget):
    """The activity rows of the agent. :meth:`add` appends one; the window shows the dock."""

    def __init__(self, parent=None):
        super().__init__(TITLE, parent)
        self.setObjectName("agent_activity")
        self.table = QTableWidget(0, len(COLUMNS), self)
        self.table.setHorizontalHeaderLabels(list(COLUMNS))
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.verticalHeader().setVisible(False)
        self.table.horizontalHeader().setStretchLastSection(True)
        self.setWidget(self.table)

    def add(self, entry: ActivityEntry) -> None:
        """Append ``entry`` as the newest row, then drop the oldest rows above the cap."""
        row = self.table.rowCount()
        self.table.insertRow(row)
        values = (
            time_text(entry.time),
            cell_text(entry.client),
            action_text(entry),
            cell_text(entry.answer or ""),
        )
        for column, text in enumerate(values):
            item = QTableWidgetItem(text)
            # no tooltip: one made of agent text would be rich text when it looks like markup
            item.setToolTip("")
            if entry.error and column == ACTION_COLUMN:
                item.setForeground(QBrush(error_colour()))
            self.table.setItem(row, column, item)
        while self.table.rowCount() > ROW_CAP:
            self.table.removeRow(0)
        self.table.scrollToBottom()

    def load_recent(self, log: ActivityLog, n: int = LOAD_COUNT) -> None:
        """Show the newest ``n`` entries of ``log`` (at most :data:`ROW_CAP`), oldest first.

        An entry that does not check (a log written by something else, a damaged line) is skipped.
        """
        for entry in log.last(min(max(n, 0), ROW_CAP)):
            try:
                checked = entry_from_untrusted(entry.to_dict())
            except ValueError:
                continue
            self.add(checked)
