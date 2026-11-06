"""Private working copies of the SRIM install, so runs never collide.

A :class:`RunDir` is a lightweight copy of the installed SRIM directory: read-only
files (executables, data tables, documentation) are *hard links* to the installed
files, files that SRIM writes or that differ per run are real copies. On Linux with
wine it also owns a private clone of pysrim2's wine prefix, so ``wineserver -k``
after a failure only stops that run's wine processes.

What SRIM writes (determined by inspecting a TRIM and an SR run):

* ``TRIM.IN``, ``TRIMAUTO`` (written by pysrim2 before every run), ``TRIM.dat``,
  ``TRIM 2.dat``, ``TRIMAUTO.TXT``, ``SR.IN`` and ``Data/TRIM.cfg``, ``Data/SRIMRun.dat``
  (state files of the programs): real copies;
* ``SR Module/``: copied completely (its stopping tables have ``_``-prefixed backups
  that the module may restore, so it is not treated as read-only);
* ``SRIM Outputs/`` and the result files (``RANGE.txt`` ...): created by the run; stale
  results of the installed directory are never copied into a run directory;
* every other file below 16 KiB is copied too (cheap, and safe whatever SRIM does);
* everything else (``*.exe``, big ``Data/`` tables, PDFs ...): hard links.
"""

import contextlib
import os
import shutil
import stat
import sys
import tempfile
import time
import warnings
from pathlib import Path

from ._lock import LOCK_NAME, FileLock
from .paths import data_dir, srim_dir, wine_prefix

RESULT_FILES = frozenset(
    name.lower()
    for name in (
        "PHONON.txt",
        "E2RECOIL.txt",
        "IONIZ.txt",
        "LATERAL.txt",
        "NOVAC.txt",
        "RANGE.txt",
        "VACANCY.txt",
        "COLLISON.txt",
        "BACKSCAT.txt",
        "SPUTTER.txt",
        "RANGE_3D.txt",
        "TRANSMIT.txt",
        "TRIMOUT.txt",
        "TDATA.txt",
        "EXYZ.txt",
        "SR_OUTPUT.txt",
        "run.yaml",
    )
)
WRITABLE_NAMES = frozenset(
    name.lower()
    for name in (
        "TRIM.IN",
        "TRIMAUTO",
        "TRIMAUTO.TXT",
        "TRIM.dat",
        "TRIM 2.dat",
        "SR.IN",
        "TRIM.cfg",
        "SRIMRun.dat",
    )
)
COPY_ALWAYS_DIRS = frozenset({"sr module", "srim outputs", "srim restore"})
SMALL_FILE_BYTES = 16 * 1024
SKIP_NAMES = frozenset({LOCK_NAME})
RUN_LOCK_NAME = ".pysrim2-run.lock"  # in the run folder: held while a process uses it
RETENTION_ENV = "PYSRIM2_RUN_RETENTION_DAYS"
DEFAULT_RETENTION_DAYS = 7.0
LAST_RUN_MARKER = ".last-isolated-run"  # in data_dir(): when the latest isolated run finished

# In a wine prefix only large binaries are linked; everything wine may write (registry,
# user profile, temp files, ini files, caches) is copied.
WINE_LINK_SUFFIXES = frozenset(
    {
        ".dll",
        ".exe",
        ".sys",
        ".drv",
        ".ocx",
        ".nls",
        ".ttf",
        ".ttc",
        ".fon",
        ".acm",
        ".cpl",
        ".so",
        ".ax",
        ".tlb",
        ".msstyles",
        ".vxd",
        ".ds",
        ".com",
        ".mui",
    }
)
WINE_COPY_DIRS = frozenset({"users", "programdata", "temp"})
WINE_SKIP_NAMES = frozenset({"wineserver"})

_warned_full_copy = False


def _warn_full_copy(what):
    global _warned_full_copy
    if not _warned_full_copy:
        _warned_full_copy = True
        warnings.warn(
            f"hard links are not possible here, so each run gets a full copy of {what} "
            "(more disk space and time per run); set work_dir= to a folder on the same "
            "file system as the SRIM install to avoid this",
            RuntimeWarning,
            stacklevel=3,
        )


