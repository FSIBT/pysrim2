"""The install child takes its planner from the request only. An environment variable naming a
planner has no effect: production never reads one, and tests inject the fake planner through
``HeadlessBackend(plan_steps=...)``, which the request carries."""

import install_fakes
from install_fakes import use

from srim.installer import steps as installer_steps
from srim.mcp import install_child
from srim.mcp.headless import HeadlessBackend


def test_the_planner_is_never_read_from_the_environment(monkeypatch):
    monkeypatch.setenv("PYSRIM2_INSTALL_PLANNER", "install_fakes:plan")
    assert install_child._planner(None) is installer_steps.plan


def test_an_install_uses_the_injected_planner_whatever_the_environment(tmp_path, monkeypatch):
    fake = use(monkeypatch, tmp_path, {"wine": [{"name": "fetch"}]})
    monkeypatch.setenv("PYSRIM2_INSTALL_PLANNER", "no_such_module:plan")
    out = HeadlessBackend(tmp_path, plan_steps=install_fakes.plan).install_step("wine")
    assert out["ran"] == ["fetch"]
    assert fake.calls() == ["fetch"]
