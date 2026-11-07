"""The agent reference case: where 200 boron ions stop in 100 nm of gold on silicon.

The run is the one of ``examples/scripts/boron_in_gold_on_silicon.py`` (20 keV, 7 degrees,
seed 12345). The histogram is drawn from ``RANGE_3D.txt``. Needs SRIM.
"""

import importlib.util
import tempfile
from pathlib import Path

import matplotlib.pyplot as plt

from srim.output import Range3D

TITLE = "The agent example: 200 boron ions into gold on silicon"
NEEDS_SRIM = True
OUTPUT = "agent_boron.png"

ROOT = Path(__file__).resolve().parents[2]
INTERFACE_A = 1000.0  # the Au layer is 100 nm = 1000 angstrom


def _example(name):
    path = ROOT / "examples" / "scripts" / f"{name}.py"
    spec = importlib.util.spec_from_file_location(f"gallery_example_{name}", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def make(outdir):
    boron = _example("boron_in_gold_on_silicon")
    with tempfile.TemporaryDirectory() as tmp:
        summary = boron.run(200, Path(tmp) / "out")
        depth = Range3D(str(Path(tmp) / "out")).df["depth_A"]
    mean = summary["mean_depth_A"]
    fig, ax = plt.subplots(figsize=(6, 4), dpi=100)
    ax.hist(depth, bins=50, color="0.6")
    ax.axvline(INTERFACE_A, color="C1", label="Au / Si interface (100 nm)")
    ax.axvline(mean, color="C0", label=f"mean depth {mean:.0f} Å")
    ax.legend()
    ax.set_xlabel("depth below the surface (Å)")
    ax.set_ylabel("ions per bin")
    ax.set_title("20 keV B, 7°, seed 12345")

    path = Path(outdir) / OUTPUT
    fig.savefig(path, bbox_inches="tight")
    plt.close(fig)
    return path
