"""Find a container runtime and pull the pysrim2 image.

Podman is preferred; Docker is used when it is already there. pysrim2 never
installs Docker Desktop. The image is ``linux/amd64`` only.
"""

import re
import shutil
import subprocess
from pathlib import Path

import srim
from srim.installer import steps
from srim.installer.steps import Step, register
from srim.installer.wine import distro_ids

IMAGE = "ghcr.io/fsibt/pysrim2"
PLATFORM = "linux/amd64"
RUNTIMES = ("podman", "docker")
PODMAN_DOCS = "https://podman.io/docs/installation"
INFO_TIMEOUT_SECONDS = 60
INSPECT_TIMEOUT_SECONDS = 60
PULL_TIMEOUT_SECONDS = 1800
_RELEASE = re.compile(r"\d+(\.\d+)*")


class ContainerError(RuntimeError):
    """A container runtime or image step failed."""


def installed_runtimes() -> list[str]:
    """Return the runtimes whose command is on PATH, usable or not."""
    return [name for name in RUNTIMES if shutil.which(name)]


def _usable(name: str) -> bool:
    try:
        result = subprocess.run(
            [name, "info"],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            timeout=INFO_TIMEOUT_SECONDS,
        )
    except (OSError, subprocess.SubprocessError):
        return False
    return result.returncode == 0


def find_runtime() -> str | None:
    """Return ``podman`` or ``docker`` if it is installed and answers ``info``."""
    for name in installed_runtimes():
        if _usable(name):
            return name
    return None


def start_hint(runtime: str) -> str:
    """Say how to start ``runtime`` when it is installed but not answering."""
    system = steps.current_platform()
    if runtime == "podman":
        if system == "linux":
            return "podman info fails; check your podman installation"
        return "podman machine start"
    if system == "linux":
        return "sudo systemctl start docker"
    return "start Docker Desktop"


def image_tag() -> str:
    """Return the image tag for this pysrim2: its version if a release, else ``main``."""
    version = srim.__version__
    return version if _RELEASE.fullmatch(version) else "main"


def image_ref(tag: str) -> str:
    return f"{IMAGE}:{tag}"


def pull_command(runtime: str, tag: str) -> list[str]:
    return [runtime, "pull", "--platform", PLATFORM, image_ref(tag)]


def pull_image(runtime: str, tag: str) -> None:
    command = pull_command(runtime, tag)
    shown = " ".join(command)
    try:
        result = subprocess.run(command, timeout=PULL_TIMEOUT_SECONDS)
    except (OSError, subprocess.SubprocessError) as exc:
        raise ContainerError(f"could not run {shown}: {exc}") from exc
    if result.returncode != 0:
        raise ContainerError(
            f"pulling {image_ref(tag)} failed. The image may not be published yet "
            f"(it appears with the next release). Command: {shown}"
        )


def image_present(runtime: str, tag: str) -> bool:
    try:
        result = subprocess.run(
            [runtime, "image", "inspect", image_ref(tag)],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            timeout=INSPECT_TIMEOUT_SECONDS,
        )
    except (OSError, subprocess.SubprocessError):
        return False
    return result.returncode == 0


def runtime_install_commands(os_release: Path = Path("/etc/os-release")) -> list[str]:
    """Return the commands that install Podman here (a ``#`` line if we do not know how)."""
    system = steps.current_platform()
    if system == "macos":
        return ["brew install podman", "podman machine init", "podman machine start"]
    if system == "windows":
        return ["winget install -e --id RedHat.Podman"]
    ids = distro_ids(os_release)
    if "debian" in ids or "ubuntu" in ids:
        return ["sudo apt-get install -y podman"]
    if "fedora" in ids:
        return ["sudo dnf install -y podman"]
    return [f"# unknown distribution: install podman, see {PODMAN_DOCS}"]


def install_runtime() -> None:
    steps.run_admin_commands(runtime_install_commands(), ContainerError, "podman", PODMAN_DOCS)


def run_hint(runtime: str, tag: str | None = None) -> str:
    """Return the command that runs a script in the image, using ``runtime``."""
    return f'{runtime} run --rm -v "$PWD:/data" {image_ref(tag or image_tag())} python /data/run.py'


# --- steps ------------------------------------------------------------------


def _runtime_step() -> Step:
    return Step(
        name="runtime",
        summary="install Podman, a container runtime (Docker Desktop is never installed)",
        check=lambda: find_runtime() is not None,
        commands=runtime_install_commands,
        run=install_runtime,
        needs_admin=True,
    )


def _image_present() -> bool:
    runtime = find_runtime()
    return runtime is not None and image_present(runtime, image_tag())


def _pull_command_text() -> list[str]:
    return [" ".join(pull_command(find_runtime() or "podman", image_tag()))]


def _pull() -> None:
    runtime = find_runtime()
    if runtime is None:
        raise ContainerError("no container runtime found; run: pysrim2 install runtime")
    pull_image(runtime, image_tag())


def _image_step() -> Step:
    return Step(
        name="image",
        summary=f"pull the SRIM container image {image_ref(image_tag())}",
        check=_image_present,
        commands=_pull_command_text,
        run=_pull,
        requires=("runtime",),
    )


register("runtime", _runtime_step)
register("image", _image_step)
