"""Fake SRIM runners for the app's run tests.

They are module-level functions, so the worker processes (started with ``spawn``) can import
them by name: this folder is on the path of the test process, and spawn passes that path on.
Each returns the outputs of ``test_files/1`` (a real SRIM result); the worker copies them, and
the fixture is only read.
"""

import os
import time
from pathlib import Path

from srim.output import Results

FIXTURE = Path(__file__).resolve().parents[2] / "test_files" / "1"
#: the environment variable that names the file a gated runner waits for
GATE_VAR = "PYSRIM2_TEST_GATE"
#: how long a runner waits before it gives up on its gate or on being stopped
SLOW_SECONDS = 30.0


def fixture_runner(spec):
    """The outputs of test_files/1, for any spec."""
    return Results(str(FIXTURE))


def failing_runner(spec):
    """Every run fails, as a crashed SRIM would."""
    raise RuntimeError("SRIM failed")


def slow_runner(spec):
    """Runs for up to 30 s in steps of 0.1 s. A stop ends it at once (the worker exits on
    SIGTERM), so it never has to wait out the 30 s."""
    deadline = time.monotonic() + SLOW_SECONDS
    while time.monotonic() < deadline:
        time.sleep(0.1)
    return fixture_runner(spec)


def gated_runner(spec):
    """Waits until the file named by ``PYSRIM2_TEST_GATE`` exists, then returns the fixture.

    The test creates the gate file when it wants the run to go on, so the point of the run
    that is going at that moment is known.
    """
    gate = Path(os.environ[GATE_VAR])
    deadline = time.monotonic() + SLOW_SECONDS
    while not gate.exists() and time.monotonic() < deadline:
        time.sleep(0.05)
    return fixture_runner(spec)
