"""Pinned SRIM download asset: where to fetch it and how to verify it."""

from dataclasses import dataclass


@dataclass(frozen=True)
class Asset:
    name: str
    sha256: str
    size: int
    urls: tuple[str, ...]


SRIM_ASSET = Asset(
    name="srim.2013.zip",
    sha256="f425c4715d5a849c1f521a8fc14aff58d41066fa4ca773546690a220ff3acecf",
    size=13768612,
    urls=(
        "https://github.com/FSIBT/pysrim2/releases/download/srim-2013/srim.2013.zip",
        "https://gitlab.com/costrouc/pysrim/-/raw/master/assets/srim.2013.zip",
    ),
)
