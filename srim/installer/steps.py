"""Install steps that ``pysrim2 install`` plans and runs.

A ``Step`` knows how to check whether it is already satisfied, which commands
it stands for (shown to the user) and how to run itself. Components register a
factory with ``register``; ``plan`` turns component names into steps. Other
modules register further components (``image``, ``runtime``, ``skill``).
"""

import shlex
import shutil
import subprocess
import sys
from collections.abc import Callable, Sequence
from dataclasses import dataclass

from srim.installer.srim_install import install_srim
from srim.installer.wine import (
    WINE_DOCS,
    WineSetupError,
    distro_wine_commands,
    setup_wine_prefix,
    wine_status,
)
from srim.paths import srim_dir, wine_prefix

COMPONENTS = ("srim", "wine", "image", "runtime", "skill")
PACKAGE_TIMEOUT_SECONDS = 1800


@dataclass
class Step:
    name: str
    summary: str
    check: Callable[[], bool]
    commands: Callable[[], list[str]]
    run: Callable[[], None]
    needs_admin: bool = False
    requires: tuple[str, ...] = ()


class ComponentUnavailable(Exception):
    """The component is unknown, not implemented yet, or not for this platform."""

    def __init__(self, component: str, reason: str | None = None):
        self.component = component
        self.reason = reason or f"component {component} is not available yet"
        super().__init__(self.reason)


_REGISTRY: dict[str, Callable[[], Step | list[Step]]] = {}


def register(component: str, factory: Callable[[], Step | list[Step]]) -> None:
    """Register ``factory`` as the provider of ``component``'s steps."""
    _REGISTRY[component] = factory


def is_registered(component: str) -> bool:
    return component in _REGISTRY


def plan(components: Sequence[str]) -> list[Step]:
    """Return the steps for ``components``; raise ComponentUnavailable if one is missing."""
    steps: list[Step] = []
    for component in components:
        factory = _REGISTRY.get(component)
        if factory is None:
            raise ComponentUnavailable(component)
        made = factory()
        steps.extend(made if isinstance(made, list) else [made])
    return steps


def current_platform() -> str:
    """Return ``windows``, ``macos`` or ``linux``."""
    if sys.platform == "win32":
        return "windows"
    if sys.platform == "darwin":
        return "macos"
    return "linux"


def container_runtime() -> str | None:
    """Return the name of an installed container runtime (docker or podman), if any."""
    for name in ("docker", "podman"):
        if shutil.which(name):
            return name
    return None


def default_components() -> list[str]:
    """Return the components installed when none are named, for this platform.

    Components nobody has registered are left out.
    """
    system = current_platform()
    if system == "windows":
        wanted = ["srim", "skill"]
    elif system == "linux":
        if wine_status().wine or not container_runtime():
            wanted = ["wine", "srim", "skill"]
        else:
            wanted = ["image", "skill"]
    elif container_runtime():
        wanted = ["image", "skill"]
    else:
        wanted = ["runtime", "image", "skill"]
    return [c for c in wanted if is_registered(c)]


# --- srim -------------------------------------------------------------------


def _srim_step() -> Step:
    dest = srim_dir()
    return Step(
        name="srim",
        summary=f"download SRIM-2013 (verified by SHA-256) and unpack it into {dest}",
        check=lambda: (srim_dir() / "TRIM.exe").is_file(),
        commands=lambda: [f"download srim.2013.zip, verify SHA-256, unpack into {dest}"],
        run=lambda: install_srim(dest),
    )


# --- wine (Linux) -----------------------------------------------------------


def _wine_packages_ok() -> bool:
    status = wine_status()
    return all((status.wine, status.xvfb_run, status.extractor, status.downloader))


def _wine_package_commands() -> list[str]:
    return distro_wine_commands() or [
        f"# unknown distribution: install wine and xvfb, see {WINE_DOCS}"
    ]


def _run_wine_packages() -> None:
    commands = distro_wine_commands()
    if not commands:
        raise WineSetupError(
            f"unknown distribution; install wine and xvfb yourself, see {WINE_DOCS}"
        )
    for command in commands:
        if not command.strip() or command.lstrip().startswith("#"):
            raise WineSetupError(
                f"refusing to run {command!r}; install wine yourself, see {WINE_DOCS}"
            )
        try:
            subprocess.run(shlex.split(command), check=True, timeout=PACKAGE_TIMEOUT_SECONDS)
        except (OSError, subprocess.SubprocessError) as exc:
            raise WineSetupError(f"{command} failed: {exc}") from exc


def _wine_steps() -> list[Step]:
    if current_platform() != "linux":
        raise ComponentUnavailable("wine", "component wine is only for Linux")
    prefix = wine_prefix()
    return [
        Step(
            name="wine-packages",
            summary="install wine, xvfb and helper tools",
            check=_wine_packages_ok,
            commands=_wine_package_commands,
            run=_run_wine_packages,
            needs_admin=True,
        ),
        Step(
            name="wine",
            summary=f"set up a private wine prefix at {prefix} (VB5 runtime and OCX components)",
            check=lambda: wine_status().prefix_ready,
            commands=lambda: [f"create wine prefix {prefix}: wineboot, winetricks, VB5 runtime"],
            run=lambda: setup_wine_prefix(wine_prefix()),
            requires=("wine-packages",),
        ),
    ]


register("srim", _srim_step)
register("wine", _wine_steps)
