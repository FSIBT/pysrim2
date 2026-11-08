"""The welcome screen and the guided setup: choose a route, install what it needs, check it.

The wizard runs the install steps with ``run_steps(yes=False, confirm=None)``, so an admin step
is never run here: its commands are shown in a box for the user to run in a terminal, and
"Check again" plans the steps once more. The install runs in a thread; its log lines reach the
GUI thread through a queued signal. Nothing here imports anything but Qt, the installer and
the app's own modules.
"""

import html
from collections.abc import Callable
from functools import partial

from PySide6.QtCore import QObject, Signal
from PySide6.QtWidgets import (
    QApplication,
    QButtonGroup,
    QHBoxLayout,
    QLabel,
    QListWidget,
    QPlainTextEdit,
    QPushButton,
    QRadioButton,
    QVBoxLayout,
    QWizard,
    QWizardPage,
)

from srim.app import DOCS_URL, routes
from srim.app.guidance import setup_guidance
from srim.app.settings import AppSettings, save_settings
from srim.app.workers import run_in_thread
from srim.cli import collect_checks
from srim.installer import steps as steps_module
from srim.installer.runner import RunOutcome, run_steps
from srim.installer.steps import ComponentUnavailable, Step

ADMIN_HINT = "run these commands in a terminal, then press Check again:"
ROUTE_DESCRIPTIONS = {
    "native": "runs SRIM's own TRIM.exe (Windows only)",
    "wine": "runs TRIM.exe under wine, in pysrim2's private prefix",
    "container": "runs SRIM inside the pysrim2 container image; needs Podman or Docker",
}


class _LogBridge(QObject):
    """Carries log lines from the install thread to the GUI thread (a queued connection)."""

    line = Signal(str)


class WelcomePage(QWizardPage):
    def __init__(self):
        super().__init__()
        self.setTitle("Welcome to pysrim2")
        text = QLabel(
            "This guide sets up SRIM, the program pysrim2 runs, in one of three ways. "
            "It installs what the route you choose still needs, and it never runs a command "
            "that needs administrator rights: those are shown for you to run in a terminal."
        )
        text.setWordWrap(True)
        docs = QLabel(
            f'Documentation: <a href="{html.escape(DOCS_URL)}">{html.escape(DOCS_URL)}</a>'
        )
        docs.setOpenExternalLinks(True)
        layout = QVBoxLayout(self)
        layout.addWidget(text)
        layout.addWidget(docs)
        layout.addStretch(1)


class RoutePage(QWizardPage):
    def __init__(self, system: str, settings: AppSettings):
        super().__init__()
        self.setTitle("Choose how SRIM runs")
        self._buttons: dict[str, QRadioButton] = {}
        self._group = QButtonGroup(self)
        layout = QVBoxLayout(self)
        recommended = routes.recommended_route()
        offered = routes.available_routes(system)
        preselect = settings.route if settings.route in offered else recommended
        for route in offered:
            label = routes.ROUTE_LABELS[route]
            if route == recommended:
                label += " (recommended)"
            button = QRadioButton(label, self)
            self._group.addButton(button)
            self._buttons[route] = button
            layout.addWidget(button)
            description = QLabel(ROUTE_DESCRIPTIONS[route], self)
            description.setWordWrap(True)
            description.setContentsMargins(24, 0, 0, 8)
            layout.addWidget(description)
            if route == preselect:
                button.setChecked(True)
        if not any(b.isChecked() for b in self._buttons.values()) and self._buttons:
            next(iter(self._buttons.values())).setChecked(True)
        layout.addStretch(1)

    def routes(self) -> list[str]:
        return list(self._buttons)

    def button(self, route: str) -> QRadioButton:
        return self._buttons[route]

    def selected_route(self) -> str:
        for route, button in self._buttons.items():
            if button.isChecked():
                return route
        raise RuntimeError("no route is offered on this system")


