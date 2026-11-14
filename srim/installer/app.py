"""Install the desktop app (the ``app`` extra) into the pysrim2 install that is running.

How it is installed depends on how pysrim2 was installed: a ``uv tool`` install is reinstalled
with the extras it already has plus ``app``; a pip install gets ``pysrim2[app]`` at the same
version; a source checkout (editable) is told to run ``uv sync --extra app`` instead.
On Python 3.15 and newer the app is not offered (see ``app_supported``).
"""

import importlib.metadata
import importlib.util
import json
import os
import shlex
import shutil
import subprocess
import sys
import tomllib
from pathlib import Path

import srim
from srim.installer.steps import PACKAGE_TIMEOUT_SECONDS, ComponentUnavailable, Step, register

#: the top-level packages of the ``app`` extra (checked without importing them)
APP_PACKAGES = ("PySide6", "matplotlib", "marimo", "jupyterlab", "nbconvert", "playwright")
#: the playwright browsers that nbconvert's WebPDF (the notebook PDF export) prints with
CHROMIUM_BROWSERS = ("chromium", "chromium-headless-shell")
RECEIPT = "uv-receipt.toml"
EDITABLE_HINT = "# in your pysrim2 checkout run: uv sync --extra app"
# PySide6 breaks Python 3.15's lazy imports (concurrent.futures, used by scans) once it is
# imported, so the app extra is limited to older Pythons until PySide6 supports 3.15.
APP_PYTHON_LIMIT = (3, 15)
APP_PYTHON_NOTE = (
    "the desktop app needs Python 3.14 or older (PySide6 does not support Python 3.15 yet)"
)


class AppInstallError(RuntimeError):
    """The desktop app could not be installed (the command failed or could not start)."""


class _FindUv:
    """Default for ``app_command``'s ``uv``: look for uv when the command is built."""

    def __repr__(self) -> str:
        return "<find uv with shutil.which>"


_FIND_UV = _FindUv()


def app_supported(version: tuple[int, ...] = tuple(sys.version_info)) -> bool:
    """True when the app can run on this Python (``version``: the major and minor numbers)."""
    return tuple(version[:2]) < APP_PYTHON_LIMIT


def app_available() -> bool:
    """True when every package of the app extra can be found (see ``APP_PACKAGES``)."""
    return all(importlib.util.find_spec(name) is not None for name in APP_PACKAGES)


def _playwright_manifest() -> Path | None:
    """The ``browsers.json`` of the installed playwright (its browser revisions), or None.

    Found from the package's location, so playwright itself is not imported (importing it, or
    starting its driver, is slow). Returns None when playwright is not installed.
    """
    spec = importlib.util.find_spec("playwright")
    if spec is None or not spec.origin:
        return None
    return Path(spec.origin).parent / "driver" / "package" / "browsers.json"


def _browsers_root(manifest: Path) -> Path:
    """Where playwright keeps its browsers: ``PLAYWRIGHT_BROWSERS_PATH`` (``0`` means inside
    the package), else its default cache folder (``ms-playwright``)."""
    env = os.environ.get("PLAYWRIGHT_BROWSERS_PATH")
    if env == "0":
        return manifest.parent / ".local-browsers"
    if env:
        return Path(env)
    if sys.platform == "darwin":
        base = Path.home() / "Library" / "Caches"
    elif sys.platform == "win32":
        base = Path(os.environ.get("LOCALAPPDATA") or Path.home() / "AppData" / "Local")
    else:
        base = Path(os.environ.get("XDG_CACHE_HOME") or Path.home() / ".cache")
    return base / "ms-playwright"


def chromium_available() -> bool:
    """True when the Chromium browsers of playwright are downloaded (False without playwright).

    A browser folder is ``<name>-<revision>`` (name with ``_`` for ``-``) under the browsers
    folder, and playwright writes ``INSTALLATION_COMPLETE`` in it when the download is done.

    The layout (the manifest, the folder names, the marker file) is playwright's private layout,
    not a documented interface: a playwright release that changes it makes this check say the
    browser is missing, so the app asks for a reinstall rather than failing later.
    """
    manifest = _playwright_manifest()
    if manifest is None or not manifest.is_file():
        return False
    try:
        browsers = json.loads(manifest.read_text(encoding="utf-8"))["browsers"]
    except (OSError, ValueError, KeyError, TypeError):
        return False
    root = _browsers_root(manifest)
    markers = []
    for browser in browsers:
        if isinstance(browser, dict) and browser.get("name") in CHROMIUM_BROWSERS:
            folder = f"{browser['name'].replace('-', '_')}-{browser['revision']}"
            markers.append(root / folder / "INSTALLATION_COMPLETE")
    return len(markers) == len(CHROMIUM_BROWSERS) and all(m.is_file() for m in markers)


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


def chromium_command(python: str = sys.executable) -> list[str]:
    """The command that downloads the Chromium of playwright (into the running Python's install)."""
    return [python, "-m", "playwright", "install", "chromium"]


def _app_steps() -> list[Step]:
    if not app_supported():
        raise ComponentUnavailable("app", APP_PYTHON_NOTE)
    kind = install_kind()
    extras: set[str] = set()
    if kind == "uv-tool":
        extras = installed_extras(Path(sys.prefix) / RECEIPT)
    cmd = app_command(kind, srim.__version__, extras)
    commands = [shlex.join(cmd)] if cmd else [EDITABLE_HINT]
    app = Step(
        name="app",
        summary=(
            "install the desktop app (PySide6, matplotlib, the notebook tools marimo and "
            "JupyterLab, nbconvert for the PDF export) into this pysrim2 install"
        ),
        check=lambda: app_available(),
        commands=lambda: list(commands),
        run=lambda: _run(cmd),
    )
    chromium = chromium_command()
    # a checkout installs the packages with uv sync first; the download then uses their playwright
    chromium_commands = [EDITABLE_HINT, shlex.join(chromium)] if not cmd else [shlex.join(chromium)]
    download = Step(
        name="chromium",
        summary="download the Chromium that the notebook PDF export prints with (playwright)",
        check=chromium_available,
        commands=lambda: list(chromium_commands),
        run=lambda: _run(chromium),
        requires=("app",),
    )
    return [app, download]


register("app", _app_steps)
