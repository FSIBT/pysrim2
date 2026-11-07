"""The dashboard view of a running scan, drawn from a scripted event stream."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from _live import screenshot  # noqa: E402  (the values are illustrative)

TITLE = "The scan dashboard (--ui dashboard)"
NEEDS_SRIM = False
OUTPUT = "tui_dashboard.svg"


def make(outdir):
    return screenshot("dashboard", Path(outdir), OUTPUT)
