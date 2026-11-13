"""The desktop app's Results tab: the ionization profile of a finished run.

The run is 3 MeV Au into SiC, the SRIM outputs of test_files/2 (a full cascade), opened as a
run folder would be. No SRIM run is made. It needs the app extra (PySide6); the window runs
offscreen.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from _app_shots import grab_results  # noqa: E402

TITLE = "The desktop app's Results tab: ionization against depth of a finished run"
NEEDS_SRIM = False
OUTPUT = "app_results.png"


def make(outdir):
    return grab_results(Path(outdir) / OUTPUT)
