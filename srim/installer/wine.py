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
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from srim.installer.srim_install import _download
from srim.paths import data_dir, wine_prefix

# xvfb-run's default screen is 640x480x8; wine 11's wineboot hangs on it.
XVFB_SCREEN_ARGS = ["-s", "-screen 0 1024x768x24"]
XVFB_ARGS = ["-a", *XVFB_SCREEN_ARGS]
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
KILL_TIMEOUT_SECONDS = 15
SHUTDOWN_TIMEOUT_SECONDS = 60
DLL_OVERRIDES = "winebus.sys,wineusb.sys,winebth.sys=d;mscoree,mshtml="
AEDEBUG_KEY = r"HKLM\Software\Microsoft\Windows NT\CurrentVersion\AeDebug"
DLLS = ("msvbvm50.dll", "comcat.dll")
MARKER_NAME = ".pysrim2-vb5"
WINE_DOCS = "https://wiki.winehq.org/Download"
WINE_MANUAL_HINT = (
    f"install wine (32-bit support) and xvfb-run with your package manager, see {WINE_DOCS}"
)


class WineSetupError(RuntimeError):
    """The wine prefix could not be set up."""


class WineTimeoutError(WineSetupError):
    """A wine command did not finish in time."""


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


def _pinned_winetricks() -> str | None:
    """Return the path of the downloaded, verified winetricks, or None if not fetched yet."""
    script = _winetricks_script()
    try:
        return str(script) if script.is_file() and _sha256(script) == WINETRICKS_SHA256 else None
    except OSError:
        return None


def wine_status() -> WineStatus:
    """Report which wine tools are on PATH and whether the prefix is ready."""
    return WineStatus(
        wine=shutil.which("wine"),
        xvfb_run=shutil.which("xvfb-run"),
        extractor=_find_extractor(),
        winetricks=_pinned_winetricks(),
        downloader=_find_downloader(),
        prefix_ready=_prefix_ready(wine_prefix()),
    )


def wine_env(prefix: Path) -> dict[str, str]:
    """Return a copy of ``os.environ`` that points wine at ``prefix``."""
    env = dict(os.environ)
    env["WINEPREFIX"] = str(prefix)
    env["WINEARCH"] = "win32"
    env["WINEDEBUG"] = "-all"
    # SRIM needs no controller/USB/Bluetooth drivers (they crash without SDL on
    # some distros and keep wineserver alive) and no Mono/Gecko install prompts.
    overrides = os.environ.get("WINEDLLOVERRIDES")
    overrides = (overrides or "").rstrip(";")
    ours = f"{overrides};{DLL_OVERRIDES}" if overrides else DLL_OVERRIDES
    env["WINEDLLOVERRIDES"] = ours
    return env


def setup_wine_prefix(prefix: Path, progress: Callable[[str], None] | None = None) -> None:
    """Create a 32-bit wine prefix at ``prefix`` with what SRIM needs installed.

    That is the OCX components (via winetricks) and the VB5 runtime. Does
    nothing if the prefix was already set up by this function. ``progress`` is
    called with one line of text as each sub-step starts.
    """
    note = progress or (lambda _line: None)
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
        try:
            note("  - creating wine prefix (wineboot)…")
            _run([*xvfb, "wineboot", "-i"], env)
            _disable_crash_debugger(env, xvfb)
            _wait_for_wineserver(env, xvfb, note)
            note(
                "  - installing OCX controls with winetricks (downloads; can take several minutes)…"
            )
            cache = data_dir() / "cache" / "winetricks"
            cache.mkdir(parents=True, exist_ok=True)
            _run([*xvfb, *winetricks, "-q", *WINETRICKS_VERBS], {**env, "W_CACHE": str(cache)})
            _wait_for_wineserver(env, xvfb, note)
        except BaseException:
            # Failure or Ctrl-C: do not leave this prefix's wine processes running.
            _kill_wineserver(env)
            raise

        note("  - installing VB5 runtime DLLs…")
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


