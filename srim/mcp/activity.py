"""The agent activity log: what an agent did, and what the user answered (Qt-free).

Every request and answer of the agent goes into one JSON line of ``activity.jsonl`` under the
data directory. The app's activity panel and headless mode write the same file, and each entry
says which mode wrote it. The file is private to the user and rotates at a size limit.
"""

import dataclasses
import json
import os
import warnings
from dataclasses import dataclass
from pathlib import Path

from srim.paths import data_dir

LOG_NAME = "activity.jsonl"


def activity_path() -> Path:
    """Where the activity log is kept: ``<data dir>/agent/activity.jsonl``."""
    return data_dir() / "agent" / LOG_NAME


@dataclass(frozen=True)
class ActivityEntry:
    """One line of the log.

    ``mode`` is "app" or "headless". ``ask`` is what the user was asked (when anything was), and
    ``answer`` is "allowed", "denied", "timed out" or "code issued". ``error`` is set when the
    action failed.
    """

    time: float
    mode: str
    client: str
    tool: str
    summary: str
    ask: str | None = None
    answer: str | None = None
    error: str | None = None

    def to_dict(self) -> dict:
        return dataclasses.asdict(self)

    @classmethod
    def from_dict(cls, data: dict) -> "ActivityEntry":
        """The entry in ``data``. Raises ``TypeError`` or ``KeyError`` when a field is missing."""
        known = {field.name for field in dataclasses.fields(cls)}
        return cls(**{key: value for key, value in data.items() if key in known})


class ActivityLog:
    """Appends entries to the log file and reads the last ones back."""

    def __init__(self, path: Path | None = None, max_bytes: int = 1_000_000):
        self.path = path if path is not None else activity_path()
        self.max_bytes = max_bytes
        self._warned = False

    def append(self, entry: ActivityEntry) -> None:
        """Write one entry. Never raises: a write that fails is reported once and dropped."""
        try:
            line = json.dumps(entry.to_dict(), sort_keys=True) + "\n"
            self.path.parent.mkdir(parents=True, exist_ok=True)
            if self.path.is_file() and self.path.stat().st_size > self.max_bytes:
                os.replace(self.path, self.path.with_name(self.path.name + ".1"))
            fd = os.open(self.path, os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o600)
            with os.fdopen(fd, "a", encoding="utf-8") as handle:
                handle.write(line)
        except (OSError, TypeError, ValueError) as error:
            if not self._warned:
                self._warned = True
                warnings.warn(f"the agent activity log could not be written: {error}", stacklevel=2)

    def last(self, n: int) -> list[ActivityEntry]:
        """The last ``n`` entries, oldest first, from the rotated file too when it is needed."""
        entries = self._read(self.path)
        if len(entries) < n:
            older = self._read(self.path.with_name(self.path.name + ".1"))
            entries = older + entries
        return entries[-n:] if n > 0 else []

    @staticmethod
    def _read(path: Path) -> list[ActivityEntry]:
        """Every readable entry of ``path``; broken lines are skipped, a missing file is empty."""
        try:
            lines = path.read_text(encoding="utf-8").splitlines()
        except OSError:
            return []
        entries = []
        for line in lines:
            try:
                entries.append(ActivityEntry.from_dict(json.loads(line)))
            except (ValueError, TypeError, KeyError, AttributeError):
                continue
        return entries
