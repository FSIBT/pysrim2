"""Shared test helpers.

Pytest puts this folder on ``sys.path`` (it holds this conftest), so the helper modules here,
such as ``run_folders``, import by name from any test folder.
"""

import pytest


@pytest.fixture(autouse=True)
def own_data_directory(tmp_path_factory, monkeypatch):
    """Every test has its own data directory. The user's is never read: it may hold the discovery
    file of an app that is running, and a test must not reach it (see ``srim.mcp.app_client``)."""
    monkeypatch.setenv("PYSRIM2_HOME", str(tmp_path_factory.mktemp("pysrim2-home")))
