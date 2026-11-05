"""Full-cascade damage profile through a three-layer target, written to CSV.

A 100 keV Ni ion hits 50 nm Au on 500 nm Si on an Al substrate. TRIM runs a
"full cascade" calculation (``calculation=2``), which is slow, so the default is
only 20 ions; raise it for smooth profiles.

    python multilayer_damage.py [number_of_ions] [output_folder]

The vacancy profile (vacancies per angstrom per ion by depth: ``results.vacancy.df``
plus a total) is written to
``<output_folder>/vacancy_profile.csv`` (default ``./output``), together with
copies of the SRIM output files.
"""

import sys
from pathlib import Path

import pandas as pd

from srim import TRIM, Ion, Layer, Target

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

    # results.vacancy.df: depth_A, knock_ons_per_A_per_ion (vacancies made by the ion itself)
    # and one vacancies_<element>_per_A_per_ion column per element, in SRIM's order.
    profile = results.vacancy.df
    vacancy_columns = [c for c in profile.columns if c != "depth_A"]
    profile["total"] = profile[vacancy_columns].sum(axis=1)
    profile.to_csv(outdir / "vacancy_profile.csv", index=False)

    # Each run happens in its own folder (results.directory); keep copies of what you need.
    TRIM.copy_output_files(results.directory, outdir)
    return profile


if __name__ == "__main__":
    n = int(sys.argv[1]) if len(sys.argv) > 1 else 20
    out = sys.argv[2] if len(sys.argv) > 2 else "output"
    table = run(n, out)
    peak = table.loc[table["total"].idxmax()]
    print(f"peak damage {peak['total']:.3g} vacancies/(angstrom ion) at {peak['depth_A']:.0f} A")
    print(f"wrote {Path(out) / 'vacancy_profile.csv'}")
