"""Full-cascade damage profile through a three-layer target, written to CSV.

A 100 keV Ni ion hits 50 nm Au on 500 nm Si on an Al substrate. TRIM runs a
"full cascade" calculation (``calculation=2``), which is slow, so the default is
only 20 ions; raise it for smooth profiles.

    python multilayer_damage.py [number_of_ions] [output_folder]

The vacancy profile (vacancies per angstrom per ion, by depth) is written to
``<output_folder>/vacancy_profile.csv`` (default ``./output``), together with
copies of the SRIM output files.
"""

import sys
from pathlib import Path

import pandas as pd

from srim import TRIM, Ion, Layer, Target
from srim.paths import srim_dir

LAYERS = [
    Layer.from_formula("Au", density=19.3, width=500.0),  # 50 nm
    Layer.from_formula("Si", density=2.33, width=5000.0),  # 500 nm
    Layer.from_formula("Al", density=2.7, width=20000.0),  # substrate
]


def run(number_ions: int = 20, outdir="output") -> pd.DataFrame:
    """Run TRIM, write ``vacancy_profile.csv`` in ``outdir`` and return the profile."""
    outdir = Path(outdir)
    outdir.mkdir(parents=True, exist_ok=True)
    results = TRIM(
        Target(LAYERS),
        Ion("Ni", energy=100e3),  # eV
        number_ions=number_ions,
        calculation=2,  # full cascade; 1 = quick Kinchin-Pease
        random_seed=12345,
    ).run()

    vac = results.vacancy
    # One column per element in the target, in the order SRIM lists them
    # (here: Au, Si, Al), plus the vacancies made by the ion itself.
    names = [f"vacancies_{name}" for name in ("Au", "Si", "Al")]
    profile = pd.DataFrame(vac.vacancies, columns=names)
    profile.insert(0, "from_ion", vac.knock_ons)
    profile.insert(0, "depth_A", vac.depth)
    profile["total"] = profile[["from_ion", *names]].sum(axis=1)
    profile.to_csv(outdir / "vacancy_profile.csv", index=False)

    # SRIM writes inside its own folder; keep copies of what you need next to the script.
    TRIM.copy_output_files(srim_dir(), outdir)
    return profile


if __name__ == "__main__":
    n = int(sys.argv[1]) if len(sys.argv) > 1 else 20
    out = sys.argv[2] if len(sys.argv) > 2 else "output"
    table = run(n, out)
    peak = table.loc[table["total"].idxmax()]
    print(f"peak damage {peak['total']:.3g} vacancies/(angstrom ion) at {peak['depth_A']:.0f} A")
    print(f"wrote {Path(out) / 'vacancy_profile.csv'}")
