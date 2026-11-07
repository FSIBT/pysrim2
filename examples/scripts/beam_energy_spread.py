"""Energy spread of a beam: 20 keV boron with a Gaussian spread of 1 keV, run two ways.

    python beam_energy_spread.py [output_folder]

The exact run samples 200 ions and runs SRIM on exactly those ions (TRIM.DAT). The binned
run splits 200 ions over 4 energy bins, one SRIM run per bin. The script prints the projected
range and straggling of both runs and writes ``beam_energy_spread.csv`` to the output folder
(default ``./output``). The ion counts are small so that the script runs quickly.
"""

import sys
from pathlib import Path

import pandas as pd

from srim import TRIM, Ion, Layer, Target
from srim.analysis import implant_summary
from srim.beam import Gaussian, run_beam
from srim.spec import RunSpec

IONS = 200
MODES = ("exact", "binned")
SUMMARY = ("n_ions", "mean_depth_A", "straggle_depth_A", "lateral_straggle_A")


def beam_spec(mode: str, ions: int = IONS) -> RunSpec:
    """The run spec: B into 1000 nm of Si, energy 20 keV with a 1 keV Gaussian spread."""
    target = Target([Layer.from_formula("Si", density=2.33, width=10000.0)])  # 1000 nm
    trim = TRIM(
        target,
        Ion("B", energy=20e3),  # eV; replaced by the distribution below
        number_ions=ions,
        calculation=1,  # quick Kinchin-Pease
        ranges=1,  # RANGE_3D.txt, needed by implant_summary
        random_seed=12345,
    )
    data = trim.to_spec().to_dict()
    data["ion"]["energy_keV"] = Gaussian(20.0, 1.0).to_dict()
    if mode == "binned":
        data["beam"] = {"mode": "binned", "bins": {"energy_keV": 4}}
    return RunSpec.from_dict(data)


def run(outdir="output", ions=IONS) -> pd.DataFrame:
    """Run both modes, write ``beam_energy_spread.csv`` in ``outdir`` and return the table."""
    outdir = Path(outdir)
    outdir.mkdir(parents=True, exist_ok=True)
    rows = []
    for mode in MODES:
        with run_beam(beam_spec(mode, ions), output_dir=outdir / mode) as results:
            summary = implant_summary(results.range3d.df)
        rows.append({"mode": mode, **{key: summary[key] for key in SUMMARY}})
    table = pd.DataFrame(rows)
    table.to_csv(outdir / "beam_energy_spread.csv", index=False)
    return table


if __name__ == "__main__":  # required: binned runs use worker processes that import this file
    out = sys.argv[1] if len(sys.argv) > 1 else "output"
    print(run(out).to_string(index=False))
