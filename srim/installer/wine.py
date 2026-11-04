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
import time
from dataclasses import dataclass
from pathlib import Path

from srim.installer.srim_install import _download
from srim.paths import data_dir, wine_prefix

# xvfb-run's default screen is 640x480x8; wine 11's wineboot hangs on it.
XVFB_ARGS = ["-a", "-s", "-screen 0 1024x768x24"]
MSVBVM50_URL = "https://download.microsoft.com/download/vb50pro/utility/1/win98/en-us/msvbvm50.exe"
MSVBVM50_NAME = "msvbvm50.exe"
MSVBVM50_SHA256 = "b5f8ea5b9d8b30822a2be2cdcb89cda99ec0149832659ad81f45360daa6e6965"
WINETRICKS_TAG = "20260125"
WINETRICKS_URL = (
    f"https://raw.githubusercontent.com/Winetricks/winetricks/{WINETRICKS_TAG}/src/winetricks"
)
WINETRICKS_SHA256 = "431f82fc74000e6c864409f1d8fb495d696c03928808e3e8acffc45179312a7b"
WINETRICKS_VERBS = ("comdlg32ocx", "msflxgrd", "richtx32", "comctl32ocx", "tabctl32")
SETUP_TIMEOUT_SECONDS = 600
OUTPUT_TAIL_LINES = 20
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
    winetricks: str | None
    downloader: str | None
    prefix_ready: bool


def _find_extractor() -> str | None:
    for name in ("7z", "7zz", "cabextract"):
        if shutil.which(name):
            return name
    return None


def _find_downloader() -> str | None:
    for name in ("wget", "curl", "aria2c"):
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
        winetricks=shutil.which("winetricks"),
        downloader=_find_downloader(),
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
    """Create a 32-bit wine prefix at ``prefix`` with what SRIM needs installed.

    That is the OCX components (via winetricks) and the VB5 runtime. Does
    nothing if the prefix was already set up by this function.
    """
    prefix = Path(prefix)
    if _prefix_ready(prefix):
        return
    if not shutil.which("wine"):
        raise WineSetupError(f"wine is not installed; see {WINE_DOCS}")
    if not _find_downloader():
        install = distro_wine_commands()
        hint = f"; install the packages with: {' && '.join(install)}" if install else ""
        raise WineSetupError(
            f"none of wget, curl or aria2c is installed (winetricks needs one){hint}"
        )
    extractor = _find_extractor()
    if not extractor:
        raise WineSetupError("none of 7z, 7zz or cabextract is installed")

    env = wine_env(prefix)
    xvfb = ["xvfb-run", *XVFB_ARGS] if shutil.which("xvfb-run") else []
    with tempfile.TemporaryDirectory(prefix="pysrim2-vb5-") as tmp:
        # Fetch everything first so a network problem fails before wine is touched.
        work = Path(tmp)
        installer = work / MSVBVM50_NAME
        _fetch(MSVBVM50_URL, installer, MSVBVM50_SHA256, MSVBVM50_NAME)
        winetricks = _winetricks_command()
        extracted = work / "out"
        extracted.mkdir()
        _extract(extractor, installer, extracted)
        found = {p.name.lower(): p for p in extracted.rglob("*") if p.is_file()}
        missing = [dll for dll in DLLS if dll not in found]
        if missing:
            raise WineSetupError(f"{missing[0]} not found in {MSVBVM50_NAME}")

        prefix.mkdir(parents=True, exist_ok=True)
        _run([*xvfb, "wineboot", "-i"], env)
        _wait_for_wineserver(env, xvfb)
        cache = data_dir() / "cache" / "winetricks"
        cache.mkdir(parents=True, exist_ok=True)
        _run([*xvfb, *winetricks, "-q", *WINETRICKS_VERBS], {**env, "W_CACHE": str(cache)})
        _wait_for_wineserver(env, xvfb)

        system32 = _system32(prefix)
        system32.mkdir(parents=True, exist_ok=True)
        for dll in DLLS:
            shutil.copyfile(found[dll], system32 / dll)
    (prefix / MARKER_NAME).write_text("1\n")


WINESERVER_PATHS = (
    "/usr/lib/wine/wineserver",
    "/usr/lib/wine/wineserver32",
    "/usr/lib/wine/wineserver64",
    "/usr/lib/x86_64-linux-gnu/wine/wineserver",
    "/usr/lib/i386-linux-gnu/wine/wineserver",
    "/opt/wine-stable/bin/wineserver",
)


def _wineserver() -> str | None:
    """Return the path of ``wineserver``: on PATH, else a known distro location."""
    found = shutil.which("wineserver")
    if found:
        return found
    for candidate in WINESERVER_PATHS:
        if os.access(candidate, os.X_OK) and Path(candidate).is_file():
            return candidate
    return None


