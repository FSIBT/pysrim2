"""Stopping power and range table of an ion in a material (SRIM's SR module).

    python stopping_table.py [output_folder]

Prints the table for 10 keV to 1 MeV He in Si and writes it to
``<output_folder>/stopping_table.csv`` (default ``./output``). SR needs no ion
histories, so it is fast. Energies are in keV and lengths in micrometre;
stopping is in the units reported by ``results.units``.
"""

import sys
from pathlib import Path

import pandas as pd

from srim import SR, Ion, Layer

COLUMNS = [
    "energy_keV",
    "electronic_stopping",
    "nuclear_stopping",
    "projected_range_um",
    "longitudinal_straggling_um",
    "lateral_straggling_um",
]


def run(outdir="output") -> pd.DataFrame:
    """Run SR, write ``stopping_table.csv`` in ``outdir`` and return the table."""
    outdir = Path(outdir)
    outdir.mkdir(parents=True, exist_ok=True)
    layer = Layer.from_formula("Si", density=2.33, width=10000.0)
    ion = Ion("He", energy=1.0e6)  # eV, the top of the table
    results = SR(layer, ion, energy_min=10e3, output_type=1).run()  # output_type 1 = eV/Angstrom
    table = pd.DataFrame(results.data.T, columns=COLUMNS)
    table.attrs["units"] = results.units
    table.to_csv(outdir / "stopping_table.csv", index=False)
    return table


if __name__ == "__main__":
    out = sys.argv[1] if len(sys.argv) > 1 else "output"
    table = run(out)
    print(f"stopping units: {table.attrs['units']}")
    print(table.to_string(index=False))
