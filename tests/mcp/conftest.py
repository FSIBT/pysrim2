"""Every install in the MCP tests runs its steps in a child process that takes its plan from
``install_fakes`` (never the real installer): the child is told which planner to use."""

import pytest


@pytest.fixture(autouse=True)
def fake_install_planner(monkeypatch):
    monkeypatch.setenv("PYSRIM2_INSTALL_PLANNER", "install_fakes:plan")