class InstallPage(QWizardPage):
    def __init__(self, wizard: "SetupWizard"):
        super().__init__()
        self.setTitle("Install what is missing")
        self._wizard = wizard
        self._steps: list[Step] = []
        self._busy = False
        self._generation = 0
        self._pending_outcome: RunOutcome | None = None
        self._install_note: str | None = None
        self._bridge = _LogBridge(self)
        self._bridge.line.connect(self._append_log)

        self.step_list = QListWidget(self)
        self.status_label = QLabel(self)
        self.status_label.setWordWrap(True)
        self.install_button = QPushButton("Install", self)
        self.install_button.clicked.connect(self.install)
        self.copy_button = QPushButton("Copy commands", self)
        self.copy_button.clicked.connect(self.copy_commands)
        self.check_button = QPushButton("Check again", self)
        self.check_button.clicked.connect(self.refresh)
        self.commands_label = QLabel(ADMIN_HINT, self)
        self.commands_label.setWordWrap(True)
        self.commands_box = QPlainTextEdit(self)
        self.commands_box.setReadOnly(True)
        self.commands_box.setMaximumHeight(120)
        self.log = QPlainTextEdit(self)
        self.log.setReadOnly(True)

        buttons = QHBoxLayout()
        for button in (self.install_button, self.copy_button, self.check_button):
            buttons.addWidget(button)
        buttons.addStretch(1)
        layout = QVBoxLayout(self)
        layout.addWidget(self.step_list)
        layout.addWidget(self.status_label)
        layout.addLayout(buttons)
        layout.addWidget(self.commands_label)
        layout.addWidget(self.commands_box)
        layout.addWidget(self.log)

    @property
    def busy(self) -> bool:
        return self._busy

    def status_text(self) -> str:
        return self.status_label.text()

    def initializePage(self):  # noqa: N802 (Qt's name)
        self.refresh()

    def isComplete(self):  # noqa: N802 (Qt's name)
        # Next stays enabled when the route is unavailable, so Back can pick another route.
        return not self._busy

    def refresh(self) -> None:
        """Plan the steps for the chosen route, then check each one in a thread.

        Each check runs once per refresh. A refresh that is overtaken by a newer one (or by the
        wizard going away) ignores its results.
        """
        self._generation += 1
        generation = self._generation
        self.step_list.clear()
        self._steps = []
        self.commands_box.setPlainText("")
        self.commands_label.setVisible(False)
        self.install_button.setEnabled(False)
        try:
            steps = self._wizard.plan_for_route(self._wizard.route_page.selected_route())
        except ComponentUnavailable as error:
            self.status_label.setText(error.reason)
            self._settle()
            return
        self._steps = steps
        self.status_label.setText("Checking the installation…")
        run_in_thread(
            partial(_inspect_all, steps),
            partial(self._show_rows, generation),
            partial(self._check_failed, generation),
        )

    def install(self) -> None:
        """Run the plain steps in a thread; admin steps are only reported."""
        if self._busy or not self._steps:
            return
        steps = list(self._steps)
        self._set_busy(True)
        self.status_label.setText("Installing…; please wait")
        self.log.clear()
        emit = self._bridge.line.emit

        def work() -> RunOutcome:
            return run_steps(
                steps,
                yes=False,
                confirm=None,
                say=emit,
                warn=emit,
                admin_hint=ADMIN_HINT,
            )

        run_in_thread(work, self._install_done, self._install_failed)

    def copy_commands(self) -> None:
        QApplication.clipboard().setText(self.commands_box.toPlainText())

    def _append_log(self, line: str) -> None:
        self.log.appendPlainText(line)

    def _set_busy(self, busy: bool) -> None:
        self._busy = busy
        for button in (self.install_button, self.copy_button, self.check_button):
            button.setEnabled(not busy)
        # completeChanged makes QWizard recompute Back and Next, so the wizard's install
        # state is applied after it, or Back would come back enabled.
        self.completeChanged.emit()
        self._wizard.set_installing(busy)

    def _install_done(self, outcome: RunOutcome) -> None:
        self._set_busy(False)
        self._pending_outcome = outcome
        self._install_note = _outcome_note(outcome)
        self.refresh()

    def _install_failed(self, error: BaseException) -> None:
        self._set_busy(False)
        self._append_log(f"[fail] install: {error}")
        self._pending_outcome = RunOutcome(failed="install")
        self._install_note = f"The install stopped: {error}"
        self.refresh()

    def _show_rows(self, generation: int, rows: list) -> None:
        if generation != self._generation:
            return
        try:
            self.step_list.clear()
            unmet_admin: list[str] = []
            plain_pending = 0
            admin_pending = 0
            for step, done, commands in rows:
                if done:
                    text = f"{step.name}: installed"
                elif step.needs_admin:
                    text = f"{step.name}: needs admin rights ({step.summary})"
                    admin_pending += 1
                    unmet_admin.extend(commands)
                else:
                    text = f"{step.name}: to install ({step.summary})"
                    plain_pending += 1
                self.step_list.addItem(text)
            self.commands_box.setPlainText("\n".join(unmet_admin))
            self.commands_label.setVisible(bool(unmet_admin))
            self.install_button.setEnabled(plain_pending > 0 and not self._busy)
            if self._install_note is not None:
                status = self._install_note
            elif plain_pending:
                status = "Press Install to set up what is missing."
            elif admin_pending:
                status = "Run the commands below in a terminal, then press Check again."
            elif rows:
                status = "Everything for this route is installed."
            else:
                status = "Nothing to install for this route."
            self.status_label.setText(status)
        except RuntimeError:  # the wizard was deleted while the check ran
            return
        self._settle()

    def _check_failed(self, generation: int, error: BaseException) -> None:
        if generation != self._generation:
            return
        try:
            self.status_label.setText(f"Could not check the installation: {error}")
        except RuntimeError:
            return
        self._settle()

    def _settle(self) -> None:
        """Report a finished install (once) after the page shows its new state."""
        outcome, self._pending_outcome = self._pending_outcome, None
        self._install_note = None
        if outcome is not None:
            self._wizard.install_finished.emit(outcome)


def _check(step: Step) -> bool:
    try:
        return bool(step.check())
    except Exception:  # a broken check counts as not installed; the install reports the error
        return False


