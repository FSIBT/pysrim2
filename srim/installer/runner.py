"""Run install steps, one after another, for the command line and the app.

``run_steps`` owns the decisions (skip what is installed, wait for requirements, ask before an
admin step, stop at the first failure) and reports them through ``say`` and ``warn``. It returns
a ``RunOutcome`` that says what happened; the caller turns that into an exit code or a screen.
"""

import subprocess
import sys
from collections.abc import Callable
from dataclasses import dataclass, field

from srim.installer import container, skill
from srim.installer.srim_install import NOTICE, SrimInstallError
from srim.installer.steps import Step
from srim.installer.vb5 import Vb5SetupError
from srim.installer.wine import WineSetupError
from srim.paths import srim_dir

INSTALL_ERRORS = (
    SrimInstallError,
    WineSetupError,
    container.ContainerError,
    Vb5SetupError,
    skill.SkillInstallError,
    ValueError,
    subprocess.SubprocessError,
    OSError,
)

EXIT_OK = 0
EXIT_FAILED = 1
EXIT_ADMIN_SKIPPED = 2
EXIT_INTERRUPTED = 130


@dataclass
class RunOutcome:
    """What ``run_steps`` did.

    ``admin_skipped`` lists the steps that were not run for want of admin rights (and the
    steps only described in comments), with their commands. ``failed`` is the name of the step
    that raised, and ``interrupted`` is set when the user cancelled.
    """

    installed: list[str] = field(default_factory=list)
    admin_skipped: list[tuple[str, list[str]]] = field(default_factory=list)
    failed: str | None = None
    interrupted: bool = False

    @property
    def exit_code(self) -> int:
        if self.interrupted:
            return EXIT_INTERRUPTED
        if self.failed is not None:
            return EXIT_FAILED
        if self.admin_skipped:
            return EXIT_ADMIN_SKIPPED
        return EXIT_OK


def _stderr(line: str) -> None:
    print(line, file=sys.stderr)


def _print_commands(say: Callable[[str], None], commands: list[str]) -> None:
    for command in commands:
        say(f"    {command}")


def run_steps(
    steps: list[Step],
    *,
    dry_run: bool = False,
    yes: bool = False,
    confirm: Callable[[Step, list[str]], bool] | None = None,
    say: Callable[[str], None] = print,
    warn: Callable[[str], None] = _stderr,
    admin_hint: str = "rerun with --yes or run:",
) -> RunOutcome:
    """Run ``steps`` in order and return the outcome.

    An admin step runs without asking when ``yes`` is true. Otherwise ``confirm(step, commands)``
    decides; with ``confirm`` None the step is skipped. A ``KeyboardInterrupt`` in ``confirm``
    or in a step cancels the run.
    """
    outcome = RunOutcome()
    notice_shown = False
    unmet: set[str] = set()
    for step in steps:
        if step.check():
            say(f"[ok] {step.name}: already installed")
            continue
        if step.name == "srim" and not notice_shown:
            say(NOTICE)
            notice_shown = True
        if any(dep in unmet for dep in step.requires):
            say(f"[skip] {step.name}: waiting for {', '.join(step.requires)}")
            unmet.add(step.name)
            continue
        commands = step.commands()
        if dry_run:
            say(f"[do] {step.name}: {step.summary}")
            _print_commands(say, commands)
            continue
        if commands and all(c.lstrip().startswith("#") for c in commands):
            say(f"[skip] {step.name}: cannot be installed automatically here:")
            _print_commands(say, commands)
            outcome.admin_skipped.append((step.name, commands))
            unmet.add(step.name)
            continue
        if step.needs_admin and not yes:
            confirmed = False
            if confirm is not None:
                try:
                    confirmed = confirm(step, commands)
                except KeyboardInterrupt:
                    say(f"\n[skip] {step.name}: cancelled")
                    outcome.interrupted = True
                    return outcome
            if not confirmed:
                say(f"[skip] {step.name}: needs admin rights — {admin_hint}")
                _print_commands(say, commands)
                outcome.admin_skipped.append((step.name, commands))
                unmet.add(step.name)
                continue
            say(f"[do] {step.name}: {step.summary}")
        else:
            say(f"[do] {step.name}: {step.summary}")
            _print_commands(say, commands)
        try:
            if step.wants_progress:
                step.run(lambda line: say(line))
            else:
                step.run()
            container.reset_cache()
            where = f" in {srim_dir()}" if step.name == "srim" else ""
            say(f"[ok] {step.name}: installed{where}")
            outcome.installed.append(step.name)
        except INSTALL_ERRORS as exc:
            warn(f"[fail] {step.name}: {exc}")
            outcome.failed = step.name
            break
        except KeyboardInterrupt:
            say(f"\n[skip] {step.name}: cancelled")
            outcome.interrupted = True
            return outcome
    return outcome
