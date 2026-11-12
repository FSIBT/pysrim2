import subprocess

import pytest

from srim import cli
from srim.installer import app as app_install
from srim.installer import steps
from srim.installer.app import app_command, app_supported, install_kind, installed_extras


@pytest.fixture(autouse=True)
def _supported_python(monkeypatch):
    """The install tests run on every Python; the 3.15 limit has tests of its own."""
    monkeypatch.setattr(app_install, "app_supported", lambda: True)


def test_a_uv_tool_install_keeps_its_extras(tmp_path):
    (tmp_path / "uv-receipt.toml").write_text(
        '[tool]\nrequirements = [{ name = "pysrim2", extras = ["tui"] }]\n'
    )
    assert install_kind(tmp_path) == "uv-tool"
    assert installed_extras(tmp_path / "uv-receipt.toml") == {"tui"}
    assert app_command("uv-tool", "0.12.0", {"tui"}) == [
        "uv",
        "tool",
        "install",
        "--reinstall",
        "pysrim2[app,tui]==0.12.0",
    ]


def test_installed_extras_is_empty_when_the_receipt_is_missing_or_broken(tmp_path):
    assert installed_extras(tmp_path / "missing.toml") == set()
    bad = tmp_path / "uv-receipt.toml"
    bad.write_text("[tool\n")
    assert installed_extras(bad) == set()


def test_pip_uses_the_running_python_and_pins_the_version():
    assert app_command("pip", "0.12.0", python="/v/bin/python", uv=None) == [
        "/v/bin/python",
        "-m",
        "pip",
        "install",
        "pysrim2[app]==0.12.0",
    ]
    assert app_command("pip", "0.12.0", python="/v/bin/python", uv="/bin/uv") == [
        "/bin/uv",
        "pip",
        "install",
        "--python",
        "/v/bin/python",
        "pysrim2[app]==0.12.0",
    ]


def test_uv_is_looked_up_when_the_command_is_built(monkeypatch):
    monkeypatch.setattr(app_install.shutil, "which", lambda name: "/later/uv")
    assert app_command("pip", "0.12.0", python="/v/bin/python")[0] == "/later/uv"


def test_editable_installs_have_no_command_of_their_own():
    assert app_command("editable", "0.12.0") == []


def test_install_kind_is_editable_for_a_checkout(tmp_path, monkeypatch):
    monkeypatch.setattr(app_install, "_direct_url", lambda: {"dir_info": {"editable": True}})
    assert install_kind(tmp_path) == "editable"


def test_install_kind_is_pip_otherwise(tmp_path, monkeypatch):
    monkeypatch.setattr(app_install, "_direct_url", lambda: None)
    assert install_kind(tmp_path) == "pip"


def test_a_checkout_is_told_to_use_uv_sync(monkeypatch, capsys):
    monkeypatch.setattr(app_install, "install_kind", lambda *a, **k: "editable")
    monkeypatch.setattr(app_install, "app_available", lambda: False)
    ran = []
    monkeypatch.setattr(subprocess, "run", lambda *a, **k: ran.append(a))
    assert cli.main(["install", "app"]) == 2
    assert "uv sync --extra app" in capsys.readouterr().out
    assert ran == []


def test_a_failed_install_names_the_command_to_run_by_hand(monkeypatch, capsys):
    monkeypatch.setattr(app_install, "install_kind", lambda *a, **k: "uv-tool")
    monkeypatch.setattr(app_install, "app_available", lambda: False)
    monkeypatch.setattr(app_install, "installed_extras", lambda receipt: set())

    def fail(cmd, **kwargs):
        raise subprocess.CalledProcessError(1, cmd)

    monkeypatch.setattr(subprocess, "run", fail)
    assert cli.main(["install", "app"]) == 1
    err = capsys.readouterr().err
    assert "[fail] app:" in err
    assert "run it yourself: uv tool install --reinstall" in err


@pytest.mark.parametrize("system", ["windows", "linux", "macos"])
def test_app_is_not_a_default_component(monkeypatch, system):
    monkeypatch.setattr(steps, "current_platform", lambda: system)
    monkeypatch.setattr(steps, "container_runtime", lambda: None)
    monkeypatch.setattr(steps, "wine_status", lambda: _no_wine())
    assert "app" in steps.COMPONENTS
    assert "app" not in steps.default_components()


def _no_wine():
    from srim.installer.wine import WineStatus

    return WineStatus(None, None, None, None, None, False)


def test_pysrim2_app_without_the_extra_says_how_to_install(monkeypatch, capsys):
    monkeypatch.setattr(app_install, "app_available", lambda: False)
    assert cli.main(["app"]) == 1
    assert "run: pysrim2 install app" in capsys.readouterr().err


def test_the_app_needs_python_3_14_or_older():
    assert app_supported((3, 11))
    assert app_supported((3, 14, 2))
    assert not app_supported((3, 15))
    assert not app_supported((3, 16, 0))


def test_install_app_on_python_3_15_says_why_and_installs_nothing(monkeypatch, capsys):
    monkeypatch.setattr(app_install, "app_supported", lambda: False)
    ran = []
    monkeypatch.setattr(subprocess, "run", lambda *a, **k: ran.append(a))
    assert cli.main(["install", "app"]) == 1
    assert "needs Python 3.14 or older" in capsys.readouterr().err
    assert ran == []


def test_pysrim2_app_on_python_3_15_says_why(monkeypatch, capsys):
    monkeypatch.setattr(app_install, "app_supported", lambda: False)
    assert cli.main(["app"]) == 1
    assert "needs Python 3.14 or older" in capsys.readouterr().err
