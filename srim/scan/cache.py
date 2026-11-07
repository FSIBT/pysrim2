"""The result cache of a scan: finished SRIM runs, reused by reruns and other scans.

An entry is the complete output folder of one SRIM run (what ``output_dir=`` receives: the
SRIM outputs and ``run.yaml``), stored under :func:`cache_key` of its run spec and seed. The
key covers everything that goes into ``TRIM.IN`` (the spec's ``to_dict()``, ``number_ions``
included) and the seed, but no provenance, so a rerun or a second scan with the same run
reuses the outputs. Extractors read an entry like any run's outputs, so an extractor added
later works on runs that were cached earlier.

Layout of a cache folder (created on first use)::

    CACHEDIR.TAG             the standard cache tag, naming pysrim2
    <key[:2]>/<key>/         an entry: the run's files, and ``.complete`` written last
    locks/<key>.lock         held while a run for the key is computed and stored
    tmp/<key>-<uuid>/        an entry being written; renamed into place once complete

Writes are atomic: an entry is built in ``tmp/`` and renamed to its key when complete, so a
reader never sees a partial entry. A writer that is killed leaves only a ``tmp/`` folder, which
the next write of the same key removes. :func:`~srim.scan.run_scan` takes the key's lock before
it computes a run, so two scans sharing a cache never compute the same key at the same time:
the second one waits for the first and then reuses its entry.

Only large outputs are hard-linked: a file of at least 1 MiB that is not ``run.yaml`` or an
input (``*.IN``). Everything else is copied, in both directions (a run's folder into an entry,
an entry into a point folder). So the linked files of an entry and of the point folder that made
or reused it share their data, and those linked files must never be edited in place. Editing
``run.yaml``, an input or a small output in a point folder never changes the entry.

Entries record the SRIM build they were made with (``srim_sha256`` in their ``run.yaml``). An
entry made with another build than the installed one is not used: the run is computed again
(with a warning, once per entry), and the old entry is kept until ``clear_cache`` removes it.
"""

import contextlib
import hashlib
import logging
import os
import re
import shutil
import uuid
from collections.abc import Mapping
from numbers import Integral, Real
from pathlib import Path

import yaml

from .._lock import FileLock
from ..spec import RunSpec

log = logging.getLogger(__name__)

#: tag line of the key's canonical text; bump it when the key's meaning changes
SCHEMA_TAG = "pysrim2-cache-v1"
#: the file written last into a complete entry
COMPLETE = ".complete"
#: the standard cache tag (https://bford.info/cachedir/)
TAG_NAME = "CACHEDIR.TAG"
_SIGNATURE = "Signature: 8a477f597d28d172789f06886806bc55"
_TAG_TEXT = (
    f"{_SIGNATURE}\n"
    "# pysrim2 result cache: finished SRIM runs reused by scans (srim.scan.cache).\n"
    "# 'pysrim2 scan clean' or srim.scan.cache.clear_cache() empties it.\n"
)
#: top-level files of a run that are not part of its entry (a failed attempt's log)
_NOT_CACHED = ("error.log",)
#: files at least this large are hard-linked (when the file system allows it)
LINK_MIN_BYTES = 1 << 20
_warned: set[str] = set()


