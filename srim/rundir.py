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
import sys
import tempfile
import warnings
from pathlib import Path

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
SKIP_NAMES = frozenset({".pysrim2.lock"})

# In a wine prefix only large binaries are linked; everything wine may write (registry,
# user profile, temp files, ini files, caches) is copied.
WINE_LINK_SUFFIXES = frozenset(
    {".dll", ".exe", ".sys", ".drv", ".ocx", ".nls", ".ttf", ".ttc", ".fon", ".acm", ".cpl", ".so"}
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

    def __init__(self, root, path, source, wine_prefix, keep, manifest):
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
        base = Path(base) if base is not None else data_dir() / "runs"
        # fail early and with the missing path in the error if there is no install
        with os.scandir(source):
            pass
        base.mkdir(parents=True, exist_ok=True)
        root = Path(tempfile.mkdtemp(prefix="run-", dir=base))
        try:
            path = root / "srim"
            manifest = cls._populate(source, path)
            prefix = None
            if wine is None:
                wine = _wine_available()
            if wine:
                prefix = clone_wine_prefix(wine_prefix(), root / "wine")
        except BaseException:
            shutil.rmtree(root, ignore_errors=True)
            raise
        return cls(root, path, source, prefix, keep, manifest)

    @staticmethod
    def _populate(source, path):
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
                shutil.rmtree(entry.path, ignore_errors=True)
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
        shutil.rmtree(self.wine_prefix, ignore_errors=True)
        self.wine_prefix = None

    def stop_wine(self):
        """Stop the wine processes of *this* run only (``wineserver -k`` in its prefix)."""
        if self.wine_prefix is not None:
            from .installer.wine import _kill_wineserver, wine_env

            _kill_wineserver(wine_env(self.wine_prefix))

    def cleanup(self):
        """Stop this run's wine processes and delete the whole run folder."""
        self.stop_wine()
        shutil.rmtree(self.root, ignore_errors=True)

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        if exc is not None:
            self.stop_wine()
            exc.add_note(f"the run directory was kept for debugging: {self.path}")
        elif not self.keep:
            self.cleanup()
        return False


def _wine_available():
    if sys.platform == "win32" or not shutil.which("wine"):
        return False
    from .installer.wine import _prefix_ready

    return _prefix_ready(wine_prefix())
