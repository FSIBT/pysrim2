"""Where the ions of a 3D range run stop in the target: 10,000 ions, a quick Kinchin-Pease run.

``ranges=1`` makes TRIM write ``RANGE_3D.txt``, the final position of every ion. Needs SRIM.
"""

from pathlib import Path

import matplotlib.pyplot as plt
import numpy as np
from matplotlib.colors import LogNorm

from srim import TRIM, Ion, Layer, Target

TITLE = "Where 10,000 nickel ions at 5 MeV stop in nickel (RANGE_3D.txt)"
NEEDS_SRIM = True
OUTPUT = "heatmap.png"

IONS = 10_000


def make(outdir):
    target = Target([Layer.from_formula("Ni", density=8.9, width=40000.0)])  # 4 um
    trim = TRIM(target, Ion("Ni", energy=5e6), number_ions=IONS, ranges=1, random_seed=12345)
    df = trim.run().range3d.df
    depth_um = df["depth_A"] / 1e4
    lateral_um = np.hypot(df["y_A"], df["z_A"]) / 1e4

    fig, ax = plt.subplots(figsize=(6, 4), dpi=100)
    _, _, _, image = ax.hist2d(depth_um, lateral_um, bins=60, norm=LogNorm())
    fig.colorbar(image, ax=ax, label="ions per bin")
    ax.set_xlabel("depth along the beam (µm)")
    ax.set_ylabel("lateral distance √(y² + z²) (µm)")
    ax.set_title(f"5 MeV Ni into Ni, {IONS:,} ions")

    path = Path(outdir) / OUTPUT
    fig.savefig(path, bbox_inches="tight")
    plt.close(fig)
    return path
