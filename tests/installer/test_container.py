import json
import subprocess

import pytest

import srim
from srim.installer import container, steps


class FakeRun:
    """Stands in for subprocess.run; ``results`` maps argv tuples to return codes."""

    def __init__(self, results=None, default=0):
        self.results = results or {}
        self.default = default
        self.calls = []

    def __call__(self, cmd, **kwargs):
        self.calls.append(list(cmd))
        code = self.results.get(tuple(cmd), self.default)
        return subprocess.CompletedProcess(cmd, code, "", "")


def installed(monkeypatch, *names):
    monkeypatch.setattr(
        container.shutil, "which", lambda n: f"/usr/bin/{n}" if n in names else None
    )


def test_find_runtime_prefers_podman(monkeypatch):
    installed(monkeypatch, "docker", "podman")
    monkeypatch.setattr(container.subprocess, "run", FakeRun())
    assert container.find_runtime() == "podman"


def test_find_runtime_falls_back_to_docker(monkeypatch):
    installed(monkeypatch, "docker", "podman")
    run = FakeRun({("podman", "info"): 125})
    monkeypatch.setattr(container.subprocess, "run", run)
    assert container.find_runtime() == "docker"


def test_find_runtime_none_installed(monkeypatch):
    installed(monkeypatch)
    run = FakeRun()
    monkeypatch.setattr(container.subprocess, "run", run)
    assert container.find_runtime() is None
    assert run.calls == []


def test_daemon_not_running_is_not_usable(monkeypatch):
    installed(monkeypatch, "docker")
    monkeypatch.setattr(container.subprocess, "run", FakeRun(default=1))
    assert container.find_runtime() is None
    assert container.installed_runtimes() == ["docker"]


def test_info_timeout_is_not_usable(monkeypatch):
    installed(monkeypatch, "docker")

    def hang(cmd, **kw):
        raise subprocess.TimeoutExpired(cmd, 1)

    monkeypatch.setattr(container.subprocess, "run", hang)
    assert container.find_runtime() is None


def test_start_hint(monkeypatch):
    monkeypatch.setattr(steps, "current_platform", lambda: "macos")
    assert container.start_hint("podman") == "podman machine start"
    assert "Docker Desktop" in container.start_hint("docker")
    monkeypatch.setattr(steps, "current_platform", lambda: "linux")
    assert "systemctl" in container.start_hint("docker")


@pytest.mark.parametrize(
    ("version", "tag"),
    [
        ("0.7.0", "0.7.0"),
        ("1.2.3", "1.2.3"),
        ("0.7.0.dev1", "main"),
        ("0.7.0rc1", "main"),
        ("0.7.0a2", "main"),
        ("0.7.0b1", "main"),
        ("0.7.1.dev3+g1234", "main"),
    ],
)
def test_image_tag(monkeypatch, version, tag):
    monkeypatch.setattr(srim, "__version__", version)
    assert container.image_tag() == tag


def test_pull_image_command(monkeypatch):
    run = FakeRun()
    monkeypatch.setattr(container.subprocess, "run", run)
    container.pull_image("podman", "0.7.0")
    assert run.calls == [
        ["podman", "pull", "--platform", "linux/amd64", "ghcr.io/fsibt/pysrim2:0.7.0"]
    ]


def test_pull_failure_explains(monkeypatch):
    monkeypatch.setattr(container.subprocess, "run", FakeRun(default=1))
    with pytest.raises(container.ContainerError) as err:
        container.pull_image("docker", "main")
    message = str(err.value)
    assert "may not be published yet" in message
    assert "docker pull --platform linux/amd64 ghcr.io/fsibt/pysrim2:main" in message


def test_pull_runtime_missing(monkeypatch):
    def missing(cmd, **kw):
        raise FileNotFoundError(cmd[0])

    monkeypatch.setattr(container.subprocess, "run", missing)
    with pytest.raises(container.ContainerError, match="docker pull"):
        container.pull_image("docker", "main")


@pytest.mark.parametrize(
    ("system", "os_release", "expected"),
    [
        ("macos", "", ["brew install podman", "podman machine init", "podman machine start"]),
        ("linux", "ID=ubuntu\nID_LIKE=debian\n", ["sudo apt-get install -y podman"]),
        ("linux", "ID=fedora\n", ["sudo dnf install -y podman"]),
        ("windows", "", ["winget install -e --id RedHat.Podman"]),
    ],
)
def test_runtime_install_commands(monkeypatch, tmp_path, system, os_release, expected):
    monkeypatch.setattr(steps, "current_platform", lambda: system)
    path = tmp_path / "os-release"
    path.write_text(os_release)
    assert container.runtime_install_commands(path) == expected


def test_runtime_install_commands_unknown_distro(monkeypatch, tmp_path):
    monkeypatch.setattr(steps, "current_platform", lambda: "linux")
    path = tmp_path / "os-release"
    path.write_text("ID=plan9\n")
    [hint] = container.runtime_install_commands(path)
    assert hint.startswith("#")
    assert container.PODMAN_DOCS in hint


def test_run_hint_podman_relabels_and_has_no_seccomp_flag():
    hint = container.run_hint("podman", "0.7.0")
    assert hint == (
        'podman run --rm -v "$PWD:/data:Z" ghcr.io/fsibt/pysrim2:0.7.0 python /data/run.py'
    )
    assert "seccomp" not in hint


def test_run_hint_docker_uses_the_profile(monkeypatch, tmp_path):
    monkeypatch.setenv("PYSRIM2_HOME", str(tmp_path))
    hint = container.run_hint("docker", "0.7.0")
    profile = tmp_path / "seccomp-docker.json"
    assert f'--security-opt seccomp="{profile}"' in hint
    assert hint.endswith('-v "$PWD:/data" ghcr.io/fsibt/pysrim2:0.7.0 python /data/run.py')
    assert "unconfined" not in hint
    assert profile.is_file()