def _kill_wineserver(env: dict[str, str]) -> bool:
    """Stop the wineserver of the private prefix in ``env`` (``wineserver -k``); never raises.

    Returns whether a kill was attempted (a wineserver executable was found).
    """
    server = _wineserver()
    if not server:
        return False
    try:
        subprocess.run(
            [server, "-k"],
            env=env,
            timeout=KILL_TIMEOUT_SECONDS,
            check=False,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
    except (OSError, subprocess.SubprocessError):
        pass
    return True


def _disable_crash_debugger(env: dict[str, str], xvfb: list[str]) -> None:
    """Empty wine's ``AeDebug`` debugger so a crashing wine process never waits on winedbg.

    Best effort: a failure here is not an error (Ctrl-C still propagates).
    """
    cmd = [*xvfb, "wine", "reg", "add", AEDEBUG_KEY, "/v", "Debugger", "/t", "REG_SZ"]
    try:
        _run([*cmd, "/d", "", "/f"], env, timeout=SHUTDOWN_TIMEOUT_SECONDS)
    except WineSetupError:
        pass


def _wait_for_wineserver(
    env: dict[str, str], xvfb: list[str], note: Callable[[str], None] | None = None
) -> None:
    """Wait until wine's background processes finish (``wineserver -w``).

    Ubuntu does not put ``wineserver`` on PATH; if it cannot be found, ask
    wine to end its session instead. If wine does not shut down within
    ``SHUTDOWN_TIMEOUT_SECONDS`` (a crashed driver host can keep it alive),
    stop it with ``wineserver -k`` and carry on.
    """
    server = _wineserver()
    try:
        if server:
            _run([server, "-w"], env, timeout=SHUTDOWN_TIMEOUT_SECONDS)
        else:
            _run(
                [*xvfb, "wine", "wineboot", "--end-session"], env, timeout=SHUTDOWN_TIMEOUT_SECONDS
            )
            _wait_until_no_wineserver()
    except WineTimeoutError:
        killed = _kill_wineserver(env)
        if note:
            note(
                "  - wine did not shut down by itself; "
                + ("stopped it" if killed else "carrying on (no wineserver found to stop)")
            )


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


def _run(cmd: list[str], env: dict[str, str], timeout: float = SETUP_TIMEOUT_SECONDS) -> None:
    """Run ``cmd``; on failure raise WineSetupError with the tail of its output.

    Output goes to a temporary file, not a pipe: wine starts ``wineserver`` as a
    daemon that inherits the child's stdout, and a pipe would stay open (and
    block us) until that daemon exits.
    """
    with tempfile.TemporaryFile() as log:
        try:
            subprocess.run(
                cmd,
                env=env,
                check=True,
                timeout=timeout,
                stdout=log,
                stderr=subprocess.STDOUT,
            )
        except (OSError, subprocess.SubprocessError) as exc:
            message = f"{' '.join(cmd)} failed: {exc}"
            log.seek(0)
            output = log.read().decode(errors="replace")
            if not output.strip():
                output = getattr(exc, "output", None) or ""
                if isinstance(output, bytes):
                    output = output.decode(errors="replace")
            if output.strip():
                tail = "\n".join(output.strip().splitlines()[-OUTPUT_TAIL_LINES:])
                message += f"\nLast output:\n{tail}"
            error = (
                WineTimeoutError if isinstance(exc, subprocess.TimeoutExpired) else WineSetupError
            )
            raise error(message) from exc


def _winetricks_script() -> Path:
    """Return where pysrim2's pinned copy of winetricks lives (it may not exist yet)."""
    return data_dir() / "cache" / f"winetricks-{WINETRICKS_TAG}"


def _winetricks_command() -> list[str]:
    """Return the command that runs winetricks: always our pinned, verified copy.

    A winetricks on PATH is never used: old copies do not know the verbs we need.
    """
    script = _winetricks_script()
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
    # IDs seen: opensuse-tumbleweed, opensuse-leap; ID_LIKE "opensuse suse".
    if any(i.startswith("opensuse") or "suse" in i for i in ids):
        return ["sudo zypper install -y wine xvfb-run cabextract unzip curl"]
    # Arch: untested, package names taken from the Arch wiki/package list.
    if "arch" in ids:
        return ["sudo pacman -S --needed wine xorg-server-xvfb cabextract unzip curl"]
    return []
