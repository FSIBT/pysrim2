"""Mean depth of B ions in Si against the energy, one line per angle: a parameter scan.

The base run and the seed are those of ``examples/scripts/angle_energy_scan.py``; this entry
scans more energies (10 to 50 keV) at two angles (0 and 45 degrees), ten points on two worker
processes, a quick Kinchin-Pease run each. Needs SRIM.
"""

import importlib.util
import tempfile
from pathlib import Path

import matplotlib.pyplot as plt

from srim.scan import Scan, run_scan

TITLE = "A parameter scan: mean depth against energy, one line per angle"
NEEDS_SRIM = True
OUTPUT = "scan_plot.png"

ROOT = Path(__file__).resolve().parents[2]

# 200 ions per point instead of the example's IONS, so that the ten points stay a quick run.
IONS = 200


def _example(name):
    path = ROOT / "examples" / "scripts" / f"{name}.py"
    spec = importlib.util.spec_from_file_location(f"gallery_example_{name}", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def make(outdir):
    example = _example("angle_energy_scan")
    scan = Scan(
        example.base_spec(),
        grid={"ion.energy_keV": [10.0, 20.0, 30.0, 40.0, 50.0], "ion.angle_deg": [0.0, 45.0]},
        extract=["implant_summary"],
        ions=IONS,
        seed=example.SEED,
    )
    fig, ax = plt.subplots(figsize=(6, 4), dpi=100)
    with tempfile.TemporaryDirectory() as tmp:
        result = run_scan(scan, workers=2, scan_dir=Path(tmp) / "scan")
        result.plot(x="ion.energy_keV", y="mean_depth_A", ax=ax)
    handles, labels = ax.get_legend_handles_labels()
    angles = [f"{float(label.split('=')[1]):g}°" for label in labels]
    ax.legend(handles, angles, title="angle")
    ax.set_xlabel("ion energy (keV)")
    ax.set_ylabel("mean depth (Å)")
    ax.set_title("B into 1000 nm of Si")

    path = Path(outdir) / OUTPUT
    fig.savefig(path, bbox_inches="tight")
    plt.close(fig)
    return path