def _canonical(value):
    """``value`` as plain YAML-safe data: mappings, lists, str, bool, int, float.

    Integral floats become ints (``30.0`` and ``30`` are the same value to ``TRIM.IN``), so
    the key does not depend on how a number was written.
    """
    if isinstance(value, Mapping):
        return {str(k): _canonical(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_canonical(v) for v in value]
    if value is None or isinstance(value, (bool, str)):
        return value
    if isinstance(value, Integral):
        return int(value)
    if isinstance(value, Real):
        number = float(value)
        return int(number) if number.is_integer() else number
    return value


def cache_key(spec: RunSpec, seed: int) -> str:
    """SHA-256 hex digest that identifies one SRIM run: its spec and its seed.

    The digest covers the canonical YAML of ``spec.to_dict(inline=True)``, ``number_ions``
    included, with its ``seed`` set to the given one, after a schema tag line. Beam
    distributions are covered in full: their definitions, histogram content (inline) and the
    source of Python-function distributions (:func:`srim.beam.spec.fingerprint`). Provenance
    (host, times, versions) is not part of it.
    """
    from ..beam.spec import fingerprint  # lazy: srim.beam imports srim.scan.extract

    data = _canonical(spec.to_dict(inline=True))
    data["seed"] = _canonical(seed)
    digest = fingerprint(spec)
    if digest is not None:  # only with functions, so plain specs keep their keys
        data["function_sources"] = digest
    text = SCHEMA_TAG + "\n" + yaml.safe_dump(data, sort_keys=True, default_flow_style=False)
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def current_srim_sha() -> str | None:
    """The SRIM build sha256 of the installed SRIM (as :meth:`TRIM.run` records it), or None."""
    from ..installer.srim_install import MARKER_NAME
    from ..paths import srim_dir

    try:
        text = (Path(srim_dir()) / MARKER_NAME).read_text(encoding="utf-8").strip()
    except OSError:
        return None
    return text or None


def cache_root(cache, scan_dir) -> Path | None:
    """The cache folder that ``cache=`` of :func:`~srim.scan.run_scan` names.

    ``False``: no cache (None). ``None``: ``$PYSRIM2_CACHE`` if set, otherwise
    ``<scan_dir>/cache``. Any other value is a path.
    """
    if cache is False:
        return None
    if cache is None:
        override = os.environ.get("PYSRIM2_CACHE")
        chosen = override if override else os.path.join(os.fspath(scan_dir), "cache")
    else:
        chosen = os.fspath(cache)
    return Path(os.path.abspath(chosen))


def is_cache(path) -> bool:
    """True if ``path`` holds a pysrim2 ``CACHEDIR.TAG`` (what :func:`clear_cache` requires)."""
    try:
        text = (Path(path) / TAG_NAME).read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return False
    lines = text.splitlines()
    return bool(lines) and lines[0] == _SIGNATURE and "pysrim2" in text


class RunCache:
    """A cache folder: look up, lock, store and read the entries of :func:`cache_key`.

    The folder is created on first use. The scan asks for the key's lock with
    :meth:`try_lock` before it computes a run, and stores the run with :meth:`store` while it
    holds the lock.
    """

    def __init__(self, root):
        self.root = Path(os.path.abspath(os.fspath(root)))

    def entry_dir(self, key: str) -> Path:
        """Where the entry of ``key`` is (whether or not it exists)."""
        return self.root / key[:2] / key

    def prepare(self):
        """Create the folder and its tag. Refuses a non-empty folder that is not a cache."""
        root = self.root
        if root.exists() and not is_cache(root):
            # another process may be creating the cache right now: only its own layout
            # (tag being written, locks, tmp) is tolerated in an untagged folder
            if not root.is_dir() or any(
                not (p.name in (TAG_NAME, "locks", "tmp") or p.name.startswith(f".{TAG_NAME}."))
                for p in root.iterdir()
            ):
                raise ValueError(
                    f"{root} is not a pysrim2 result cache (not empty, and no pysrim2 "
                    f"{TAG_NAME}); choose another folder"
                )
        root.mkdir(parents=True, exist_ok=True)
        if not is_cache(root):
            # atomic, so a concurrent scan never reads a half-written tag
            part = root / f".{TAG_NAME}.{uuid.uuid4().hex}"
            part.write_text(_TAG_TEXT, encoding="utf-8")
            try:
                # POSIX: replaces atomically; Windows: fails if another scan was first (its
                # tag is complete too, it was renamed into place the same way)
                os.rename(part, root / TAG_NAME)
            except (FileExistsError, PermissionError):
                part.unlink(missing_ok=True)
        (root / "locks").mkdir(exist_ok=True)
        (root / "tmp").mkdir(exist_ok=True)

    def lookup(self, key: str, srim_sha: str | None = None) -> Path | None:
        """The complete entry of ``key``, or None.

        An entry whose recorded SRIM build differs from ``srim_sha`` is a miss (one warning
        per entry). An unknown build on either side matches.
        """
        entry = self.entry_dir(key)
        if not (entry / COMPLETE).is_file():
            return None
        recorded = _recorded_sha(entry)
        if srim_sha and recorded and recorded != srim_sha:
            if key not in _warned:
                _warned.add(key)
                log.warning(
                    "cached run %s was made with another SRIM build (sha256 %s, installed "
                    "%s); it is not used and the run is computed again. 'pysrim2 scan clean' "
                    "removes it.",
                    key[:12],
                    recorded[:12],
                    srim_sha[:12],
                )
            return None
        return entry

    def try_lock(self, key: str) -> FileLock | None:
        """The exclusive lock of ``key``, or None if another process holds it (no waiting)."""
        (self.root / "locks").mkdir(parents=True, exist_ok=True)
        lock = FileLock(self.root / "locks" / f"{key}.lock", timeout=0)
        try:
            return lock.acquire()
        except TimeoutError:
            return None

    def store(self, key: str, source) -> Path:
        """Make ``source`` (a run's output folder) the entry of ``key``.

        The caller holds the key's lock. Files named in ``_NOT_CACHED`` at the top level are
        left out. An entry that is already complete is kept, unless it records another SRIM
        build than ``source`` does (both known): then the new run replaces it. If another
        writer completed the entry meanwhile, its entry is kept.
        """
        final = self.entry_dir(key)
        replace = False
        if (final / COMPLETE).is_file():
            new_sha, old_sha = _recorded_sha(Path(source)), _recorded_sha(final)
            if not (new_sha and old_sha and new_sha != old_sha):
                return final
            replace = True
            log.info("cached run %s is replaced: it was made with another SRIM build", key[:12])
        _sweep_partial(self.root, key)
        tmp = self.root / "tmp" / f"{key}-{uuid.uuid4().hex}"
        old = self.root / "tmp" / f"{key}-old-{uuid.uuid4().hex}"
        moved = False
        try:
            _link_tree(source, tmp, skip=_NOT_CACHED)
            (tmp / COMPLETE).write_text(key + "\n", encoding="utf-8")
            final.parent.mkdir(parents=True, exist_ok=True)
            if replace:
                os.replace(final, old)
                moved = True
            os.replace(tmp, final)
        except OSError:
            if moved and not final.exists():  # put the old entry back
                with contextlib.suppress(OSError):
                    os.replace(old, final)
            elif not replace and (final / COMPLETE).is_file():  # another writer finished first
                return final
            raise
        finally:
            shutil.rmtree(tmp, ignore_errors=True)
            shutil.rmtree(old, ignore_errors=True)
        return final


def materialise(entry, out) -> None:
    """Make the files of ``entry`` appear in folder ``out`` (hard links, or copies)."""
    _link_tree(entry, out, skip=(COMPLETE,))


def clear_cache(path) -> int:
    """Remove the entries of the cache at ``path``; returns how many were removed.

    Only a folder with a pysrim2 ``CACHEDIR.TAG`` is cleared; anything else raises ValueError
    and nothing is touched. Only what the cache itself makes is removed: shard folders named
    ``[0-9a-f]{2}`` holding entries named ``[0-9a-f]{64}``, and the contents of ``tmp/``. Any
    other item is left in place with a warning. An entry whose key is locked (a scan is
    computing or storing it) is kept. Lock files in ``locks/`` are never removed (they are
    tiny and removing one could let a second writer lock a different file for the same key).
    The tag stays, so the folder is still recognised as a cache.
    """
    root = Path(os.path.abspath(os.fspath(path)))
    if not is_cache(root):
        raise ValueError(
            f"{root} is not a pysrim2 result cache (no pysrim2 {TAG_NAME}); nothing removed"
        )
    cache = RunCache(root)
    removed = 0
    for item in sorted(root.iterdir()):
        if item.name in (TAG_NAME, "locks", "tmp"):
            continue
        if not _is_plain_dir(item, _SHARD):
            log.warning("clear_cache: %s is not part of the cache; left in place", item)
            continue
        for entry in sorted(item.iterdir()):
            if not _is_plain_dir(entry, _KEY):
                log.warning("clear_cache: %s is not a cache entry; left in place", entry)
                continue
            lock = cache.try_lock(entry.name)
            if lock is None:
                continue  # being computed or stored by a scan
            try:
                shutil.rmtree(entry, ignore_errors=True)
                removed += 1
            finally:
                lock.release()
        _rmdir_if_empty(item)
    tmp = root / "tmp"
    if tmp.is_dir():
        for leftover in sorted(tmp.iterdir()):
            key = leftover.name.split("-", 1)[0]
            if not _KEY.fullmatch(key):
                log.warning("clear_cache: %s is not a cache leftover; left in place", leftover)
                continue
            lock = cache.try_lock(key)
            if lock is None:
                continue
            try:
                shutil.rmtree(leftover, ignore_errors=True)
            finally:
                lock.release()
        _rmdir_if_empty(tmp)
    return removed


_SHARD = re.compile(r"[0-9a-f]{2}")
_KEY = re.compile(r"[0-9a-f]{64}")


def _is_plain_dir(path: Path, pattern) -> bool:
    return path.is_dir() and not path.is_symlink() and pattern.fullmatch(path.name) is not None


def _recorded_sha(entry: Path) -> str | None:
    """The ``srim_sha256`` an entry's ``run.yaml`` records, or None (missing or unknown)."""
    try:
        with open(entry / "run.yaml", encoding="utf-8") as f:
            document = yaml.safe_load(f)
    except (OSError, UnicodeDecodeError, yaml.YAMLError):
        return None
    provenance = document.get("provenance") if isinstance(document, dict) else None
    sha = provenance.get("srim_sha256") if isinstance(provenance, dict) else None
    return str(sha) if sha else None


def _sweep_partial(root: Path, key: str):
    """Remove what an earlier, killed writer of ``key`` left (the caller holds the lock)."""
    final = root / key[:2] / key
    if final.is_dir() and not (final / COMPLETE).is_file():
        shutil.rmtree(final, ignore_errors=True)
    for leftover in (root / "tmp").glob(f"{key}-*"):
        shutil.rmtree(leftover, ignore_errors=True)


def _link_tree(source, dest, skip=()):
    """Recreate the folder ``source`` at ``dest`` with hard links (copies if not possible).

    Top-level files named in ``skip`` are not copied.
    """
    source, dest = Path(source), Path(dest)
    if not source.is_dir():
        raise FileNotFoundError(f"no run folder at {source}")
    dest.mkdir(parents=True, exist_ok=True)
    for dirpath, _dirnames, filenames in os.walk(source):
        here = Path(dirpath)
        target = dest / here.relative_to(source)
        target.mkdir(parents=True, exist_ok=True)
        for name in filenames:
            if here == source and name in skip:
                continue
            _link_file(here / name, target / name)


def _link_file(src: Path, dst: Path):
    """``dst`` is a hard link to ``src`` if the file is a large output, else a copy."""
    if dst.exists() or dst.is_symlink():
        dst.unlink()
    if _linkable(src):
        try:
            os.link(src, dst)
            return
        except OSError:
            pass
    shutil.copy2(src, dst)


def _linkable(src: Path) -> bool:
    if src.name == "run.yaml" or src.name.upper().endswith(".IN"):
        return False
    return src.stat().st_size >= LINK_MIN_BYTES


def _rmdir_if_empty(path: Path):
    try:
        path.rmdir()
    except OSError:
        pass
