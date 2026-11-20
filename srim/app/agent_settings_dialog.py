"""The Agent settings dialog: the two ask-first limits of the agent (Qt).

"Ask before runs longer than [N] minutes" and "Ask before using more than [N] % of the cores". OK
saves them with :func:`srim.app.settings.save_settings`; Cancel changes nothing. A limit the user
did not change keeps its saved value exactly (a percentage the spin box cannot show is not rounded
into the file). A save that fails keeps the window's settings as they were.
"""

from dataclasses import replace

from PySide6.QtWidgets import (
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QLabel,
    QMessageBox,
    QSpinBox,
    QVBoxLayout,
    QWidget,
)

from srim.app.settings import AGENT_MINUTES_RANGE, AppSettings, save_settings

#: the core share the spin box offers, in whole percent
CORE_PERCENT_RANGE = (10, 100)
TITLE = "Agent settings"


class AgentSettingsDialog(QDialog):
    """The two limits, edited on a copy and stored on OK."""

    def __init__(self, settings: AppSettings, parent: QWidget | None = None):
        super().__init__(parent)
        self._settings = settings
        self.setWindowTitle(TITLE)

        low, high = AGENT_MINUTES_RANGE
        self.minutes = QSpinBox(self)
        self.minutes.setRange(low, high)
        self.minutes.setSuffix(" minutes")
        self.minutes.setValue(settings.agent_max_minutes)
        self._minutes_shown = self.minutes.value()

        low, high = CORE_PERCENT_RANGE
        self.cores = QSpinBox(self)
        self.cores.setRange(low, high)
        self.cores.setSuffix(" %")
        self.cores.setValue(min(max(round(settings.agent_max_core_fraction * 100), low), high))
        self._cores_shown = self.cores.value()

        form = QFormLayout()
        form.addRow(QLabel("Ask before runs longer than", self), self.minutes)
        form.addRow(QLabel("Ask before using more than", self), self.cores)

        self.buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel, self
        )
        self.buttons.accepted.connect(self.accept)
        self.buttons.rejected.connect(self.reject)

        layout = QVBoxLayout(self)
        layout.addLayout(form)
        layout.addWidget(self.buttons)

    def accept(self) -> None:
        """Store the limits the user changed, then close. A failed save keeps the dialog open."""
        changes = {}
        if self.minutes.value() != self._minutes_shown:
            changes["agent_max_minutes"] = self.minutes.value()
        if self.cores.value() != self._cores_shown:
            changes["agent_max_core_fraction"] = round(self.cores.value() / 100, 2)
        candidate = replace(self._settings, **changes)
        try:
            save_settings(candidate)
        except OSError as error:
            QMessageBox.warning(self, TITLE, f"Could not save the settings: {error}")
            return
        self._settings.agent_max_minutes = candidate.agent_max_minutes
        self._settings.agent_max_core_fraction = candidate.agent_max_core_fraction
        super().accept()
