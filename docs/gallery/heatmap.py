"""Where the ions of a 3D range run stop in the target."""

from pathlib import Path

import matplotlib.pyplot as plt
import numpy as np
from matplotlib.colors import LogNorm

from srim.output import Range3D

TITLE = "Where 1000 nickel ions stop in nickel (RANGE_3D.txt)"
NEEDS_SRIM = False
OUTPUT = "heatmap.png"

FIXTURE = Path(__file__).resolve().parents[2] / "test_files" / "1"


def make(outdir):
    df = Range3D(str(FIXTURE)).df
    depth_um = df["depth_A"] / 1e4
    lateral_um = np.hypot(df["y_A"], df["z_A"]) / 1e4

    fig, ax = plt.subplots(figsize=(6, 4), dpi=100)
    _, _, _, image = ax.hist2d(depth_um, lateral_um, bins=100, norm=LogNorm())
    fig.colorbar(image, ax=ax, label="ions per bin")
    ax.set_xlabel("depth along the beam (µm)")
    ax.set_ylabel("lateral distance √(y² + z²) (µm)")
    ax.set_title("5 MeV Ni into Ni")

    path = Path(outdir) / OUTPUT
    fig.savefig(path, bbox_inches="tight")
    plt.close(fig)
    return path
