import dataclasses

import pytest

from srim.installer.assets import SRIM_ASSET, Asset


def test_srim_asset_matches_pinned_constants():
    assert SRIM_ASSET.name == "srim.2013.zip"
    assert SRIM_ASSET.sha256 == "f425c4715d5a849c1f521a8fc14aff58d41066fa4ca773546690a220ff3acecf"
    assert SRIM_ASSET.size == 13768612
    assert SRIM_ASSET.urls == (
        "https://github.com/FSIBT/pysrim2/releases/download/srim-2013/srim.2013.zip",
        "https://gitlab.com/costrouc/pysrim/-/raw/master/assets/srim.2013.zip",
    )


def test_asset_is_frozen():
    with pytest.raises(dataclasses.FrozenInstanceError):
        SRIM_ASSET.size = 1


def test_asset_fields():
    assert [f.name for f in dataclasses.fields(Asset)] == ["name", "sha256", "size", "urls"]
