"""The ask-first limits of the agent (Qt-free): when a run needs the user's OK first.

A run needs the OK when it is estimated to take longer than the ``agent_max_minutes`` setting,
or when it wants more workers than ``agent_max_core_fraction`` of the cores. The limits are the
app's settings (``srim/app/settings.py``), so the window and headless mode agree.
"""

import math
from dataclasses import dataclass

from srim.app.settings import AppSettings


@dataclass(frozen=True)
class RunRequest:
    """A run the agent wants to start: its estimated ``seconds``, its ``workers``, the machine's
    ``cores`` and the SRIM runs (``units``) it is split into."""

    seconds: float
    workers: int
    cores: int
    units: int


def default_workers(units: int, cores: int) -> int:
    """The workers of a run that the server picks: half the cores, at most one per unit."""
    return max(1, min(units, cores // 2))


def core_limit(cores: int, fraction: float) -> int:
    """The most workers the ``fraction`` of ``cores`` allows, at least one."""
    return max(1, math.floor(cores * fraction))


def reasons_to_ask(request: RunRequest, settings: AppSettings) -> list[str]:
    """The reasons the user's OK is needed for ``request``, in words for the user.

    An empty list means the run starts without asking.
    """
    reasons = []
    limit_seconds = settings.agent_max_minutes * 60
    if request.seconds > limit_seconds:
        reasons.append(
            f"it is estimated to take about {_duration(request.seconds)} "
            f"(limit {_duration(limit_seconds)})"
        )
    limit = core_limit(request.cores, settings.agent_max_core_fraction)
    if request.workers > limit:
        reasons.append(f"it uses {request.workers} of {request.cores} cores (limit {limit})")
    return reasons


def _duration(seconds: float) -> str:
    """Minutes rounded up below two hours, else hours with one decimal (rounded up)."""
    if seconds < 2 * 3600:
        return f"{math.ceil(seconds / 60)} min"
    return f"{math.ceil(seconds / 360) / 10:.1f} h"
