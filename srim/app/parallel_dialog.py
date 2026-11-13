"""The dialog that offers a long run in parallel: the run's advice and the choice it makes.

"Run in parallel" accepts with the boxes as they are, "Run as one" accepts with one worker and no
split, and Cancel rejects. :meth:`ParallelDialog.choice` is the :class:`~srim.app.runs.ParallelChoice`
the boxes say. The split and container boxes are shown only when the advice offers them.
"""

import math

from PySide6.QtWidgets import (
    QCheckBox,
    QDialog,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QSpinBox,
    QVBoxLayout,
)

from srim.app.runs import Advice, ParallelChoice
from srim.ui.tui.state import format_hms

SPLIT_NOTE = (
    "Batches use different random numbers than one run; the results agree statistically."
)


class ParallelDialog(QDialog):
    """Ask whether a run is run in parallel, split into batches, or in containers."""

    def __init__(self, advice: Advice, *, cores: int, ions: int = 0, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Run in parallel")
        self.advice = advice
        cores = max(1, cores)
        workers = min(max(1, advice.default_workers), cores)

        self.message = QLabel(
            f"This run may take about {format_hms(advice.seconds)} "
            f"({advice.units} units of work). Run it in parallel?",
            self,
        )
        self.message.setWordWrap(True)

        self.workers_box = QSpinBox(self)
        self.workers_box.setRange(1, cores)
        self.workers_box.setValue(workers)

        self.split_box = QCheckBox("Split into batches of", self)
        self.batch_box = QSpinBox(self)
        self.batch_box.setRange(1, max(1, ions))
        self.batch_box.setValue(max(1, math.ceil(ions / workers)) if ions > 0 else 1)
        self.batch_box.setEnabled(False)
        self.split_box.toggled.connect(self.batch_box.setEnabled)
        self.note = QLabel(SPLIT_NOTE, self)
        self.note.setWordWrap(True)

        self.container_box = QCheckBox("Run in containers", self)

        self.run_button = QPushButton("Run in parallel", self)
        self.as_one_button = QPushButton("Run as one", self)
        self.cancel_button = QPushButton("Cancel", self)
        self.run_button.setDefault(True)
        self.run_button.clicked.connect(self.accept)
        self.as_one_button.clicked.connect(self._run_as_one)
        self.cancel_button.clicked.connect(self.reject)

        workers_row = QHBoxLayout()
        workers_row.addWidget(QLabel("Workers", self))
        workers_row.addWidget(self.workers_box)
        workers_row.addStretch(1)
        split_row = QHBoxLayout()
        split_row.addWidget(self.split_box)
        split_row.addWidget(self.batch_box)
        split_row.addStretch(1)
        buttons = QHBoxLayout()
        buttons.addStretch(1)
        for button in (self.run_button, self.as_one_button, self.cancel_button):
            buttons.addWidget(button)

        layout = QVBoxLayout(self)
        layout.addWidget(self.message)
        layout.addLayout(workers_row)
        layout.addLayout(split_row)
        layout.addWidget(self.note)
        layout.addWidget(self.container_box)
        layout.addLayout(buttons)

        self.split_box.setVisible(advice.can_split)
        self.batch_box.setVisible(advice.can_split)
        self.note.setVisible(advice.can_split)
        self.container_box.setVisible(advice.offer_container)

    def choice(self) -> ParallelChoice:
        """The run the boxes say: the workers, the batch size when split, the container back end."""
        split = self.advice.can_split and self.split_box.isChecked()
        container = self.advice.offer_container and self.container_box.isChecked()
        return ParallelChoice(
            workers=self.workers_box.value(),
            batch_ions=self.batch_box.value() if split else None,
            container=container,
        )

    def _run_as_one(self) -> None:
        self.workers_box.setValue(1)
        self.split_box.setChecked(False)
        self.accept()
