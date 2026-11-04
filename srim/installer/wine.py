"""Set up a private wine prefix that can run SRIM on Linux.

The prefix is pysrim2's own (``srim.paths.wine_prefix()``). The user's
``$WINEPREFIX`` and ``~/.wine`` are never read or modified, and
``WINEPREFIX`` is only ever set in the environment of child processes.

SRIM needs the Visual Basic 5 runtime (``msvbvm50.dll``) and ``comcat.dll``.
They are taken from Microsoft's ``msvbvm50.exe`` self-extractor; winetricks
is not used because its ``vb5run`` verb is broken on current Debian wine.
"""

import hashlib
import os
import shutil
import subprocess
import tempfile
from dataclasses import dataclass
from pathlib import Path

from srim.installer.srim_install import _download
from srim.paths import wine_prefix

MSVBVM50_URL = "https://download.microsoft.com/download/vb50pro/utility/1/win98/en-us/msvbvm50.exe"
MSVBVM50_NAME = "msvbvm50.exe"
MSVBVM50_SHA256 = "b5f8ea5b9d8b30822a2be2cdcb89cda99ec0149832659ad81f45360daa6e6965"
MSVBVM50_SIZE = 992864
DLLS = ("msvbvm50.dll", "comcat.dll")
MARKER_NAME = ".pysrim2-vb5"
WINE_DOCS = "https://wiki.winehq.org/Download"


class WineSetupError(RuntimeError):
    """The wine prefix could not be set up."""


@dataclass(frozen=True)
class WineStatus:
    wine: str | None
    xvfb_run: str | None
    extractor: str | None
    prefix_ready: bool


def _find_extractor() -> str | None:
    for name in ("7z", "7zz", "cabextract"):
        if shutil.which(name):
            return name
    return None


def _system32(prefix: Path) -> Path:
    return Path(prefix) / "drive_c" / "windows" / "system32"


def _prefix_ready(prefix: Path) -> bool:
    prefix = Path(prefix)
    return (prefix / MARKER_NAME).is_file() and all(
        (_system32(prefix) / dll).is_file() for dll in DLLS
    )


def wine_status() -> WineStatus:
    """Report which wine tools are on PATH and whether the prefix is ready."""
    return WineStatus(
        wine=shutil.which("wine"),
        xvfb_run=shutil.which("xvfb-run"),
        extractor=_find_extractor(),
        prefix_ready=_prefix_ready(wine_prefix()),
    )


def wine_env(prefix: Path) -> dict[str, str]:
    """Return a copy of ``os.environ`` that points wine at ``prefix``."""
    env = dict(os.environ)
    env["WINEPREFIX"] = str(prefix)
    env["WINEARCH"] = "win32"
    env["WINEDEBUG"] = "-all"
    return env


def setup_wine_prefix(prefix: Path) -> None:
    """Create a 32-bit wine prefix at ``prefix`` with the VB5 runtime installed.

    Does nothing if the prefix was already set up by this function.
    """
    prefix = Path(prefix)
    if _prefix_ready(prefix):
        return
    wine = shutil.which("wine")
    if not wine:
        raise WineSetupError(f"wine is not installed; see {WINE_DOCS}")
    extractor = _find_extractor()
    if not extractor:
        raise WineSetupError("none of 7z, 7zz or cabextract is installed")

    env = wine_env(prefix)
    prefix.mkdir(parents=True, exist_ok=True)
    boot = ["wineboot", "-i"]
    if shutil.which("xvfb-run"):
        boot = ["xvfb-run", "-a", *boot]
    subprocess.run(boot, env=env, check=True)
    subprocess.run(["wineserver", "-w"], env=env, check=True)

    with tempfile.TemporaryDirectory(prefix="pysrim2-vb5-") as tmp:
        work = Path(tmp)
        installer = work / MSVBVM50_NAME
        _fetch_msvbvm50(installer)
        extracted = work / "out"
        extracted.mkdir()
        _extract(extractor, installer, extracted)
        system32 = _system32(prefix)
        system32.mkdir(parents=True, exist_ok=True)
        found = {p.name.lower(): p for p in extracted.rglob("*") if p.is_file()}
        for dll in DLLS:
            if dll not in found:
                raise WineSetupError(f"{dll} not found in {MSVBVM50_NAME}")
            shutil.copyfile(found[dll], system32 / dll)
    (prefix / MARKER_NAME).write_text("1\n")


def _fetch_msvbvm50(target: Path) -> None:
    try:
        _download(MSVBVM50_URL, target)
    except (OSError, ValueError) as exc:
        raise WineSetupError(f"could not download {MSVBVM50_URL}: {exc}") from exc
    actual = hashlib.sha256(target.read_bytes()).hexdigest()
    if actual != MSVBVM50_SHA256:
        raise WineSetupError(
            f"SHA-256 mismatch for {MSVBVM50_NAME}: expected {MSVBVM50_SHA256}, got {actual}"
        )


def _extract(extractor: str, installer: Path, out: Path) -> None:
    if extractor == "cabextract":
        cmd = [extractor, "-q", "-d", str(out), str(installer)]
    else:
        cmd = [extractor, "e", "-y", f"-o{out}", str(installer), "MSVBVM50.DLL", "COMCAT.DLL"]
    try:
        subprocess.run(cmd, check=True, stdout=subprocess.DEVNULL)
    except (OSError, subprocess.CalledProcessError) as exc:
        raise WineSetupError(f"could not extract {MSVBVM50_NAME} with {extractor}: {exc}") from exc


def distro_wine_commands(os_release: Path = Path("/etc/os-release")) -> list[str]:
    """Return the shell commands that install wine on this distribution.

    The list is empty for distributions we do not know; see ``WINE_DOCS``.
    """
    ids: list[str] = []
    try:
        text = Path(os_release).read_text()
    except OSError:
        return []
    for line in text.splitlines():
        key, _, value = line.partition("=")
        if key in ("ID", "ID_LIKE"):
            ids += value.strip().strip("\"'").lower().split()
    if "debian" in ids or "ubuntu" in ids:
        return [
            "sudo dpkg --add-architecture i386",
            "sudo apt-get update",
            "sudo apt-get install -y wine wine32:i386 xvfb xauth cabextract",
        ]
    if "fedora" in ids:
        return ["sudo dnf install -y wine xorg-x11-server-Xvfb cabextract"]
    return []
