"""Download, verify and install SRIM outside the PyPI package.

The pinned SRIM-2013 zip is fetched from the sources in ``Asset.urls``, checked
against its size and SHA-256, then unpacked into a directory that holds
``TRIM.exe`` directly. Installs are atomic: the files are staged next to the
destination and moved into place with a single rename.
"""

import hashlib
import http.client
import os
import shutil
import tempfile
import urllib.request
import zipfile
from pathlib import Path, PurePosixPath, PureWindowsPath

import srim
from srim.installer.assets import SRIM_ASSET, Asset
from srim.paths import data_dir

TIMEOUT_SECONDS = 60
CHUNK_SIZE = 1 << 16
MARKER_NAME = ".pysrim2-installed"
MANUAL_SOURCE = "https://github.com/FSIBT/pysrim2/releases/tag/srim-2013"
NOTICE = "SRIM © SRIM.com (James F. Ziegler) — free for non-commercial use only; see LEGAL.rtf"


class SrimInstallError(RuntimeError):
    """SRIM could not be downloaded, verified or installed."""


def install_srim(dest: Path, asset: Asset = SRIM_ASSET, *, cache: Path | None = None) -> Path:
    """Install SRIM into ``dest`` and return ``dest``.

    Returns immediately if ``dest`` already holds ``TRIM.exe``, for example an
    install that was copied in by hand. Raises ``SrimInstallError`` if ``dest``
    is a non-empty directory without ``TRIM.exe``; such a directory is never
    removed or overwritten.
    """
    dest = Path(dest)
    if (dest / "TRIM.exe").is_file():
        return dest
    _refuse_existing(dest)

    cache = Path(cache) if cache is not None else data_dir() / "cache"
    zip_path = _fetch(asset, cache, dest)

    dest.parent.mkdir(parents=True, exist_ok=True)
    staging = Path(tempfile.mkdtemp(prefix=".srim-install-", dir=dest.parent))
    try:
        top = _unpack(zip_path, staging, asset)
        _write_markers(top, asset)
        _refuse_existing(dest)
        _replace(top, dest)
    finally:
        shutil.rmtree(staging, ignore_errors=True)
    return dest


def _refuse_existing(dest: Path) -> None:
    """Refuse a non-empty ``dest`` that does not hold ``TRIM.exe``."""
    if not dest.exists() or (dest.is_dir() and not any(dest.iterdir())):
        return
    raise SrimInstallError(
        f"{dest} already exists and is not a SRIM install (no TRIM.exe). Remove it, "
        "or set PYSRIM2_SRIM_DIR to an existing SRIM install."
    )


def _replace(src: Path, dst: Path) -> None:
    try:
        os.replace(src, dst)
    except OSError as exc:
        raise SrimInstallError(f"could not move {src} to {dst}: {exc}") from exc


def _fetch(asset: Asset, cache: Path, dest: Path) -> Path:
    """Return a path to a verified copy of the asset, downloading it if needed."""
    cache.mkdir(parents=True, exist_ok=True)
    cached = cache / asset.name
    if cached.exists():
        if _problem(cached, asset) is None:
            return cached
        cached.unlink()

    failures = []
    for url in asset.urls:
        fd, part_name = tempfile.mkstemp(prefix=asset.name + ".", suffix=".part", dir=cache)
        os.close(fd)
        part = Path(part_name)
        try:
            _download(url, part)
            problem = _problem(part, asset)
            if problem is None:
                _replace(part, cached)
                return cached
            failures.append((url, problem))
        except (OSError, http.client.HTTPException, ValueError) as exc:
            failures.append((url, str(exc) or type(exc).__name__))
        finally:
            part.unlink(missing_ok=True)

    lines = [f"Could not obtain {asset.name}:"]
    lines += [f"  {url}: {problem}" for url, problem in failures]
    lines.append(
        f"Download {asset.name} manually from {MANUAL_SOURCE}, check its SHA-256 "
        f"({asset.sha256}), unzip it and copy the contents of its top-level srim/ folder "
        f"into {dest}, or unzip it anywhere and set PYSRIM2_SRIM_DIR to that srim/ folder."
    )
    lines.append(NOTICE)
    raise SrimInstallError("\n".join(lines))


def _download(url: str, target: Path) -> None:
    request = urllib.request.Request(url, headers={"User-Agent": f"pysrim2/{srim.__version__}"})
    with urllib.request.urlopen(request, timeout=TIMEOUT_SECONDS) as response:
        with open(target, "wb") as out:
            shutil.copyfileobj(response, out, CHUNK_SIZE)


def _problem(path: Path, asset: Asset) -> str | None:
    """Return why the file does not match the asset, or None if it matches."""
    size = path.stat().st_size
    if size != asset.size:
        return f"size {size} does not match expected {asset.size}"
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        for chunk in iter(lambda: handle.read(CHUNK_SIZE), b""):
            digest.update(chunk)
    actual = digest.hexdigest()
    if actual != asset.sha256:
        return f"SHA-256 mismatch: expected {asset.sha256}, got {actual}"
    return None


def _unpack(zip_path: Path, staging: Path, asset: Asset) -> Path:
    """Extract the zip into ``staging`` and return the directory to install."""
    try:
        with zipfile.ZipFile(zip_path) as archive:
            members = archive.infolist()
            parts_by_member = []
            for info in members:
                parts = _checked_parts(info.filename, asset)
                if parts:
                    parts_by_member.append((info, parts))
            archive.extractall(staging)
    except zipfile.BadZipFile as exc:
        raise SrimInstallError(f"{asset.name} is not a valid zip file: {exc}") from exc
    except OSError as exc:
        raise SrimInstallError(f"could not extract {asset.name} into {staging}: {exc}") from exc

    top_names = {parts[0] for _, parts in parts_by_member}
    if len(top_names) == 1 and all(
        len(parts) > 1 or info.is_dir() for info, parts in parts_by_member
    ):
        top = staging / top_names.pop()
    else:
        top = staging
    if not (top / "TRIM.exe").is_file():
        raise SrimInstallError(f"{asset.name} does not contain TRIM.exe")
    return top


def _checked_parts(name: str, asset: Asset) -> tuple[str, ...]:
    """Return the path components of a zip member, refusing unsafe names."""
    normalized = name.replace("\\", "/")
    parts = PurePosixPath(normalized).parts
    unsafe = normalized.startswith("/") or PureWindowsPath(name).drive != "" or ".." in parts
    if unsafe:
        raise SrimInstallError(f"{asset.name} contains an unsafe path: {name!r}")
    return tuple(part for part in parts if part not in ("", "."))


def _write_markers(top: Path, asset: Asset) -> None:
    (top / "TRIMAUTO").write_text("1\n")
    (top / MARKER_NAME).write_text(asset.sha256)
