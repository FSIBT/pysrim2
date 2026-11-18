"""Installing one component in its own process, for the MCP server (Qt-free).

The server's stdout carries the MCP protocol, and its other threads keep writing to it while an
install runs, so an install never runs in the server process. :func:`run_install` starts
``python -m srim.mcp.install_child`` with stdin from the null device and its stdout and stderr in
a file (files, not pipes), and reads back a JSON result the child writes. The child runs
:func:`srim.installer.runner.run_steps` exactly as ``pysrim2 install`` does (``yes=False``,
``confirm=None``), so an admin step is never run; its commands come back to the caller.

The steps the user was shown are checked in the child: a plan that differs from them is refused
before anything runs. ``PYSRIM2_INSTALL_PLANNER=module:function`` names the planner the child
uses in place of the installer's registry (tests use fake steps this way, as the scan runner does).
"""

import importlib
import json
import os
import subprocess
import sys
import tempfile
from collections.abc import Callable
from pathlib import Path
from typing import Any

from srim.installer import steps as installer_steps
from srim.installer.runner import run_steps
from srim.installer.steps import Step
from srim.mcp.backend import BackendError

PLANNER_VAR = "PYSRIM2_INSTALL_PLANNER"
#: the output lines a crash message keeps: the last of the child's log
TAIL_LINES = 20


def run_install(component: str, expected: list[dict] | None = None) -> dict:
    """Install ``component`` in a child process and return the outcome.

    ``expected`` is the list of steps the caller showed the user (``{"step", "commands"}`` each,
    the non-admin steps that would run); when given, the child refuses a different plan. Returns
    ``{"component", "ran", "commands", "failed", "interrupted", "log"}``. Raises
    :class:`BackendError`, with the output of the child, when the child could not report.
    """
    with tempfile.TemporaryDirectory(prefix="pysrim2-install-") as folder:
        request_path = Path(folder) / "request.json"
        result_path = Path(folder) / "result.json"
        output_path = Path(folder) / "output.txt"
        request = {"component": component, "expected": expected}
        request_path.write_text(json.dumps(request), encoding="utf-8")
        code = _start_child(request_path, result_path, output_path, component)
        output = output_path.read_text(encoding="utf-8", errors="replace")
        log = [line for line in output.splitlines() if line.strip()]
        if not result_path.is_file():
            tail = "\n".join(log[-TAIL_LINES:]) or "(no output)"
            raise BackendError(
                f"the install of {component} stopped unexpectedly (exit code {code}). "
                f"Its output:\n{tail}"
            )
        result = json.loads(result_path.read_text(encoding="utf-8"))
    if "refused" in result:
        raise BackendError(result["refused"])
    if "error" in result:
        raise BackendError(f"the install of {component} failed: {result['error']}")
    return {
        "component": component,
        "ran": result["ran"],
        "commands": result["commands"],
        "failed": result["failed"],
        "interrupted": result["interrupted"],
        "log": log,
    }


def _start_child(request: Path, result: Path, output: Path, component: str) -> int:
    """Run the child to its end; its output goes to ``output``. Returns its exit code."""
    env = dict(os.environ)
    # the child imports what this process imports (a test's fake steps among them)
    env["PYTHONPATH"] = os.pathsep.join(path for path in sys.path if path)
    command = [sys.executable, "-m", "srim.mcp.install_child", str(request), str(result)]
    try:
        with open(os.devnull, "rb") as stdin, open(output, "wb") as sink:
            done = subprocess.run(
                command,
                stdin=stdin,
                stdout=sink,
                stderr=subprocess.STDOUT,
                env=env,
                check=False,
            )
    except OSError as error:
        raise BackendError(f"could not start the install of {component}: {error}") from error
    return done.returncode


def main(argv: list[str] | None = None) -> int:
    """The child: ``install_child REQUEST RESULT``. Writes the JSON result and returns 0."""
    args = sys.argv[1:] if argv is None else argv
    request_path, result_path = Path(args[0]), Path(args[1])
    request = json.loads(request_path.read_text(encoding="utf-8"))
    outcome = _execute(request)
    temporary = result_path.with_name(result_path.name + ".tmp")
    temporary.write_text(json.dumps(outcome), encoding="utf-8")
    os.replace(temporary, result_path)
    return 0


def _execute(request: dict) -> dict:
    """What the install of the request's component does, as the JSON result."""
    component = request["component"]
    try:
        steps = _planner()([component])
        expected = request.get("expected")
        if expected is not None:
            shown = [{"step": row["step"], "commands": list(row["commands"])} for row in expected]
            found = would_run(steps)
            if found != shown:
                return {
                    "refused": (
                        f"the install plan of {component} changed since it was shown: it was "
                        f"{shown}, now it is {found}. Ask the user again with the new plan."
                    )
                }
        outcome = run_steps(
            steps,
            yes=False,
            confirm=None,
            say=_say,
            warn=_warn,
            admin_hint="run these in a terminal yourself, then check again:",
        )
    except Exception as error:  # a plan that cannot be made, or an install that cannot run
        return {"error": str(error)}
    commands = [command for _, owed in outcome.admin_skipped for command in owed]
    return {
        "ran": list(outcome.installed),
        "commands": commands,
        "failed": outcome.failed,
        "interrupted": outcome.interrupted,
    }


def would_run(steps: list[Step]) -> list[dict]:
    """The steps that would run: no admin ones, and none already installed."""
    return [
        {"step": step.name, "commands": list(step.commands())}
        for step in steps
        if not step.needs_admin and not step.check()
    ]


def _planner() -> Callable[[list[str]], list[Step]]:
    name = os.environ.get(PLANNER_VAR)
    if not name:
        return installer_steps.plan
    module_name, _, attribute = name.partition(":")
    if not module_name or not attribute:
        raise ValueError(f"{PLANNER_VAR} must look like module:function, got {name!r}")
    planner: Any = getattr(importlib.import_module(module_name), attribute)
    return planner


def _say(line: str) -> None:
    print(line, flush=True)


def _warn(line: str) -> None:
    print(line, file=sys.stderr, flush=True)


if __name__ == "__main__":
    sys.exit(main())