def remove_tree(path):
    """Delete the folder ``path`` robustly; return the list of paths that could not go.

    Read-only files and folders (which stop ``rmtree`` on Windows) are made writable
    and retried. Leftovers are reported with a ``RuntimeWarning``, never silently kept.
    """
    leftovers = []

    def handler(func, failed, exc):
        if isinstance(exc, FileNotFoundError):
            return
        try:
            os.chmod(failed, stat.S_IRWXU)
            os.chmod(os.path.dirname(failed), stat.S_IRWXU)
            func(failed)
        except FileNotFoundError:
            pass
        except OSError:
            leftovers.append(str(failed))

    if not os.path.lexists(path):
        return []
    if sys.version_info >= (3, 12):
        shutil.rmtree(path, onexc=handler)
    else:
        shutil.rmtree(path, onerror=lambda func, p, info: handler(func, p, info[1]))
    if leftovers:
        warnings.warn(
            f"could not delete {len(leftovers)} item(s) of {path} (in use? read-only?): "
            f"{leftovers[:3]}",
            RuntimeWarning,
            stacklevel=2,
        )
    return leftovers


def retention_days():
    """Days after which unused run directories are deleted (``$PYSRIM2_RUN_RETENTION_DAYS``,
    default 7; 0 keeps them forever)."""
    try:
        days = float(os.environ.get(RETENTION_ENV, DEFAULT_RETENTION_DAYS))
    except ValueError:
        return DEFAULT_RETENTION_DAYS
    return max(days, 0.0)


def runs_base():
    """The default folder of the run directories: ``data_dir()/runs``."""
    return data_dir() / "runs"


def run_folders(base):
    base = Path(base)
    if not base.is_dir():
        return []
    return sorted(
        p for p in base.iterdir() if p.name.startswith("run-") and (p / RUN_LOCK_NAME).is_file()
    )


def runs_disk_usage(base=None):
    """Return ``(number of run directories, bytes they use)`` below ``base``."""
    folders = run_folders(base if base is not None else runs_base())
    return len(folders), sum(disk_usage(f) for f in folders)


def prune_runs(base=None, older_than_days=None, dry_run=False):
    """Delete run directories below ``base`` that nobody uses and that are older than the limit.

    A run directory counts as in use while a process holds its lock; its age is the time
    since its last use. ``older_than_days`` defaults to :func:`retention_days` (0 = no
    limit given: nothing is deleted when called without argument). Failed runs are
    deleted after the same time. Returns the list of folders deleted (or that would be).
    """
    days = retention_days() if older_than_days is None else float(older_than_days)
    if older_than_days is None and days <= 0:
        return []
    cutoff = time.time() - days * 86400
    removed = []
    for folder in run_folders(base if base is not None else runs_base()):
        marker = folder / RUN_LOCK_NAME
        lock = FileLock(marker, timeout=0)
        try:
            lock.acquire()
        except (TimeoutError, OSError):
            continue  # in use
        try:
            if marker.stat().st_mtime > cutoff:
                continue
            removed.append(folder)
            if dry_run:
                continue
        finally:
            lock.release()
        remove_tree(folder)
    return removed


def note_isolated_run():
    """Record that an isolated run just finished (see :func:`warn_if_stale_install`)."""
    try:
        marker = data_dir() / LAST_RUN_MARKER
        marker.parent.mkdir(parents=True, exist_ok=True)
        marker.write_text(str(time.time()))
    except OSError:
        pass


_warned_stale = False


def warn_if_stale_install(directory):
    """Warn (once) when ``directory`` is the installed SRIM and its outputs are older than
    the latest isolated run: legacy code reading outputs from there gets old results."""
    global _warned_stale
    if _warned_stale:
        return
    try:
        marker = data_dir() / LAST_RUN_MARKER
        if not marker.is_file() or not os.path.samefile(directory, srim_dir()):
            return
        installed = Path(directory)
        newest = max(
            (
                p.stat().st_mtime
                for name in RESULT_FILES
                for p in (installed / name,)
                if p.is_file()
            ),
            default=0.0,
        )
        if newest >= marker.stat().st_mtime:
            return
    except OSError:
        return
    _warned_stale = True
    warnings.warn(
        f"{directory} holds the installed SRIM, but since pysrim2 0.9.0 TRIM.run() and SR.run() "
        "work in private run directories, so the files there are NOT from your latest run. "
        "Read the outputs from the returned results (`results.directory`, "
        "`TRIM.copy_output_files(results.directory, dest)`) or pass `output_dir=` to run().",
        UserWarning,
        stacklevel=3,
    )


class OwnsRunDir:
    """Mixin of the result classes: ``close()`` / ``with`` delete the run directory
    a default (isolated) run made, once the results are no longer needed."""

    _run_root = None

    def close(self):
        """Delete the run directory these results were read from (if the run made one)."""
        root, self._run_root = self._run_root, None
        if root is not None:
            remove_tree(root)

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()
        return False


