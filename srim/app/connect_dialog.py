"""The Connect an agent dialog: the setup for each AI client, and the pysrim2 skill (Qt).

The dialog shows the config snippet of the chosen client (:mod:`srim.mcp.connect`) in a read-only
monospace box with a Copy button, and where the snippet goes. The command in it is this install's
``pysrim2`` executable. When the MCP extra is missing, the dialog says how to add it.

The skill is checked each time the dialog is shown, and "Install the pysrim2 skill" runs its steps
the way the setup screens do: :func:`srim.installer.runner.run_steps` with ``yes=False`` and no
confirmation, so an admin step is never run from here (the skill has none). The checks and the
install run in threads (:func:`srim.app.workers.run_in_thread`). A result that comes after the
dialog was closed is dropped; an install that is still running keeps the button off until it ends.
"""

from functools import partial

from PySide6.QtCore import Qt
from PySide6.QtGui import QFontDatabase, QGuiApplication
from PySide6.QtWidgets import (
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QHBoxLayout,
    QLabel,
    QPlainTextEdit,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from srim.app.workers import run_in_thread
from srim.installer.runner import RunOutcome, run_steps
from srim.installer.steps import Step, plan
from srim.mcp import INSTALL_HINT, mcp_available
from srim.mcp.connect import (
    CLIENT_LABELS,
    CLIENTS,
    pysrim2_command,
    snippet,
    snippet_location,
)

TITLE = "Connect an agent"
CHECKING_TEXT = "Checking the skill…"
INSTALLED_TEXT = "The pysrim2 skill is installed and up to date."
NOT_INSTALLED_TEXT = (
    "The pysrim2 skill is not installed, or it is from another version of pysrim2. "
    "Installing it tells the agent how to use pysrim2."
)
INSTALLING_TEXT = "Installing the skill…"
CANCELLED_TEXT = "The install was cancelled."
#: the note for an install that finds an admin step (the skill has none, so this is a guard)
ADMIN_TEXT = "The skill needs admin rights here: run its commands in a terminal."


def skill_steps() -> list[Step]:
    """The steps of the ``skill`` component (the pysrim2 skill for coding agents)."""
    return plan(["skill"])


def skill_installed(steps: list[Step]) -> bool:
    """True when every step is satisfied. A check that raises counts as not satisfied."""
    if not steps:
        return False
    for step in steps:
        try:
            if not step.check():
                return False
        except Exception:
            return False
    return True


def install_skill_steps(lines: list[str]) -> RunOutcome:
    """Run the skill's steps without admin rights and without asking (in a thread).

    The output lines are kept in ``lines``: the thread does not touch the widgets.
    """
    return run_steps(
        skill_steps(),
        yes=False,
        confirm=None,
        say=lines.append,
        warn=lines.append,
        admin_hint="run the commands in a terminal",
    )


def outcome_note(outcome: RunOutcome, lines: list[str]) -> str:
    """What an install that did not finish completely says; empty when it finished."""
    if outcome.interrupted:
        return CANCELLED_TEXT
    if outcome.failed is not None:
        last = lines[-1] if lines else outcome.failed
        return f"The install stopped: {last}"
    if outcome.admin_skipped:
        return ADMIN_TEXT
    return ""


class ConnectDialog(QDialog):
    """Choose an agent, copy its setup, and install the skill. Shown without blocking the window."""

    def __init__(self, parent: QWidget | None = None):
        super().__init__(parent)
        self.setWindowTitle(TITLE)
        self.setMinimumWidth(480)
        self._command = pysrim2_command()
        self._text = ""
        #: the checks are numbered: a result of an older check is dropped
        self._check_generation = 0
        self._installing = False
        #: True between a close and the next show: results that come then are not shown
        self._closed = False

        self.client_box = QComboBox(self)
        for client in CLIENTS:
            self.client_box.addItem(CLIENT_LABELS[client], client)
        self.client_box.currentIndexChanged.connect(lambda _index: self._show_client())

        self.note = QLabel(self)
        self.note.setWordWrap(True)
        self.note.setTextFormat(Qt.TextFormat.PlainText)
        if mcp_available():
            self.note.hide()
        else:
            self.note.setText(INSTALL_HINT)

        self.snippet_box = QPlainTextEdit(self)
        self.snippet_box.setReadOnly(True)
        self.snippet_box.setLineWrapMode(QPlainTextEdit.LineWrapMode.NoWrap)
        self.snippet_box.setFont(QFontDatabase.systemFont(QFontDatabase.SystemFont.FixedFont))
        self.copy_button = QPushButton("Copy", self)
        self.copy_button.clicked.connect(self.copy_snippet)
        self.location = QLabel(self)
        self.location.setWordWrap(True)

        self.skill_label = QLabel(CHECKING_TEXT, self)
        self.skill_label.setWordWrap(True)
        self.skill_button = QPushButton("Install the pysrim2 skill", self)
        self.skill_button.setEnabled(False)
        self.skill_button.clicked.connect(self.install_skill)
        self.result_label = QLabel(self)
        self.result_label.setWordWrap(True)
        self.result_label.hide()

        self.buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Close, self)
        self.buttons.rejected.connect(self.reject)

        choice = QHBoxLayout()
        choice.addWidget(QLabel("Agent:", self))
        choice.addWidget(self.client_box, 1)
        copy_row = QHBoxLayout()
        copy_row.addWidget(self.location, 1)
        copy_row.addWidget(self.copy_button)
        skill_row = QHBoxLayout()
        skill_row.addWidget(self.skill_label, 1)
        skill_row.addWidget(self.skill_button)

        layout = QVBoxLayout(self)
        layout.addLayout(choice)
        layout.addWidget(self.note)
        layout.addWidget(self.snippet_box)
        layout.addLayout(copy_row)
        layout.addSpacing(12)
        layout.addLayout(skill_row)
        layout.addWidget(self.result_label)
        layout.addWidget(self.buttons)
        self._show_client()

    # --- the snippet ------------------------------------------------------------------

    def _show_client(self) -> None:
        client = self.client_box.currentData()
        self._text = snippet(client, self._command)
        self.snippet_box.setPlainText(self._text)
        self.location.setText(f"Where it goes: {snippet_location(client)}")

    def copy_snippet(self) -> None:
        QGuiApplication.clipboard().setText(self._text)

    # --- the skill --------------------------------------------------------------------

    def showEvent(self, event):  # noqa: N802 (Qt's name)
        super().showEvent(event)
        self._closed = False
        if not self._installing:
            self._check_skill()

    def closeEvent(self, event):  # noqa: N802 (Qt's name)
        self._closed = True
        super().closeEvent(event)

    def _check_skill(self) -> None:
        self._check_generation += 1
        generation = self._check_generation
        self.skill_label.setText(CHECKING_TEXT)
        self.skill_button.setEnabled(False)
        run_in_thread(
            lambda: skill_installed(skill_steps()),
            partial(self._checked, generation),
            partial(self._check_failed, generation),
        )

    def _checked(self, generation: int, installed: bool) -> None:
        if self._closed or generation != self._check_generation:
            return
        self.skill_label.setText(INSTALLED_TEXT if installed else NOT_INSTALLED_TEXT)
        self.skill_button.setEnabled(not installed and not self._installing)

    def _check_failed(self, generation: int, error: BaseException) -> None:
        if self._closed or generation != self._check_generation:
            return
        self.skill_label.setText(f"Could not check the skill: {error}")
        self.skill_button.setEnabled(not self._installing)

    def install_skill(self) -> None:
        if self._installing:
            return
        self._installing = True
        self._check_generation += 1  # a check that is still running is out of date now
        self.result_label.hide()
        self.skill_label.setText(INSTALLING_TEXT)
        self.skill_button.setEnabled(False)
        lines: list[str] = []
        run_in_thread(
            partial(install_skill_steps, lines),
            partial(self._installed, lines),
            self._install_failed,
        )

    def _installed(self, lines: list[str], outcome: RunOutcome) -> None:
        self._installing = False
        if self._closed:
            return
        note = outcome_note(outcome, lines)
        self.result_label.setText(note)
        self.result_label.setVisible(bool(note))
        self._check_skill()

    def _install_failed(self, error: BaseException) -> None:
        self._installing = False
        if self._closed:
            return
        self.result_label.setText(f"The install stopped: {error}")
        self.result_label.show()
        self._check_skill()
