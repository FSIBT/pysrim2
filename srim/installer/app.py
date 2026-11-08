"""Install the desktop app (the ``app`` extra) into the pysrim2 install that is running.

How it is installed depends on how pysrim2 was installed: a ``uv tool`` install is reinstalled
with the extras it already has plus ``app``; a pip install gets ``pysrim2[app]`` at the same
version; a source checkout (editable) is told to run ``uv sync --extra app`` instead.
"""

import importlib.metadata
import importlib.util
import json
import shlex
import shutil
import subprocess
import sys
import tomllib
from pathlib import Path

import srim
from srim.installer.steps import PACKAGE_TIMEOUT_SECONDS, Step, register

APP_PACKAGES = ("PySide6", "matplotlib")
RECEIPT = "uv-receipt.toml"
EDITABLE_HINT = "# in your pysrim2 checkout run: uv sync --extra app"


class AppInstallError(RuntimeError):
    """The desktop app could not be installed (the command failed or could not start)."""


class _FindUv:
    """Default for ``app_command``'s ``uv``: look for uv when the command is built."""

    def __repr__(self) -> str:
        return "<find uv with shutil.which>"


_FIND_UV = _FindUv()


def app_available() -> bool:
    """True when both the Qt binding and matplotlib can be found."""
    return all(importlib.util.find_spec(name) is not None for name in APP_PACKAGES)


def _direct_url() -> dict | None:
    """The parsed ``direct_url.json`` of the installed pysrim2 distribution, if it has one."""
    try:
        text = importlib.metadata.distribution("pysrim2").read_text("direct_url.json")
    except importlib.metadata.PackageNotFoundError:
        return None
    if not text:
        return None
    try:
        data = json.loads(text)
    except ValueError:
        return None
    return data if isinstance(data, dict) else None


def install_kind(prefix: Path | None = None) -> str:
    """Return ``uv-tool``, ``editable`` or ``pip`` for the running pysrim2."""
    root = Path(sys.prefix) if prefix is None else Path(prefix)
    if (root / RECEIPT).is_file():
        return "uv-tool"
    direct = _direct_url() or {}
    if (direct.get("dir_info") or {}).get("editable") is True:
        return "editable"
    return "pip"


def installed_extras(receipt: Path) -> set[str]:
    """The extras of the ``pysrim2`` entry in a uv receipt; empty when absent or unreadable."""
    try:
        data = tomllib.loads(Path(receipt).read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, tomllib.TOMLDecodeError):
        return set()
    for requirement in (data.get("tool") or {}).get("requirements") or []:
        if isinstance(requirement, dict) and requirement.get("name") == "pysrim2":
            return set(requirement.get("extras") or [])
    return set()


def app_command(
    kind: str,
    version: str,
    extras: set[str] = frozenset(),
    python: str = sys.executable,
    uv: str | None | _FindUv = _FIND_UV,
) -> list[str]:
    """The command that installs the app for ``kind``; empty for an editable checkout.

    ``uv`` is the uv executable: the default looks it up now, and ``None`` means no uv.
    """
    if uv is _FIND_UV:
        uv = shutil.which("uv")
    if kind == "uv-tool":
        spec = f"pysrim2[{','.join(sorted(set(extras) | {'app'}))}]=={version}"
        return ["uv", "tool", "install", "--reinstall", spec]
    if kind == "pip":
        spec = f"pysrim2[app]=={version}"
        if uv:
            return [uv, "pip", "install", "--python", python, spec]
        return [python, "-m", "pip", "install", spec]
    if kind == "editable":
        return []
    raise ValueError(f"unknown install kind {kind!r}")


def _run(cmd: list[str]) -> None:
    if not cmd:
        raise AppInstallError(
            "nothing to run here; in your pysrim2 checkout run: uv sync --extra app"
        )
    try:
        subprocess.run(cmd, check=True, timeout=PACKAGE_TIMEOUT_SECONDS)
    except (subprocess.SubprocessError, OSError) as exc:
        raise AppInstallError(f"{exc}; run it yourself: {shlex.join(cmd)}") from exc


def _app_step() -> Step:
    kind = install_kind()
    extras: set[str] = set()
    if kind == "uv-tool":
        extras = installed_extras(Path(sys.prefix) / RECEIPT)
    cmd = app_command(kind, srim.__version__, extras)
    commands = [shlex.join(cmd)] if cmd else [EDITABLE_HINT]
    return Step(
        name="app",
        summary="install the desktop app (PySide6, matplotlib) into this pysrim2 install",
        check=lambda: app_available(),
        commands=lambda: list(commands),
        run=lambda: _run(cmd),
    )


register("app", _app_step)
