"""A beam with a Gaussian energy spread: the ions SRIM ran (exact mode), drawn over the density.

The beam is the one of ``examples/scripts/beam_energy_spread.py``: 2000 B ions at 20 keV with
a 1 keV Gaussian spread. Needs SRIM.
"""

import importlib.util
import tempfile
from pathlib import Path

import matplotlib.pyplot as plt

from srim.beam import plot_beam, run_beam

TITLE = "A beam with a 1 keV Gaussian energy spread (exact mode)"
NEEDS_SRIM = True
OUTPUT = "beam.png"

ROOT = Path(__file__).resolve().parents[2]

IONS = 2000


def _example(name):
    path = ROOT / "examples" / "scripts" / f"{name}.py"
    spec = importlib.util.spec_from_file_location(f"gallery_example_{name}", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def make(outdir):
    spec = _example("beam_energy_spread").beam_spec("exact", IONS)
    fig, ax = plt.subplots(figsize=(6, 4), dpi=100)
    with tempfile.TemporaryDirectory() as tmp:
        with run_beam(spec, output_dir=Path(tmp) / "exact") as results:
            plot_beam(results, "energy_keV", ax=ax, bins=40)
    ax.set_xlabel("ion energy (keV)")
    ax.set_ylabel("probability density (1/keV)")
    ax.set_title("B at 20 keV into 1000 nm of Si")

    path = Path(outdir) / OUTPUT
    fig.savefig(path, bbox_inches="tight")
    plt.close(fig)
    return path
