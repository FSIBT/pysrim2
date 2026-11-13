"""The app's settings file: ``<data dir>/app/settings.json``.

A missing or broken file, or a field that is of the wrong type or not an allowed value, gives
the default for that field, so the app always starts. Writes go through a temporary file so a
crash never leaves half a file behind.
"""

import json
import math
import os
import tempfile
from dataclasses import asdict, dataclass, field
from pathlib import Path

from srim.app.routes import ROUTES
from srim.paths import data_dir

THEMES = ("system", "light", "dark")
#: the calculations whose ions-per-second rate the app remembers (as text, the JSON keys)
RATE_KEYS = tuple(str(n) for n in range(1, 8))
#: the run view's refresh interval, in whole seconds
REFRESH_RANGE = (1, 3600)


@dataclass
class AppSettings:
    route: str | None = None
    theme: str = "system"
    #: the last measured ions per second of each calculation, keyed "1".."7"
    ions_per_s: dict[str, float] = field(default_factory=dict)
    refresh_seconds: int = 5


def settings_path() -> Path:
    return data_dir() / "app" / "settings.json"


def _valid(data: dict) -> AppSettings:
    settings = AppSettings()
    route = data.get("route")
    if route is None or (isinstance(route, str) and route in ROUTES):
        settings.route = route
    theme = data.get("theme")
    if isinstance(theme, str) and theme in THEMES:
        settings.theme = theme
    rates = data.get("ions_per_s")
    if isinstance(rates, dict):
        for key, value in rates.items():
            if key in RATE_KEYS and _positive(value):
                settings.ions_per_s[key] = float(value)
    refresh = data.get("refresh_seconds")
    low, high = REFRESH_RANGE
    if isinstance(refresh, int) and not isinstance(refresh, bool) and low <= refresh <= high:
        settings.refresh_seconds = refresh
    return settings


def _positive(value) -> bool:
    """True for a finite number above zero (a bool is not a number here)."""
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return False
    return math.isfinite(value) and value > 0


def load_settings(path: Path | None = None) -> AppSettings:
    """Return the saved settings, or the defaults for anything missing or broken."""
    path = path or settings_path()
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return AppSettings()
    if not isinstance(data, dict):
        return AppSettings()
    return _valid(data)


def save_settings(settings: AppSettings, path: Path | None = None) -> None:
    """Write ``settings`` to ``path`` (default: the settings file), atomically."""
    path = path or settings_path()
    _write_json_atomic(path, asdict(settings), ".settings-")


def _write_json_atomic(path: Path, data, prefix: str) -> None:
    """Write ``data`` as JSON to ``path`` through a temporary file in the same folder.

    The file is replaced in one step, so a crash never leaves half a file behind. The app's
    other JSON files (the user materials) are written the same way.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=prefix, suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(data, handle, indent=2)
            handle.write("\n")
        os.replace(tmp, path)
    except BaseException:
        Path(tmp).unlink(missing_ok=True)
        raise
