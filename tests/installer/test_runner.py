import subprocess

import pytest

from srim.installer import container, runner
from srim.installer.runner import INSTALL_ERRORS, RunOutcome, run_steps
from srim.installer.srim_install import NOTICE
from srim.installer.steps import Step


class Recorder:
    def __init__(self):
        self.ran = []
        self.done = set()

    def step(self, name, admin=False, requires=(), cmds=("cmd one", "cmd two")):
        def run():
            self.ran.append(name)
            self.done.add(name)

        return Step(
            name,
            f"summary of {name}",
            lambda: name in self.done,
            lambda: list(cmds),
            run,
            needs_admin=admin,
            requires=requires,
        )


@pytest.fixture
def rec():
    return Recorder()


def test_lines_go_to_say_and_the_outcome_lists_what_happened(rec):
    lines = []
    out = run_steps([rec.step("srim"), rec.step("pkgs", admin=True)], say=lines.append)
    assert out.installed == ["srim"]
    assert out.admin_skipped == [("pkgs", ["cmd one", "cmd two"])]
    assert out.exit_code == 2
    assert rec.ran == ["srim"]
    assert "[skip] pkgs: needs admin rights — rerun with --yes or run:" in lines
    assert "    cmd one" in lines and "    cmd two" in lines


def test_admin_hint_replaces_the_cli_wording(rec):
    lines = []
    run_steps(
        [rec.step("pkgs", admin=True)], say=lines.append, admin_hint="run them in a terminal:"
    )
    assert "[skip] pkgs: needs admin rights — run them in a terminal:" in lines


def test_a_failing_step_stops_the_run(rec):
    warnings = []

    def boom():
        raise OSError("boom")

    failing = Step("first", "s", lambda: False, lambda: [], boom)
    out = run_steps([failing, rec.step("second")], warn=warnings.append)
    assert out.failed == "first"
    assert out.exit_code == 1
    assert warnings == ["[fail] first: boom"]
    assert rec.ran == []


def test_confirm_none_never_runs_an_admin_step(rec, monkeypatch):
    def no_input(prompt=""):
        raise AssertionError("stdin must not be read")

    monkeypatch.setattr("builtins.input", no_input)
    out = run_steps([rec.step("pkgs", admin=True)], say=lambda line: None, confirm=None)
    assert rec.ran == []
    assert out.admin_skipped == [("pkgs", ["cmd one", "cmd two"])]


def test_confirm_answer_decides_an_admin_step(rec):
    asked = []

    def confirm(step, commands):
        asked.append((step.name, commands))
        return True

    out = run_steps([rec.step("pkgs", admin=True)], say=lambda line: None, confirm=confirm)
    assert asked == [("pkgs", ["cmd one", "cmd two"])]
    assert rec.ran == ["pkgs"]
    assert out.installed == ["pkgs"] and out.exit_code == 0


def test_yes_runs_an_admin_step_without_asking(rec):
    def confirm(step, commands):
        raise AssertionError("confirm must not be called with yes")

    lines = []
    out = run_steps([rec.step("pkgs", admin=True)], say=lines.append, yes=True, confirm=confirm)
    assert rec.ran == ["pkgs"] and out.exit_code == 0
    assert "[do] pkgs: summary of pkgs" in lines


def test_keyboard_interrupt_in_confirm_cancels(rec):
    def confirm(step, commands):
        raise KeyboardInterrupt

    lines = []
    out = run_steps(
        [rec.step("pkgs", admin=True), rec.step("after")], say=lines.append, confirm=confirm
    )
    assert out.interrupted is True
    assert out.exit_code == 130
    assert "\n[skip] pkgs: cancelled" in lines
    assert rec.ran == []


def test_keyboard_interrupt_in_a_step_cancels(rec):
    def interrupt():
        raise KeyboardInterrupt

    lines = []
    failing = Step("srim", "s", lambda: False, lambda: [], interrupt)
    out = run_steps([failing, rec.step("after")], say=lines.append)
    assert out.interrupted is True and out.exit_code == 130
    assert "\n[skip] srim: cancelled" in lines
    assert rec.ran == []


def test_dry_run_prints_every_step_and_runs_nothing(rec):
    lines = []
    out = run_steps(
        [rec.step("srim"), rec.step("pkgs", admin=True)], say=lines.append, dry_run=True
    )
    assert rec.ran == []
    assert "[do] srim: summary of srim" in lines
    assert "[do] pkgs: summary of pkgs" in lines
    assert out.exit_code == 0 and out.installed == []


def test_notice_is_said_once_before_srim(rec):
    lines = []
    run_steps([rec.step("srim"), rec.step("srim")], say=lines.append, dry_run=True)
    assert lines.count(NOTICE) == 1


def test_done_step_and_unmet_requirement(rec):
    lines = []
    rec.done.add("srim")
    out = run_steps(
        [
            rec.step("srim"),
            rec.step("pkgs", admin=True),
            rec.step("wine", requires=("pkgs",)),
        ],
        say=lines.append,
    )
    assert "[ok] srim: already installed" in lines
    assert "[skip] wine: waiting for pkgs" in lines
    assert out.admin_skipped == [("pkgs", ["cmd one", "cmd two"])]
    assert rec.ran == []


def test_comment_only_commands_are_listed_as_admin_skipped(rec):
    lines = []
    out = run_steps([rec.step("pkgs", cmds=("# install it yourself",))], say=lines.append)
    assert "[skip] pkgs: cannot be installed automatically here:" in lines
    assert out.admin_skipped == [("pkgs", ["# install it yourself"])]
    assert out.exit_code == 2


def test_srim_success_line_names_the_directory(rec, monkeypatch):
    monkeypatch.setattr(runner, "srim_dir", lambda: "/data/srim")
    lines = []
    out = run_steps([rec.step("srim")], say=lines.append)
    assert "[ok] srim: installed in /data/srim" in lines
    assert out.installed == ["srim"]


def test_cache_is_reset_after_each_step(rec, monkeypatch):
    resets = []
    monkeypatch.setattr(container, "reset_cache", lambda: resets.append(1))
    run_steps([rec.step("srim"), rec.step("other")], say=lambda line: None)
    assert len(resets) == 2


def test_progress_lines_go_to_say(rec):
    lines = []

    def run(progress):
        progress("50%")

    step = Step("srim", "s", lambda: False, lambda: [], run, wants_progress=True)
    run_steps([step], say=lines.append)
    assert "50%" in lines


@pytest.mark.parametrize(
    ("outcome", "code"),
    [
        (RunOutcome(), 0),
        (RunOutcome(admin_skipped=[("p", [])]), 2),
        (RunOutcome(failed="x"), 1),
        (RunOutcome(failed="x", admin_skipped=[("p", [])]), 1),
        (RunOutcome(interrupted=True, admin_skipped=[("p", [])]), 130),
    ],
)
def test_exit_code(outcome, code):
    assert outcome.exit_code == code


def test_install_errors_members():
    assert subprocess.SubprocessError in INSTALL_ERRORS
    assert OSError in INSTALL_ERRORS and ValueError in INSTALL_ERRORS
