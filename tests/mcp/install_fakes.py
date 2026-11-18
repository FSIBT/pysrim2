"""Fake installer steps for the install child (``srim.mcp.install_child``) and its parent.

The plan is a JSON file named by ``PYSRIM2_FAKE_PLAN``: ``{component: [step, ...]}``. A step is
``{"name", "summary", "commands", "admin", "done", "action"}``. ``action`` is ``"ok"`` (nothing),
``"print"`` (prints from Python and from a child process), ``"fail"`` (raises OSError),
``"sleep"`` (two seconds) or ``"crash"`` (the process exits at once with code 3). Each run
appends the step's name to the file named by ``PYSRIM2_FAKE_CALLS``, so a test can see what ran,
in the child too.

``plan`` is the function both sides use (the child through ``PYSRIM2_INSTALL_PLANNER``, the
parent as the backend's ``plan_steps``), so they agree on the steps.
"""

import json
import os
import subprocess
import sys
import time
from dataclasses import dataclass
from pathlib import Path

from srim.installer.steps import ComponentUnavailable, Step


@dataclass
class Fake:
    """The plan file and the calls file of one test."""

    plan_path: Path
    calls_path: Path

    def set(self, data: dict) -> None:
        self.plan_path.write_text(json.dumps(data), encoding="utf-8")

    def calls(self) -> list[str]:
        if not self.calls_path.is_file():
            return []
        return self.calls_path.read_text(encoding="utf-8").split()


def use(monkeypatch, tmp_path, data: dict) -> Fake:
    """Point the fake plan at a new file holding ``data``, and return it."""
    fake = Fake(tmp_path / "plan.json", tmp_path / "calls.txt")
    fake.set(data)
    monkeypatch.setenv("PYSRIM2_FAKE_PLAN", str(fake.plan_path))
    monkeypatch.setenv("PYSRIM2_FAKE_CALLS", str(fake.calls_path))
    return fake


def plan(components: list[str]) -> list[Step]:
    path = os.environ.get("PYSRIM2_FAKE_PLAN")
    if not path:
        raise AssertionError("the fake plan is not set up (see install_fakes.use)")
    table = json.loads(Path(path).read_text(encoding="utf-8"))
    steps: list[Step] = []
    for component in components:
        if component not in table:
            raise ComponentUnavailable(component, f"component {component} is not available yet")
        steps.extend(_step(spec) for spec in table[component])
    return steps


def _record(name: str) -> None:
    calls = os.environ.get("PYSRIM2_FAKE_CALLS")
    if calls:
        with open(calls, "a", encoding="utf-8") as handle:
            handle.write(name + "\n")


def _step(spec: dict) -> Step:
    name = spec["name"]
    action = spec.get("action", "ok")
    commands = list(spec.get("commands", ["do it"]))

    def run() -> None:
        _record(name)
        if action == "print":
            print("python says hi")
            subprocess.run([sys.executable, "-c", "print('child says hi')"], check=True)
        elif action == "fail":
            raise OSError("disk full")
        elif action == "sleep":
            time.sleep(2.0)
        elif action == "crash":
            os._exit(3)

    return Step(
        name=name,
        summary=spec.get("summary", f"{name} summary"),
        check=lambda: bool(spec.get("done", False)),
        commands=lambda: list(commands),
        run=run,
        needs_admin=bool(spec.get("admin", False)),
    )
