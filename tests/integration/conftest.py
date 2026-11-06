"""Fixtures for the tests that run real SRIM (``pytest -m srim``)."""

import shutil

import pytest

from srim.paths import srim_dir


def pytest_configure(config):
    config.addinivalue_line("markers", "srim: runs real SRIM (needs SRIM and wine, or Windows)")


def pytest_collection_modifyitems(items):
    """Mark everything in this directory ``srim`` and skip it without SRIM."""
    missing = not (srim_dir() / "TRIM.exe").is_file()
    skip = pytest.mark.skip(reason=f"SRIM is not installed ({srim_dir()}/TRIM.exe is missing)")
    for item in items:
        if "integration" not in item.path.parts or item.get_closest_marker("container"):
            continue  # container tests run pysrim2 in an image: SRIM is not needed here
        item.add_marker(pytest.mark.srim)
        if missing:
            item.add_marker(skip)


@pytest.fixture
def srim_copy(tmp_path):
    """A private copy of the SRIM install in a directory whose name has a space."""
    target = tmp_path / "srim copy"
    shutil.copytree(srim_dir(), target)
    return target