class _Placer:
    """Hard-link a file when possible, otherwise copy it (remembers that links fail)."""

    def __init__(self, what):
        self.what = what
        self.can_link = True

    def place(self, src, dst, copy=False):
        """Create ``dst`` from ``src``; return ``"link"`` or ``"copy"``."""
        if not copy and self.can_link:
            try:
                os.link(src, dst)
                return "link"
            except FileNotFoundError:
                raise
            except OSError:
                self.can_link = False
                _warn_full_copy(self.what)
        shutil.copy2(src, dst)
        with contextlib.suppress(OSError):
            os.chmod(dst, os.stat(dst).st_mode | 0o200)
        return "copy"


def _srim_decision(rel_parts, name, size):
    """Return ``"skip"``, ``"copy"`` or ``"link"`` for a file of the SRIM install."""
    lower = name.lower()
    if lower in SKIP_NAMES:
        return "skip"
    top = rel_parts[0].lower() if rel_parts else ""
    if lower in RESULT_FILES and (not rel_parts or top == "srim outputs"):
        return "skip"
    if lower in WRITABLE_NAMES or top in COPY_ALWAYS_DIRS or size < SMALL_FILE_BYTES:
        return "copy"
    return "link"


def _wine_decision(rel_parts, name):
    lower = name.lower()
    if lower in WINE_SKIP_NAMES:
        return "skip"
    if any(part.lower() in WINE_COPY_DIRS for part in rel_parts):
        return "copy"
    return "link" if os.path.splitext(lower)[1] in WINE_LINK_SUFFIXES else "copy"


def _tree(src_root):
    """Yield ``(relative parts, DirEntry)`` for everything below ``src_root`` (depth first)."""
    stack = [((), src_root)]
    while stack:
        parts, directory = stack.pop()
        with os.scandir(directory) as it:
            entries = sorted(it, key=lambda e: e.name)
        for entry in entries:
            yield parts, entry
            if entry.is_dir(follow_symlinks=False):
                stack.append(((*parts, entry.name), entry.path))


def clone_wine_prefix(src, dst):
    """Clone the wine prefix ``src`` into the new directory ``dst`` and return ``dst``.

    Large read-only binaries are hard-linked; registry files (``*.reg``), the user
    profile and everything else wine may write are copied; symlinks (``dosdevices``)
    are recreated. Falls back to a full copy where hard links are not possible.
    """
    src, dst = str(src), Path(dst)
    dst.mkdir(parents=True)
    placer = _Placer("the wine prefix")
    for parts, entry in _tree(src):
        target = dst.joinpath(*parts, entry.name)
        if entry.is_symlink():
            os.symlink(os.readlink(entry.path), target)
        elif entry.is_dir(follow_symlinks=False):
            target.mkdir()
        else:
            decision = _wine_decision(parts, entry.name)
            if decision != "skip":
                placer.place(entry.path, target, copy=decision == "copy")
    return dst


def disk_usage(path):
    """Bytes used by the files below ``path`` that no other name points to.

    Hard links to files outside the tree (the installed SRIM) are not counted, so this
    is what the directory really adds to the disk.
    """
    total = 0
    for root, _dirs, files in os.walk(path):
        for name in files:
            st = os.lstat(os.path.join(root, name))
            if st.st_nlink <= 1:
                total += st.st_size
    return total


