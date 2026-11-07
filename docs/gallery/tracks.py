"""Where the collisions of 1000 carbon ions in SiC happen: a map of the full cascades.

``COLLISON.txt`` of a full-cascade run lists every collision of the ions and their recoils.
``Collision.iter_events()`` streams it, so even a file of many gigabytes needs little memory.
The data is the SRIM output committed in ``test_files/SiC-C`` (4.5 MeV C into 1 µm of SiC,
1000 ions), so no SRIM run is needed.
"""

from pathlib import Path

import matplotlib.pyplot as plt
import numpy as np
from matplotlib.colors import LogNorm

from srim.output import Collision

TITLE = "Collision cascades of 4.5 MeV carbon ions in SiC (COLLISON.txt)"
NEEDS_SRIM = False
OUTPUT = "tracks.png"

FIXTURE = Path(__file__).resolve().parents[2] / "test_files" / "SiC-C"


def make(outdir):
    events = Collision(str(FIXTURE)).iter_events()
    depth, lateral = np.array([(e["depth_A"], e["y_A"]) for e in events]).T

    fig, ax = plt.subplots(figsize=(8, 3.5), dpi=100)
    _, _, _, image = ax.hist2d(
        depth / 10, lateral / 10, bins=(250, 100), range=((0, 1000), (-100, 100)), norm=LogNorm()
    )
    fig.colorbar(image, ax=ax, label="collisions per bin")
    ax.set_xlabel("depth (nm)")
    ax.set_ylabel("lateral position y (nm)")
    ax.set_title("4.5 MeV C into SiC, 1000 ions, full cascades")

    path = Path(outdir) / OUTPUT
    fig.savefig(path, bbox_inches="tight")
    plt.close(fig)
    return path
