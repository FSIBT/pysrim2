"""What the app's setup view says: one line per thing that is not ready.

``target`` names where a click on the line goes. This plan has only the setup view
(``"setup"``).
"""

from dataclasses import dataclass


@dataclass(frozen=True)
class Guidance:
    text: str
    target: str | None = None


def setup_guidance(checks: list[dict], route: str | None) -> list[Guidance]:
    """Return what to tell the user, given the doctor ``checks`` and the chosen ``route``."""
    if route is None:
        return [Guidance("Choose how SRIM runs: open the setup", "setup")]
    return [
        Guidance(f"{check['name']}: {check['detail']}. Fix: {check['fix']}", "setup")
        for check in checks
        if not check["ok"]
    ]