class RunDir:
    """A private working copy of the SRIM install (see the module docstring).

    Create it with :meth:`create`; use it as a context manager. It is removed on leaving
    the ``with`` block unless ``keep=True``; after an exception it is always kept (for
    debugging) and the exception gets a note with its path.

    Attributes
    ----------
    path : pathlib.Path
        The working copy of the SRIM directory (contains ``TRIM.exe``).
    root : pathlib.Path
        The folder that holds ``path`` and, with wine, ``wine_prefix``.
    wine_prefix : pathlib.Path or None
        Private clone of pysrim2's wine prefix, or ``None`` (Windows, no wine).
    """

    def __init__(self, root, path, source, wine_prefix, keep, manifest, lock=None):
        self._lock = lock
        self.root = Path(root)
        self.path = Path(path)
        self.source = Path(source)
        self.wine_prefix = Path(wine_prefix) if wine_prefix else None
        self.keep = keep
        self._manifest = manifest

    @classmethod
    def create(cls, base=None, *, source=None, keep=False, wine=None):
        """Make a new run directory below ``base`` (default ``data_dir()/runs``).

        Parameters
        ----------
        base : path-like, optional
            Folder to create the run folder in (made if missing).
        source : path-like, optional
            The SRIM install to copy; default ``srim_dir()`` at call time.
        keep : bool, optional
            Keep the directory when leaving the ``with`` block.
        wine : bool, optional
            Clone the wine prefix; default: when ``wine`` is installed and pysrim2's
            private prefix has been set up.
        """
        source = Path(source) if source is not None else srim_dir()
        base = Path(os.path.abspath(base)) if base is not None else runs_base()
        # fail early and with the missing path in the error if there is no install
        with os.scandir(source):
            pass
        base.mkdir(parents=True, exist_ok=True)
        root = Path(tempfile.mkdtemp(prefix="run-", dir=base))
        lock = FileLock(root / RUN_LOCK_NAME).acquire()  # marks the folder as in use
        try:
            path = root / "srim"
            manifest = cls._populate(source, path)
            prefix = None
            if wine is None:
                wine = _wine_available()
            if wine:
                prefix = clone_wine_prefix(wine_prefix(), root / "wine")
        except BaseException:
            lock.release()
            remove_tree(root)
            raise
        return cls(root, path, source, prefix, keep, manifest, lock)

    @staticmethod
    def _populate(source, path):
        # A legacy in-place run (srim_directory=) holds an exclusive lock while it writes;
        # wait for it so the copy is not torn. Nothing is created in the install: if no
        # in-place run ever happened there is no lock file and nothing to wait for.
        guard = FileLock(Path(source) / LOCK_NAME, shared=True)
        try:
            guard.acquire()
        except OSError:
            guard = None
        try:
            return RunDir._copy_tree(source, path)
        finally:
            if guard is not None:
                guard.release()

    @staticmethod
    def _copy_tree(source, path):
        placer = _Placer("the SRIM install")
        path.mkdir()
        manifest = {}
        for parts, entry in _tree(str(source)):
            rel = (*parts, entry.name)
            target = path.joinpath(*rel)
            if entry.is_symlink():
                os.symlink(os.readlink(entry.path), target)
                manifest[rel] = "symlink"
            elif entry.is_dir(follow_symlinks=False):
                target.mkdir()
                manifest[rel] = "dir"
            else:
                decision = _srim_decision(parts, entry.name, entry.stat().st_size)
                if decision != "skip":
                    manifest[rel] = placer.place(entry.path, target, copy=decision == "copy")
        return manifest

    def reset(self):
        """Return to the freshly created state: delete outputs, restore copied inputs.

        The wine prefix is kept, so a worker can reuse one run directory for many runs.
        """
        for rel, kind in self._manifest.items():
            target = self.path.joinpath(*rel)
            src = self.source.joinpath(*rel)
            if kind == "copy":
                with contextlib.suppress(FileNotFoundError):
                    os.remove(target)
                shutil.copy2(src, target)
                with contextlib.suppress(OSError):
                    os.chmod(target, os.stat(target).st_mode | 0o200)
            elif kind == "link":
                try:
                    same = os.path.samefile(src, target)
                except OSError:
                    same = False
                if not same:
                    with contextlib.suppress(FileNotFoundError):
                        os.remove(target)
                    os.link(src, target)
        known = set(self._manifest)
        for parts, entry in list(_tree(str(self.path))):
            rel = (*parts, entry.name)
            if rel in known or not os.path.lexists(entry.path):
                continue
            if entry.is_dir(follow_symlinks=False):
                remove_tree(entry.path)
            else:
                os.remove(entry.path)

    def disk_usage(self):
        """Bytes this run directory really occupies (hard links not counted twice)."""
        return disk_usage(self.root)

    def release_wine(self):
        """Stop this run's wineserver and delete its wine prefix (outputs are unaffected)."""
        if self.wine_prefix is None:
            return
        self.stop_wine()
        remove_tree(self.wine_prefix)
        self.wine_prefix = None

    def stop_wine(self):
        """Stop the wine processes of *this* run only (``wineserver -k`` in its prefix)."""
        if self.wine_prefix is not None:
            from .installer.wine import _kill_wineserver, wine_env

            _kill_wineserver(wine_env(self.wine_prefix))

    def cleanup(self):
        """Stop this run's wine processes and delete the whole run folder."""
        self.stop_wine()
        self.release_lock()
        remove_tree(self.root)

    def release_lock(self):
        """Mark the folder as no longer in use (it can then be pruned once it is old)."""
        lock, self._lock = self._lock, None
        if lock is not None:
            with contextlib.suppress(OSError):
                os.utime(lock.path)
            lock.release()

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        if exc is not None:
            self.stop_wine()
            self.release_lock()
            exc.add_note(f"the run directory was kept for debugging: {self.path}")
        elif not self.keep:
            self.cleanup()
        else:
            self.release_lock()
        return False


def _wine_available():
    if sys.platform == "win32" or not shutil.which("wine"):
        return False
    from .installer.wine import _prefix_ready

    return _prefix_ready(wine_prefix())
