"""Where pysrim2 keeps its data, the SRIM install, and the wine prefix.

Every lookup is resolved at call time, so environment changes take effect
without re-importing the package.
"""

import os
import sys
from pathlib import Path

# Location used by earlier pysrim releases. An install found here is still used.
LEGACY_SRIM_DIR = Path("/tmp/srim")


def _env_path(name):
    """Return the environment variable as a Path, or None when unset or empty."""
    value = os.environ.get(name)
    return Path(value) if value else None


def data_dir():
    """Return pysrim2's per-user data directory.

    ``$PYSRIM2_HOME`` wins if set. Otherwise it is ``%LOCALAPPDATA%\\pysrim2``
    on Windows, ``~/Library/Application Support/pysrim2`` on macOS, and
    ``${XDG_DATA_HOME:-~/.local/share}/pysrim2`` elsewhere.
    """
    override = _env_path("PYSRIM2_HOME")
    if override:
        return override
    if sys.platform == "win32":
        local = _env_path("LOCALAPPDATA") or Path.home() / "AppData" / "Local"
        return local / "pysrim2"
    if sys.platform == "darwin":
        return Path.home() / "Library" / "Application Support" / "pysrim2"
    xdg = _env_path("XDG_DATA_HOME") or Path.home() / ".local" / "share"
    return xdg / "pysrim2"


def srim_dir():
    """Return the directory that contains ``TRIM.exe``.

    Checked in order: ``$PYSRIM2_SRIM_DIR``; ``data_dir()/srim`` if it holds
    ``TRIM.exe``; the legacy ``/tmp/srim`` if it holds ``TRIM.exe``; otherwise
    ``data_dir()/srim`` (where a new install would go).
    """
    override = _env_path("PYSRIM2_SRIM_DIR")
    if override:
        return override
    installed = data_dir() / "srim"
    if (installed / "TRIM.exe").is_file():
        return installed
    if (LEGACY_SRIM_DIR / "TRIM.exe").is_file():
        return LEGACY_SRIM_DIR
    return installed


def wine_prefix():
    """Return the wine prefix that pysrim2 runs SRIM in.

    ``$PYSRIM2_WINEPREFIX`` if set, otherwise ``data_dir()/wine``. The user's
    own ``$WINEPREFIX`` and ``~/.wine`` are never used.
    """
    override = _env_path("PYSRIM2_WINEPREFIX")
    if override:
        return override
    return data_dir() / "wine"