def test_seccomp_profile_is_valid_and_copied(monkeypatch, tmp_path):
    monkeypatch.setenv("PYSRIM2_HOME", str(tmp_path / "home"))
    path = container.seccomp_profile_path()
    assert path == tmp_path / "home" / "seccomp-docker.json"
    profile = json.loads(path.read_text())
    assert profile["defaultAction"] and profile["syscalls"]
    assert "moby/profiles" in profile["_comment"]


def test_pull_writes_profile_only_for_docker(monkeypatch, tmp_path, capsys):
    monkeypatch.setenv("PYSRIM2_HOME", str(tmp_path))
    monkeypatch.setattr(container, "pull_image", lambda runtime, tag: None)
    [image] = steps.plan(["image"])
    monkeypatch.setattr(container, "find_runtime", lambda: "podman")
    image.run()
    assert not (tmp_path / "seccomp-docker.json").exists()
    assert ":/data:Z" in capsys.readouterr().out
    monkeypatch.setattr(container, "find_runtime", lambda: "docker")
    image.run()
    assert (tmp_path / "seccomp-docker.json").is_file()


def test_run_runtime_commands_without_shell(monkeypatch):
    monkeypatch.setattr(
        container,
        "runtime_install_commands",
        lambda: ["brew install podman", "podman machine init"],
    )
    run = FakeRun()
    monkeypatch.setattr(steps.subprocess, "run", run)
    container.install_runtime()
    assert ["brew", "install", "podman"] in run.calls
    assert ["podman", "machine", "init"] in run.calls


def test_run_runtime_refuses_comment(monkeypatch):
    monkeypatch.setattr(container, "runtime_install_commands", lambda: ["# see docs"])
    with pytest.raises(container.ContainerError):
        container.install_runtime()


def test_steps_registered():
    assert steps.is_registered("runtime")
    assert steps.is_registered("image")
    [runtime] = [s for s in steps.plan(["runtime"])]
    assert runtime.name == "runtime"
    assert runtime.needs_admin
    [image] = steps.plan(["image"])
    assert image.requires == ("runtime",)


def test_image_step_check(monkeypatch):
    monkeypatch.setattr(srim, "__version__", "0.7.0")
    [image] = steps.plan(["image"])
    monkeypatch.setattr(container, "find_runtime", lambda: None)
    assert not image.check()
    monkeypatch.setattr(container, "find_runtime", lambda: "podman")
    run = FakeRun({("podman", "image", "inspect", "ghcr.io/fsibt/pysrim2:0.7.0"): 1})
    monkeypatch.setattr(container.subprocess, "run", run)
    assert not image.check()
    monkeypatch.setattr(container.subprocess, "run", FakeRun())
    assert image.check()
    assert image.commands() == ["podman pull --platform linux/amd64 ghcr.io/fsibt/pysrim2:0.7.0"]


def test_image_step_run_without_runtime(monkeypatch):
    [image] = steps.plan(["image"])
    monkeypatch.setattr(container, "find_runtime", lambda: None)
    with pytest.raises(container.ContainerError, match="pysrim2 install runtime"):
        image.run()


@pytest.fixture(autouse=True)
def _fresh_cache():
    container.reset_cache()
    yield
    container.reset_cache()


def test_timeouts_passed_and_cached(monkeypatch):
    installed(monkeypatch, "docker")
    seen = []

    def run(cmd, **kw):
        seen.append(kw)
        return subprocess.CompletedProcess(cmd, 0, "", "")

    monkeypatch.setattr(container.subprocess, "run", run)
    assert container.find_runtime() == "docker"
    assert container.find_runtime() == "docker"
    assert len(seen) == 1
    assert seen[0]["timeout"] == 10
    container.image_present("docker", "main")
    assert seen[1]["timeout"] == 10


class Machine:
    def __init__(self, listing):
        self.listing = listing
        self.ran = []

    def __call__(self, cmd, **kw):
        if cmd[:3] == ["podman", "machine", "list"]:
            return subprocess.CompletedProcess(cmd, 0, self.listing, "")
        self.ran.append(list(cmd))
        return subprocess.CompletedProcess(cmd, 0, "", "")


def macos_install(monkeypatch, listing):
    monkeypatch.setattr(steps, "current_platform", lambda: "macos")
    machine = Machine(listing)
    monkeypatch.setattr(container.subprocess, "run", machine)
    monkeypatch.setattr(steps.subprocess, "run", machine)
    container.install_runtime()
    return machine.ran


def test_install_runtime_existing_stopped_machine_only_starts(monkeypatch):
    ran = macos_install(monkeypatch, "podman-machine-default* false\n")
    assert ran == [["brew", "install", "podman"], ["podman", "machine", "start"]]


def test_install_runtime_running_machine_does_nothing_more(monkeypatch):
    ran = macos_install(monkeypatch, "podman-machine-default* true\n")
    assert ran == [["brew", "install", "podman"]]


def test_install_runtime_no_machine_inits_and_starts(monkeypatch):
    ran = macos_install(monkeypatch, "")
    assert ["podman", "machine", "init"] in ran
    assert ["podman", "machine", "start"] in ran


def test_windows_prints_hint(monkeypatch, capsys):
    monkeypatch.setattr(steps, "current_platform", lambda: "windows")
    monkeypatch.setattr(steps.subprocess, "run", FakeRun())
    container.install_runtime()
    assert "podman machine init" in capsys.readouterr().out


def test_runtime_summary_mentions_stopped_docker(monkeypatch):
    monkeypatch.setattr(container, "installed_runtimes", lambda: ["docker"])
    assert "installed but not running" in steps.plan(["runtime"])[0].summary
