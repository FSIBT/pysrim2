"""The app's settings file: ``<data dir>/app/settings.json``.

A missing or broken file, or a field that is of the wrong type or not an allowed value, gives
the default for that field, so the app always starts. Writes go through a temporary file so a
crash never leaves half a file behind.
"""

import json
import os
import tempfile
from dataclasses import asdict, dataclass
from pathlib import Path

from srim.app.routes import ROUTES
from srim.paths import data_dir

THEMES = ("system", "light", "dark")


@dataclass
class AppSettings:
    route: str | None = None
    theme: str = "system"


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
    return settings


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
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=".settings-", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(asdict(settings), handle, indent=2)
            handle.write("\n")
        os.replace(tmp, path)
    except BaseException:
        Path(tmp).unlink(missing_ok=True)
        raise
