"""The agent activity log: what an agent did, and what the user answered (Qt-free).

Every request and answer of the agent goes into one JSON line of ``activity.jsonl`` under the
data directory. The app's activity panel and headless mode write the same file, and each entry
says which mode wrote it. The file is private to the user and rotates at a size limit.
"""

import dataclasses
import json
import math
import os
import warnings
from dataclasses import dataclass
from pathlib import Path

from srim.paths import data_dir

LOG_NAME = "activity.jsonl"
#: the most characters one text field of an entry keeps; a longer text is cut, with an ellipsis
FIELD_CAP = 2000
#: the most bytes of a log's end that :meth:`ActivityLog.last` reads (the newest entries are last)
TAIL_BYTES = 256 * 1024
#: the modes an entry can have
MODES = ("app", "headless")
#: the latest time an entry can have: 9999-12-31 in seconds since 1970
_TIME_MAX = 253402300799.0
_FIELDS = ("time", "mode", "client", "tool", "summary", "ask", "answer", "error")
_REQUIRED = ("time", "mode", "client", "tool", "summary")


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


def entry_from_untrusted(data: object) -> ActivityEntry:
    """The entry ``data`` says: a dict the agent sent (or a line of the log), every field checked.

    Raises ``ValueError``, with a sentence for the user, when the shape is wrong: not an object, a
    field the log does not have, a required field missing, a wrong type, a time that is not a
    moment, or a mode that is not one of :data:`MODES`. A text longer than :data:`FIELD_CAP` is cut.
    """
    if not isinstance(data, dict):
        raise ValueError("an entry is an object")
    if any(key not in _FIELDS for key in data):
        raise ValueError("an entry has a field the activity log does not have")
    for name in _REQUIRED:
        if name not in data:
            raise ValueError(f"an entry needs the field {name!r}")
    stamp = data["time"]
    if (
        isinstance(stamp, bool)
        or not isinstance(stamp, (int, float))
        or not math.isfinite(stamp)
        or not 0 <= stamp <= _TIME_MAX
    ):
        raise ValueError("time is a number of seconds since 1970")
    if not isinstance(data["mode"], str) or data["mode"] not in MODES:
        raise ValueError(f"mode is one of {', '.join(MODES)}")
    return ActivityEntry(
        time=float(stamp),
        mode=data["mode"],
        client=_text(data["client"], "client"),
        tool=_text(data["tool"], "tool"),
        summary=_text(data["summary"], "summary"),
        ask=_optional_text(data.get("ask"), "ask"),
        answer=_optional_text(data.get("answer"), "answer"),
        error=_optional_text(data.get("error"), "error"),
    )


def _text(value: object, name: str) -> str:
    if not isinstance(value, str):
        raise ValueError(f"{name} is text")
    if len(value) > FIELD_CAP:
        return value[: FIELD_CAP - 1] + "…"
    return value


def _optional_text(value: object, name: str) -> str | None:
    return None if value is None else _text(value, name)


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
        """The last ``n`` entries, oldest first, from the rotated file too when it is needed.

        Only the end of each file is read (:data:`TAIL_BYTES`), so a large log costs the same.
        """
        if n <= 0:
            return []
        entries = self._read(self.path)
        if len(entries) < n:
            older = self._read(self.path.with_name(self.path.name + ".1"))
            entries = older + entries
        return entries[-n:]

    @staticmethod
    def _read(path: Path) -> list[ActivityEntry]:
        """The readable entries at the end of ``path``; broken lines are skipped, a missing file is
        empty, and bytes that are not text are replaced rather than refused."""
        try:
            with open(path, "rb") as handle:
                handle.seek(0, os.SEEK_END)
                start = max(0, handle.tell() - TAIL_BYTES)
                # the tail begins inside a line unless the byte before it ends one
                cut = False
                if start > 0:
                    handle.seek(start - 1)
                    cut = handle.read(1) != b"\n"
                handle.seek(start)
                data = handle.read()
        except OSError:
            return []
        lines = data.decode("utf-8", errors="replace").split("\n")
        if cut:
            lines = lines[1:]  # the first line began before the tail: it is cut
        entries = []
        for line in lines:
            try:
                entries.append(ActivityEntry.from_dict(json.loads(line)))
            except (ValueError, TypeError, KeyError, AttributeError, RecursionError):
                continue
        return entries