def _commands(step: Step) -> list[str]:
    try:
        return list(step.commands())
    except Exception:
        return []


def _inspect_all(steps: list[Step]) -> list[tuple[Step, bool, list[str]]]:
    """Run in a thread: each step's check, once, and the commands of an admin step not done."""
    rows = []
    for step in steps:
        done = _check(step)
        rows.append((step, done, [] if done or not step.needs_admin else _commands(step)))
    return rows


def _outcome_note(outcome: RunOutcome) -> str | None:
    if outcome.interrupted:
        return "The install was cancelled."
    if outcome.failed is not None:
        return f"The install stopped at {outcome.failed}; see the log."
    if outcome.admin_skipped:
        return (
            "Some steps need admin rights: run the commands below in a terminal, "
            "then press Check again."
        )
    return None


class DonePage(QWizardPage):
    def __init__(self, wizard: "SetupWizard"):
        super().__init__()
        self.setTitle("Check the installation")
        self._wizard = wizard
        self._generation = 0
        self._result = QLabel(self)
        self._result.setWordWrap(True)
        layout = QVBoxLayout(self)
        layout.addWidget(self._result)
        layout.addStretch(1)

    def result_text(self) -> str:
        return self._result.text()

    def initializePage(self):  # noqa: N802 (Qt's name)
        self._generation += 1
        route = self._wizard.route_page.selected_route()
        self._result.setText("Checking the installation…")
        run_in_thread(
            partial(self._wizard.checks_fn, route),
            partial(self._show, self._generation, route),
            partial(self._show_error, self._generation),
        )

    def _current(self, generation: int) -> bool:
        """True while this check is the latest one and the wizard is still open."""
        try:
            return generation == self._generation and not self._wizard.is_closed()
        except RuntimeError:  # the wizard was deleted
            return False

    def _show(self, generation: int, route: str, checks: list[dict]) -> None:
        if not self._current(generation):
            return
        problems = setup_guidance(checks, route)
        try:
            if not problems:
                self._result.setText("SRIM is ready.")
            else:
                self._result.setText("\n".join(["Still to do:", *(p.text for p in problems)]))
        except RuntimeError:
            return

    def _show_error(self, generation: int, error: BaseException) -> None:
        if not self._current(generation):
            return
        try:
            self._result.setText(f"Could not check the installation: {error}")
        except RuntimeError:
            return

    def show_error(self, text: str) -> None:
        self._result.setText(text)


class SetupWizard(QWizard):
    """Welcome, route, install and check. Finish saves the route; cancel changes nothing."""

    install_finished = Signal(object)

    def __init__(
        self,
        settings: AppSettings,
        checks_fn: Callable[[str | None], list[dict]] = collect_checks,
        plan_fn: Callable[[list[str]], list[Step]] = steps_module.plan,
        system: str | None = None,
        parent=None,
    ):
        super().__init__(parent)
        self.settings = settings
        self.checks_fn = checks_fn
        self._plan_fn = plan_fn
        self.system = system or steps_module.current_platform()
        self._installing = False
        self._closed = False
        self.setWindowTitle("Set up SRIM")

        self.welcome_page = WelcomePage()
        self.route_page = RoutePage(self.system, settings)
        self.install_page = InstallPage(self)
        self.done_page = DonePage(self)
        for page in (self.welcome_page, self.route_page, self.install_page, self.done_page):
            self.addPage(page)

    def plan_for_route(self, route: str) -> list[Step]:
        """The steps for ``route``; ComponentUnavailable when the platform lacks one of them."""
        return self._plan_fn(routes.route_components(route))

    def set_installing(self, installing: bool) -> None:
        """While an install runs, the wizard cannot be left: Back, Cancel and close are off."""
        self._installing = installing
        self.button(QWizard.BackButton).setEnabled(not installing)
        self.button(QWizard.CancelButton).setEnabled(not installing)

    def is_installing(self) -> bool:
        return self._installing

    def is_closed(self) -> bool:
        """True once the wizard has finished (accepted or rejected)."""
        return self._closed

    # Button clicks reach QWizard's own slots, which the Python methods below do not intercept,
    # so the Back and Cancel buttons are disabled too; these guards cover calls made from code.
    def back(self):  # noqa: D102 (Qt's name)
        if not self._installing:
            super().back()

    def next(self):  # noqa: D102 (Qt's name)
        if not self._installing:
            super().next()

    def restart(self):  # noqa: D102 (Qt's name)
        if not self._installing:
            super().restart()

    def accept(self):  # noqa: D102 (Qt's name)
        if self._installing:
            return
        route = self.route_page.selected_route()
        previous = self.settings.route
        self.settings.route = route
        try:
            save_settings(self.settings)
        except OSError as error:
            self.settings.route = previous
            self.done_page.show_error(f"Could not save the route: {error}")
            return
        self._closed = True
        super().accept()

    def reject(self):  # noqa: D102 (Qt's name)
        if self._installing:
            return
        self._closed = True
        super().reject()

    def closeEvent(self, event):  # noqa: N802 (Qt's name)
        if self._installing:
            event.ignore()
            return
        self._closed = True
        super().closeEvent(event)
