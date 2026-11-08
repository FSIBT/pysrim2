"""Shared setup for the app tests.

The offscreen platform is chosen before Qt is imported, so the Qt tests run without a display.
Nothing here imports PySide6: the Qt-free tests in this folder run without the app extra.
"""

import os

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")


@pytest.fixture(autouse=True)
def app_home(tmp_path, monkeypatch):
    """Keep every test's settings file in a temporary folder."""
    home = tmp_path / "pysrim2-home"
    monkeypatch.setenv("PYSRIM2_HOME", str(home))
    return home
