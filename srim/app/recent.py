"""The recent setups and runs the app offers in File > Open recent: ``<data dir>/app/recent.json``.

A list of entries, newest first, at most :data:`RECENT_LIMIT`. An entry is a setup (a
``run.yaml``, kind :data:`SETUP`) or a run folder (kind :data:`RUN`), with the time it was opened
(ISO 8601, UTC). A file that is missing or is not a list reads as no entries; an entry that is
not a recent entry is skipped and the rest still load. Writes go through the atomic writer of
:mod:`srim.app.settings`. :func:`entry_problem` says why an entry cannot be opened now (a
moved or deleted file or folder, or a folder that is not a run folder). Nothing here imports Qt.
"""

import json
import os
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path

from srim.app.settings import _write_json_atomic
from srim.paths import data_dir
from srim.scan.executor import MARKER_NAME

#: the most entries kept
RECENT_LIMIT = 10
#: the kind of an entry that is a setup (a ``run.yaml``)
SETUP = "setup"
#: the kind of an entry that is a run folder
RUN = "run"
KINDS = (SETUP, RUN)
#: what :func:`entry_problem` says about an entry that cannot be opened
MOVED_TEXT = "the file was moved or deleted"
FOLDER_MOVED_TEXT = "the folder was moved or deleted"
NOT_A_RUN_TEXT = "not a pysrim2 run folder"


@dataclass(frozen=True)
class RecentEntry:
    path: str
    kind: str
    opened: str


def recent_path() -> Path:
    return data_dir() / "app" / "recent.json"


def new_entry(path, kind: str, now: datetime | None = None) -> RecentEntry:
    """An entry for ``path`` (made absolute) opened ``now`` (default: the current time, UTC)."""
    when = now or datetime.now(UTC)
    return RecentEntry(os.path.abspath(path), kind, when.isoformat(timespec="seconds"))


def _entry(raw) -> RecentEntry | None:
    if not isinstance(raw, dict):
        return None
    path, kind, opened = raw.get("path"), raw.get("kind"), raw.get("opened")
    if not isinstance(path, str) or not path or kind not in KINDS or not isinstance(opened, str):
        return None
    return RecentEntry(path, kind, opened)


def load_recent(path: Path | None = None) -> list[RecentEntry]:
    """The saved entries, newest first; a missing or broken file gives none."""
    path = path or recent_path()
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return []
    if not isinstance(data, list):
        return []
    entries = [entry for entry in (_entry(raw) for raw in data) if entry is not None]
    return entries[:RECENT_LIMIT]


def add_recent(entry: RecentEntry, path: Path | None = None) -> None:
    """Put ``entry`` at the front of the list, replacing an earlier entry of the same path, and
    keep :data:`RECENT_LIMIT` entries. The file is written atomically."""
    path = path or recent_path()
    entry = RecentEntry(os.path.abspath(entry.path), entry.kind, entry.opened)
    rest = [saved for saved in load_recent(path) if saved.path != entry.path]
    entries = [entry, *rest][:RECENT_LIMIT]
    _write_json_atomic(path, [asdict(e) for e in entries], ".recent-")


def entry_problem(entry: RecentEntry) -> str | None:
    """Why ``entry`` cannot be opened now, or None when it can.

    A setup needs its file; a run needs its folder and the scan marker that a scan folder has.
    """
    if entry.kind == SETUP:
        return None if Path(entry.path).is_file() else MOVED_TEXT
    folder = Path(entry.path)
    if not folder.is_dir():
        return FOLDER_MOVED_TEXT
    if not (folder / MARKER_NAME).is_file():
        return NOT_A_RUN_TEXT
    return None
