import json

import pytest

import srim
from srim import cli
from srim.installer import steps
from srim.installer.steps import Step
from srim.installer.wine import WineStatus


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
def rec(monkeypatch):
    monkeypatch.setattr(steps, "_REGISTRY", {})
    r = Recorder()
    steps.register("srim", lambda: r.step("srim"))
    steps.register(
        "wine",
        lambda: [r.step("wine-packages", admin=True), r.step("wine", requires=("wine-packages",))],
    )
    monkeypatch.setattr(steps, "current_platform", lambda: "linux")
    monkeypatch.setattr(steps, "container_runtime", lambda: None)
    monkeypatch.setattr(
        steps, "wine_status", lambda: WineStatus(None, None, None, None, None, False)
    )
    return r


def test_version(capsys):
    with pytest.raises(SystemExit) as exc:
        cli.main(["--version"])
    assert exc.value.code == 0
    assert srim.__version__ in capsys.readouterr().out


def test_dry_run_lists_commands_and_runs_nothing(rec, capsys):
    assert cli.main(["install", "wine", "srim", "--dry-run"]) == 0
    out = capsys.readouterr().out
    assert rec.ran == []
    assert "[do] wine-packages: summary of wine-packages" in out
    assert "    cmd one" in out
    assert "[do] srim:" in out
    assert out.count(srim.installer.srim_install.NOTICE) == 1


def test_rerun_prints_only_ok_lines(rec, capsys):
    rec.done.update({"srim", "wine-packages", "wine"})
    assert cli.main(["install", "wine", "srim"]) == 0
    lines = capsys.readouterr().out.splitlines()
    assert lines == [
        "[ok] wine-packages: already installed",
        "[ok] wine: already installed",
        "[ok] srim: already installed",
    ]


def test_admin_step_skipped_without_yes(rec, capsys):
    assert cli.main(["install", "wine", "srim"]) == 2
    out = capsys.readouterr().out
    assert "[skip] wine-packages: needs admin rights — rerun with --yes or run:" in out
    assert "    cmd two" in out
    assert "[skip] wine: waiting for wine-packages" in out
    assert rec.ran == ["srim"]


def test_yes_runs_admin_step(rec, capsys):
    assert cli.main(["install", "wine", "--yes"]) == 0
    assert rec.ran == ["wine-packages", "wine"]


def test_failure_exits_1(rec, capsys):
    from srim.installer.srim_install import SrimInstallError

    def boom():
        raise SrimInstallError("no network")

    steps.register("srim", lambda: Step("srim", "s", lambda: False, lambda: [], boom))
    assert cli.main(["install", "srim"]) == 1
    assert "[fail] srim: no network" in capsys.readouterr().err


def test_unregistered_component(rec, capsys):
    assert cli.main(["install", "image"]) == 1
    assert "component image is not available yet" in capsys.readouterr().err


def test_default_set_empty_on_macos_explains(rec, monkeypatch, capsys):
    monkeypatch.setattr(steps, "current_platform", lambda: "macos")
    assert cli.main(["install", "--dry-run"]) == 0
    assert "Native SRIM needs Windows or Linux" in capsys.readouterr().out


def test_doctor_json_schema(monkeypatch, tmp_path, capsys):
    monkeypatch.setenv("PYSRIM2_SRIM_DIR", str(tmp_path / "srim"))
    assert cli.main(["doctor", "--json"]) == 1
    data = json.loads(capsys.readouterr().out)
    assert list(data) == ["checks"]
    for check in data["checks"]:
        assert set(check) == {"name", "ok", "detail", "fix"}
        assert isinstance(check["ok"], bool)
    by_name = {c["name"]: c for c in data["checks"]}
    assert by_name["python"]["ok"]
    assert not by_name["trim-exe"]["ok"]
    assert by_name["trim-exe"]["fix"]


def test_doctor_all_ok(monkeypatch, tmp_path, capsys):
    root = tmp_path / "srim"
    (root / "Data").mkdir(parents=True)
    (root / "TRIM.exe").write_text("x")
    (root / "Data" / "LEGAL.rtf").write_text("x")
    monkeypatch.setenv("PYSRIM2_SRIM_DIR", str(root))
    monkeypatch.setattr(steps, "current_platform", lambda: "windows")
    assert cli.main(["doctor"]) == 0
    assert "[!!]" not in capsys.readouterr().out


@pytest.mark.parametrize(
    ("answer", "code", "ran"), [("y", 0, ["wine-packages", "wine"]), ("n", 2, [])]
)
def test_interactive_confirmation(rec, monkeypatch, capsys, answer, code, ran):
    monkeypatch.setattr(cli.sys.stdin, "isatty", lambda: True)
    monkeypatch.setattr("builtins.input", lambda prompt="": answer)
    assert cli.main(["install", "wine"]) == code
    out = capsys.readouterr().out
    assert "[ask] wine-packages: needs admin rights — run these commands? [y/N]" in out
    assert ("[skip] wine-packages" in out) == (answer == "n")
    assert rec.ran == ran


