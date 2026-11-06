"""Scan the energy and the angle of B ions in Si: range statistics for each point.

Four points (2 energies x 2 angles), 50 ions each, on two worker processes. Every point is
a quick Kinchin-Pease run with ``ranges=1`` (``RANGE_3D.txt``), and the extractor
``implant_summary`` gives its depth and straggling. The same scan is written as a file in
``scan.yaml`` (``pysrim2 scan run scan.yaml --ui plain``); a test keeps the two equal.

    python angle_energy_scan.py [scan_folder]

The scan folder (default ``./output/scan``) holds one folder per point, the cache of the
runs, and ``result/scan.csv`` with one row per point. Run it again and no SRIM run is
computed twice: the runs are in the cache.
"""

import sys

from srim import TRIM, Ion, Layer, Target
from srim.scan import Scan, run_scan
from srim.ui import PlainProgress

IONS = 50  # ions per point
SEED = 12345  # base seed of the scan: the same numbers on every run of this script


def base_spec():
    """The run every point starts from: B at 20 keV into 1000 nm of Si (a thick target)."""
    target = Target([Layer.from_formula("Si", density=2.33, width=10000.0)])  # 1000 nm
    trim = TRIM(
        target,
        Ion("B", energy=20e3),  # eV; the scan changes it
        number_ions=IONS,
        calculation=1,  # quick Kinchin-Pease
        ranges=1,  # RANGE_3D.txt: the final position of every ion
        random_seed=SEED,
    )
    return trim.to_spec()


def make_scan() -> Scan:
    """The scan: energies 20 and 40 keV, angles 0 and 30 degrees (a grid of four points)."""
    return Scan(
        base_spec(),
        grid={"ion.energy_keV": [20.0, 40.0], "ion.angle_deg": [0.0, 30.0]},
        extract=["implant_summary"],
        ions=IONS,
        seed=SEED,
    )


def run(scan_dir="output/scan", workers=2):
    """Run the scan into ``scan_dir``, print one status line per change, return the result."""
    return run_scan(make_scan(), workers=workers, scan_dir=scan_dir, on_event=PlainProgress())


if __name__ == "__main__":  # the guard is needed: worker processes import this file
    folder = sys.argv[1] if len(sys.argv) > 1 else "output/scan"
    result = run(folder)
    columns = [
        "ion.energy_keV",
        "ion.angle_deg",
        "status",
        "n_ions_stopped",
        "mean_depth_A",
        "straggle_depth_A",
        "lateral_straggle_A",
    ]
    print(result.table[columns].to_string(index=False))
    print(f"wrote {folder}/result/scan.csv")
