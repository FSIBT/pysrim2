"""Mean depth of B ions in Si against the energy, one line per angle: a parameter scan.

The scan is the one of ``examples/scripts/angle_energy_scan.py`` (four points on two worker
processes, a quick Kinchin-Pease run each). Needs SRIM.
"""

import importlib.util
import tempfile
from pathlib import Path

import matplotlib.pyplot as plt

from srim.scan import run_scan

TITLE = "A parameter scan: mean depth against energy, one line per angle"
NEEDS_SRIM = True
OUTPUT = "scan_plot.png"

ROOT = Path(__file__).resolve().parents[2]


def _example(name):
    path = ROOT / "examples" / "scripts" / f"{name}.py"
    spec = importlib.util.spec_from_file_location(f"gallery_example_{name}", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def make(outdir):
    scan = _example("angle_energy_scan").make_scan()
    fig, ax = plt.subplots(figsize=(6, 4), dpi=100)
    with tempfile.TemporaryDirectory() as tmp:
        result = run_scan(scan, workers=2, scan_dir=Path(tmp) / "scan")
        result.plot(x="ion.energy_keV", y="mean_depth_A", ax=ax)
    ax.set_xlabel("ion energy (keV)")
    ax.set_ylabel("mean depth (Å)")
    ax.set_title("B into 1000 nm of Si")

    path = Path(outdir) / OUTPUT
    fig.savefig(path, bbox_inches="tight")
    plt.close(fig)
    return path
