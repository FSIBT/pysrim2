import zipfile
from pathlib import Path

import pytest

import srim
from srim import cli
from srim.installer import skill, steps
from srim.installer.skill import MARKER, SkillInstallError, install_skill, skill_targets


@pytest.fixture
def home(monkeypatch, tmp_path):
    monkeypatch.setattr(Path, "home", lambda: tmp_path / "home")
    return tmp_path / "home"


def test_targets(home, tmp_path):
    cwd = tmp_path / "proj"
    assert skill_targets("claude", "user", cwd) == [home / ".claude/skills/pysrim2"]
    assert skill_targets("claude", "project", cwd) == [cwd / ".claude/skills/pysrim2"]
    assert skill_targets("all", "user", cwd) == [
        home / ".claude/skills/pysrim2",
        home / ".agents/skills/pysrim2",
    ]
    assert skill_targets("all", "project", cwd) == [
        cwd / ".claude/skills/pysrim2",
        home / ".agents/skills/pysrim2",
    ]
    with pytest.raises(ValueError):
        skill_targets("nobody", "user", cwd)


def test_install_copies_skill_and_examples(tmp_path):
    target = tmp_path / "t"
    assert install_skill([target]) == [target]
    text = (target / "SKILL.md").read_text()
    assert text.startswith("---\nname: pysrim2\ndescription:")
    assert (target / "examples" / "boron_in_gold_on_silicon.py").is_file()
    assert (target / MARKER).read_text().strip() == srim.__version__


def test_install_is_idempotent_and_updates_managed_copy(tmp_path):
    target = tmp_path / "t"
    install_skill([target])
    install_skill([target])
    (target / "SKILL.md").write_text("old pysrim2 version")
    install_skill([target])  # marker present: ours to update
    assert (target / "SKILL.md").read_text().startswith("---")


def test_install_refuses_unmanaged_file(tmp_path):
    target = tmp_path / "t"
    target.mkdir()
    (target / "SKILL.md").write_text("my own skill")
    with pytest.raises(SkillInstallError, match="--yes"):
        install_skill([target])
    assert (target / "SKILL.md").read_text() == "my own skill"
    install_skill([target], yes=True)
    assert (target / "SKILL.md").read_text().startswith("---")


def test_identical_unmanaged_copy_is_fine(tmp_path):
    target = tmp_path / "t"
    install_skill([target])
    (target / MARKER).unlink()
    install_skill([target])
    assert (target / MARKER).is_file()


def test_cli_install_skill_project_scope(home, tmp_path, monkeypatch, capsys):
    monkeypatch.chdir(tmp_path)
    assert cli.main(["install", "skill", "--scope", "project"]) == 0
    assert (tmp_path / ".claude/skills/pysrim2/SKILL.md").is_file()
    assert cli.main(["install", "skill", "--scope", "project"]) == 0
    assert "[ok] skill: already installed" in capsys.readouterr().out


def test_cli_install_skill_refuses_then_yes(home, tmp_path, monkeypatch, capsys):
    monkeypatch.chdir(tmp_path)
    target = home / ".claude/skills/pysrim2"
    target.mkdir(parents=True)
    (target / "SKILL.md").write_text("mine")
    assert cli.main(["install", "skill"]) == 1
    assert "[fail] skill" in capsys.readouterr().err
    assert cli.main(["install", "skill", "--yes"]) == 0
    assert (target / MARKER).is_file()


def test_skill_registered():
    assert steps.is_registered("skill")


def _doctor_skill(capsys):
    cli.main(["doctor", "--json"])
    import json

    checks = {c["name"]: c for c in json.loads(capsys.readouterr().out)["checks"]}
    return checks["agent-skill"]


def test_doctor_skill_check(home, tmp_path, monkeypatch, capsys):
    monkeypatch.chdir(tmp_path)
    assert _doctor_skill(capsys)["ok"]
    install_skill(skill_targets("claude", "user", tmp_path))
    check = _doctor_skill(capsys)
    assert check["ok"]
    assert "pysrim2" in check["detail"]
    (home / ".claude/skills/pysrim2/SKILL.md").write_text("stale")
    assert not _doctor_skill(capsys)["ok"]


def test_built_wheel_contains_skill(tmp_path):
    import shutil
    import subprocess

    uv = shutil.which("uv")
    root = Path(__file__).resolve().parents[2]
    if uv is None or not (root / "pyproject.toml").is_file():
        pytest.skip("needs uv and a source checkout")
    result = subprocess.run(
        [uv, "build", "--wheel", "--out-dir", str(tmp_path)],
        cwd=root,
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stderr
    wheel = next(tmp_path.glob("pysrim2-*.whl"))
    names = zipfile.ZipFile(wheel).namelist()
    assert "srim/_skill/SKILL.md" in names
    assert "srim/_skill/examples/boron_in_gold_on_silicon.py" in names
