"""The live plot view of a running scan, drawn from a scripted event stream."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from _live import screenshot  # noqa: E402  (the values are illustrative)

TITLE = "The live plot of a running scan (pysrim2 scan run --ui plot)"
NEEDS_SRIM = False
OUTPUT = "tui_plot.svg"


def make(outdir):
    return screenshot("plot", Path(outdir), OUTPUT)