def test_subprocess_error_is_reported(rec, capsys):
    import subprocess

    def boom():
        raise subprocess.TimeoutExpired("cmd", 1)

    steps.register("srim", lambda: Step("srim", "s", lambda: False, lambda: [], boom))
    assert cli.main(["install", "srim"]) == 1
    assert "[fail] srim:" in capsys.readouterr().err


def _interactive(monkeypatch, answer):
    monkeypatch.setattr(cli.sys.stdin, "isatty", lambda: True)
    monkeypatch.setattr("builtins.input", answer)


def test_do_line_printed_after_yes_answer(rec, monkeypatch, capsys):
    _interactive(monkeypatch, lambda prompt="": "y")
    assert cli.main(["install", "wine"]) == 0
    assert "[do] wine-packages: summary of wine-packages" in capsys.readouterr().out


def test_keyboard_interrupt_at_prompt_cancels(rec, monkeypatch, capsys):
    def interrupt(prompt=""):
        raise KeyboardInterrupt

    _interactive(monkeypatch, interrupt)
    assert cli.main(["install", "wine"]) == 2
    captured = capsys.readouterr()
    assert "[skip] wine-packages: cancelled" in captured.out
    assert "Traceback" not in captured.err
    assert rec.ran == []


def test_value_error_is_an_install_error(rec, capsys):
    def boom():
        raise ValueError("No closing quotation")

    steps.register("srim", lambda: Step("srim", "s", lambda: False, lambda: [], boom))
    assert cli.main(["install", "srim"]) == 1
    assert "[fail] srim: No closing quotation" in capsys.readouterr().err


def test_comment_only_commands_are_not_offered(rec, monkeypatch, capsys):
    hint = "# unknown distribution: install wine, see https://example.org"
    steps.register(
        "srim",
        lambda: Step(
            "srim",
            "s",
            lambda: False,
            lambda: [hint],
            lambda: rec.ran.append("srim"),
            needs_admin=True,
        ),
    )
    _interactive(monkeypatch, lambda prompt="": pytest.fail("must not prompt"))
    assert cli.main(["install", "srim", "--yes"]) == 2
    out = capsys.readouterr().out
    assert "[skip] srim:" in out
    assert "https://example.org" in out
    assert "[ask]" not in out
    assert rec.ran == []


def _doctor_names(monkeypatch, tmp_path, capsys):
    monkeypatch.setenv("PYSRIM2_SRIM_DIR", str(tmp_path / "srim"))
    monkeypatch.setattr(steps, "current_platform", lambda: "macos")
    cli.main(["doctor", "--json"])
    return {c["name"]: c for c in json.loads(capsys.readouterr().out)["checks"]}


def test_doctor_runtime_and_image_present(monkeypatch, tmp_path, capsys):
    monkeypatch.setattr(cli.container, "find_runtime", lambda: "podman")
    monkeypatch.setattr(cli.container, "image_present", lambda rt, tag: True)
    checks = _doctor_names(monkeypatch, tmp_path, capsys)
    assert checks["container-runtime"]["ok"]
    assert checks["image"]["ok"]


def test_doctor_image_missing_shows_pull_command(monkeypatch, tmp_path, capsys):
    monkeypatch.setattr(cli.container, "find_runtime", lambda: "podman")
    monkeypatch.setattr(cli.container, "image_present", lambda rt, tag: False)
    checks = _doctor_names(monkeypatch, tmp_path, capsys)
    assert not checks["image"]["ok"]
    assert "podman pull --platform linux/amd64 ghcr.io/fsibt/pysrim2:" in checks["image"]["fix"]


def test_doctor_runtime_installed_but_stopped(monkeypatch, tmp_path, capsys):
    monkeypatch.setattr(cli.container, "find_runtime", lambda: None)
    monkeypatch.setattr(cli.container, "installed_runtimes", lambda: ["podman"])
    checks = _doctor_names(monkeypatch, tmp_path, capsys)
    assert "installed but not running" in checks["container-runtime"]["detail"]
    assert "podman machine start" in checks["container-runtime"]["fix"]
    assert "image" not in checks


def test_doctor_no_runtime(monkeypatch, tmp_path, capsys):
    monkeypatch.setattr(cli.container, "find_runtime", lambda: None)
    monkeypatch.setattr(cli.container, "installed_runtimes", lambda: [])
    checks = _doctor_names(monkeypatch, tmp_path, capsys)
    assert checks["container-runtime"]["fix"] == "run: pysrim2 install runtime"


def test_image_waits_for_runtime_not_failed(rec, monkeypatch, capsys):
    from srim.installer import container

    monkeypatch.setattr(container, "find_runtime", lambda: None)
    steps.register(
        "runtime",
        lambda: Step("runtime", "r", lambda: False, lambda: ["x"], lambda: None, needs_admin=True),
    )
    steps.register(
        "image",
        lambda: Step(
            "image",
            "i",
            lambda: False,
            lambda: ["y"],
            lambda: rec.ran.append("image"),
            requires=("runtime",),
        ),
    )
    assert cli.main(["install", "runtime", "image"]) == 2
    out = capsys.readouterr().out
    assert "[skip] image: waiting for runtime" in out
    assert rec.ran == []
