"""The desktop app on the boron reference setup: the Geometry tab, with the target to scale.

The setup is the boron reference case of docs/first-simulation.md, built here in code: 200 B
ions at 20 keV and 7 degrees into 100 nm of Au on Si, seed 12345. It needs the app extra
(PySide6); the window runs offscreen, with no install or check.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from _app_shots import grab_setup  # noqa: E402

TITLE = "The desktop app: the boron reference setup on the Geometry tab"
NEEDS_SRIM = False
OUTPUT = "app_setup.png"

SETUP = {
    "version": 1,
    "ion": {"symbol": "B", "energy_keV": 20.0, "angle_deg": 7.0},
    "number_ions": 200,
    "calculation": 1,
    "seed": 12345,
    "settings": {},
    "target": [
        {
            "name": "gold",
            "thickness_nm": 100.0,
            "density_g_cm3": 19.3,
            "phase": 0,
            "elements": [
                {
                    "symbol": "Au",
                    "stoich": 1.0,
                    "displacement_eV": 25.0,
                    "lattice_eV": 3.0,
                    "surface_eV": 3.0,
                }
            ],
        },
        {
            "name": "silicon",
            "thickness_nm": 1000.0,
            "density_g_cm3": 2.33,
            "phase": 0,
            "elements": [
                {
                    "symbol": "Si",
                    "stoich": 1.0,
                    "displacement_eV": 15.0,
                    "lattice_eV": 2.0,
                    "surface_eV": 4.7,
                }
            ],
        },
    ],
}


def make(outdir):
    return grab_setup(SETUP, Path(outdir) / OUTPUT)
