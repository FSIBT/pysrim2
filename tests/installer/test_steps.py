import pytest

from srim.installer import steps
from srim.installer.steps import ComponentUnavailable, Step, plan
from srim.installer.wine import WineSetupError, WineStatus


def _step(name):
    return Step(name, "s", lambda: False, lambda: [], lambda: None)


@pytest.fixture
def registry(monkeypatch):
    monkeypatch.setattr(steps, "_REGISTRY", {})
    for name in ("srim", "wine", "image", "runtime", "skill"):
        steps.register(name, lambda name=name: _step(name))


def configure(monkeypatch, system, wine=False, runtime=None):
    monkeypatch.setattr(steps, "current_platform", lambda: system)
    monkeypatch.setattr(steps, "container_runtime", lambda: runtime)
    status = WineStatus("/usr/bin/wine" if wine else None, None, None, None, None, False)
    monkeypatch.setattr(steps, "wine_status", lambda: status)


@pytest.mark.parametrize(
    ("system", "wine", "runtime", "expected"),
    [
        ("windows", False, None, ["srim", "skill"]),
        ("linux", True, "docker", ["wine", "srim", "skill"]),
        ("linux", False, None, ["wine", "srim", "skill"]),
        ("linux", False, "podman", ["image", "skill"]),
        ("macos", False, "docker", ["image", "skill"]),
        ("macos", False, None, ["runtime", "image", "skill"]),
    ],
)
def test_default_components(monkeypatch, registry, system, wine, runtime, expected):
    configure(monkeypatch, system, wine, runtime)
    assert steps.default_components() == expected


def test_default_components_skip_unregistered(monkeypatch):
    monkeypatch.setattr(steps, "_REGISTRY", {})
    steps.register("srim", lambda: _step("srim"))
    configure(monkeypatch, "windows")
    assert steps.default_components() == ["srim"]


def test_plan_unregistered_component(monkeypatch):
    monkeypatch.setattr(steps, "_REGISTRY", {})
    with pytest.raises(ComponentUnavailable, match="component image is not available yet"):
        plan(["image"])


def test_plan_accepts_step_lists(monkeypatch):
    monkeypatch.setattr(steps, "_REGISTRY", {})
    steps.register("wine", lambda: [_step("a"), _step("b")])
    assert [s.name for s in plan(["wine"])] == ["a", "b"]


def test_real_wine_and_srim_steps(monkeypatch, tmp_path):
    monkeypatch.setenv("PYSRIM2_HOME", str(tmp_path))
    monkeypatch.setattr(steps, "current_platform", lambda: "linux")
    monkeypatch.setattr(
        steps, "wine_status", lambda: WineStatus(None, None, None, None, None, False)
    )
    monkeypatch.setattr(steps, "distro_wine_commands", lambda: ["sudo apt-get install -y wine"])
    srim_step, packages, prefix = plan(["srim", "wine"])
    assert srim_step.name == "srim"
    assert not srim_step.check()
    assert packages.name == "wine-packages"
    assert packages.needs_admin
    assert packages.commands() == ["sudo apt-get install -y wine"]
    assert not packages.check()
    assert prefix.name == "wine"
    assert not prefix.needs_admin
    assert prefix.requires == ("wine-packages",)


def test_wine_component_not_on_macos(monkeypatch):
    monkeypatch.setattr(steps, "current_platform", lambda: "macos")
    with pytest.raises(ComponentUnavailable, match="only for Linux"):
        plan(["wine"])


def test_wine_packages_run_executes_argv_without_shell(monkeypatch):
    seen = []
    monkeypatch.setattr(
        steps, "distro_wine_commands", lambda: ["sudo apt-get install -y 'a b'", "b"]
    )
    monkeypatch.setattr(steps.subprocess, "run", lambda cmd, **kw: seen.append((cmd, kw)))
    steps._run_wine_packages()
    assert [c for c, _ in seen] == [["sudo", "apt-get", "install", "-y", "a b"], ["b"]]
    assert all("shell" not in kw for _, kw in seen)


def test_wine_packages_stop_after_first_failure(monkeypatch):
    seen = []

    def run(cmd, **kw):
        seen.append(cmd)
        raise steps.subprocess.CalledProcessError(1, cmd)

    monkeypatch.setattr(steps, "distro_wine_commands", lambda: ["a", "b"])
    monkeypatch.setattr(steps.subprocess, "run", run)
    with pytest.raises(WineSetupError):
        steps._run_wine_packages()
    assert seen == [["a"]]


def test_wine_packages_unknown_distro(monkeypatch):
    monkeypatch.setattr(steps, "distro_wine_commands", lambda: [])
    called = []
    monkeypatch.setattr(steps.subprocess, "run", lambda *a, **k: called.append(a))
    with pytest.raises(WineSetupError, match="unknown distribution"):
        steps._run_wine_packages()
    assert called == []


def test_wine_packages_refuse_comment_lines(monkeypatch):
    monkeypatch.setattr(steps, "distro_wine_commands", lambda: ["# note"])
    with pytest.raises(WineSetupError, match="refusing"):
        steps._run_wine_packages()