def _wait_for_wineserver(env: dict[str, str], xvfb: list[str]) -> None:
    """Wait until wine's background processes finish (``wineserver -w``).

    Ubuntu does not put ``wineserver`` on PATH; if it cannot be found, ask
    wine to end its session instead.
    """
    server = _wineserver()
    if server:
        _run([server, "-w"], env)
    else:
        _run([*xvfb, "wine", "wineboot", "--end-session"], env)
        _wait_until_no_wineserver()


def _wait_until_no_wineserver(timeout: float = 30.0) -> None:
    """Poll (at most ``timeout`` seconds) until no ``wineserver`` process is left.

    Needs ``pgrep``; without it there is nothing to poll and we return. It may
    also wait for servers of other prefixes, which is harmless: it is capped.
    """
    pgrep = shutil.which("pgrep")
    if not pgrep:
        return
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        found = subprocess.run([pgrep, "-x", "wineserver"], stdout=subprocess.DEVNULL)
        if found.returncode != 0:
            return
        time.sleep(0.5)


def _run(cmd: list[str], env: dict[str, str]) -> None:
    """Run ``cmd``; on failure raise WineSetupError with the tail of its output."""
    try:
        subprocess.run(
            cmd,
            env=env,
            check=True,
            timeout=SETUP_TIMEOUT_SECONDS,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        message = f"{' '.join(cmd)} failed: {exc}"
        output = getattr(exc, "output", None)
        if isinstance(output, bytes):
            output = output.decode(errors="replace")
        if output:
            tail = "\n".join(output.strip().splitlines()[-OUTPUT_TAIL_LINES:])
            message += f"\nLast output:\n{tail}"
        raise WineSetupError(message) from exc


def _winetricks_command() -> list[str]:
    """Return the command that runs winetricks: the system one, else a pinned download."""
    system = shutil.which("winetricks")
    if system:
        return [system]
    script = data_dir() / "cache" / f"winetricks-{WINETRICKS_TAG}"
    if not script.is_file() or _sha256(script) != WINETRICKS_SHA256:
        script.parent.mkdir(parents=True, exist_ok=True)
        _fetch(WINETRICKS_URL, script, WINETRICKS_SHA256, "winetricks")
    bash = shutil.which("bash")
    if not bash:
        raise WineSetupError("bash is required to run winetricks but was not found")
    return [bash, str(script)]


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _fetch(url: str, target: Path, sha256: str, name: str) -> None:
    """Download ``url`` to ``target`` and check its SHA-256."""
    part = target.with_name(target.name + ".part")
    try:
        _download(url, part)
        actual = _sha256(part)
        if actual != sha256:
            raise WineSetupError(f"SHA-256 mismatch for {name}: expected {sha256}, got {actual}")
        os.replace(part, target)
    except (OSError, ValueError) as exc:
        raise WineSetupError(f"could not download {url}: {exc}") from exc
    finally:
        part.unlink(missing_ok=True)


def _extract(extractor: str, installer: Path, out: Path) -> None:
    if extractor == "cabextract":
        cmd = [extractor, "-q", "-d", str(out), str(installer)]
    else:
        cmd = [extractor, "e", "-y", f"-o{out}", str(installer), "MSVBVM50.DLL", "COMCAT.DLL"]
    try:
        subprocess.run(cmd, check=True, stdout=subprocess.DEVNULL, timeout=SETUP_TIMEOUT_SECONDS)
    except (OSError, subprocess.SubprocessError) as exc:
        raise WineSetupError(f"could not extract {MSVBVM50_NAME} with {extractor}: {exc}") from exc


def distro_ids(os_release: Path = Path("/etc/os-release")) -> list[str]:
    """Return the ``ID`` and ``ID_LIKE`` words of ``os_release`` (empty if unreadable)."""
    ids: list[str] = []
    try:
        text = Path(os_release).read_text()
    except OSError:
        return []
    for line in text.splitlines():
        key, _, value = line.partition("=")
        if key in ("ID", "ID_LIKE"):
            ids += value.strip().strip("\"'").lower().split()
    return ids


def distro_wine_commands(os_release: Path = Path("/etc/os-release")) -> list[str]:
    """Return the shell commands that install wine on this distribution.

    The list is empty for distributions we do not know; see ``WINE_DOCS``.
    """
    ids = distro_ids(os_release)
    if "debian" in ids or "ubuntu" in ids:
        return [
            "sudo dpkg --add-architecture i386",
            "sudo apt-get update",
            "sudo apt-get install -y wine wine32:i386 xvfb xauth 7zip cabextract unzip curl",
        ]
    if "fedora" in ids:
        return ["sudo dnf install -y wine xorg-x11-server-Xvfb cabextract unzip curl"]
    return []
