"""B ions into 100 nm Au on a Si substrate: depth, lateral and radial straggling from RANGE_3D.txt.

Run natively (``python boron_in_gold_on_silicon.py``) or in the container (needs Podman ≥ 4.3)::

    podman run --rm --userns=keep-id:uid=1000,gid=1000 \
        -v "$PWD:/data:Z" ghcr.io/fsibt/pysrim2:<tag> \
        python /data/boron_in_gold_on_silicon.py

Quick Kinchin-Pease with 200 ions takes seconds (about a minute under QEMU). With
seed 12345 the mean depth is about 282 angstrom. Copies of the SRIM output files
are written to ``./output`` so they survive the container.

Per-ion final positions (``RANGE_3D.txt``) are ``results.range3d.df``; the summary
comes from ``srim.analysis.implant_summary``.
"""

import shutil
import sys
from pathlib import Path

from srim import TRIM, Ion, Layer, Target
from srim.analysis import implant_summary
from srim.paths import srim_dir


def peak_vacancy_depth(results) -> float:
    """Depth (angstrom) where the total vacancy production per ion peaks."""
    vac = results.vacancy
    total = vac.knock_ons + vac.vacancies.sum(axis=1)
    return float(vac.depth[total.argmax()])


def run(number_ions: int = 200, outdir=None) -> dict:
    """Run TRIM and return SRIM-style range statistics (angstrom)."""
    target = Target(
        [
            Layer.from_formula("Au", density=19.3, width=1000.0),  # 100 nm
            Layer.from_formula("Si", density=2.33, width=10000.0),  # substrate
        ]
    )
    ion = Ion("B", energy=20e3)  # eV
    results = TRIM(
        target,
        ion,
        number_ions=number_ions,
        calculation=1,  # quick Kinchin-Pease; 2 = full cascade (much slower)
        angle_ions=7.0,
        ranges=1,  # write RANGE_3D.txt
        random_seed=12345,
    ).run()
    summary = implant_summary(results.range3d.df)
    if outdir is not None:
        outdir = Path(outdir)
        outdir.mkdir(parents=True, exist_ok=True)
        for name in ("RANGE_3D.txt", "RANGE.txt", "VACANCY.txt", "IONIZ.txt"):
            for src in (srim_dir() / name, srim_dir() / "SRIM Outputs" / name):
                if src.is_file():
                    shutil.copy(src, outdir / name)
                    break
    return {
        "mean_depth_A": summary["mean_depth_A"],
        "straggle_depth_A": summary["straggle_depth_A"],
        "lateral_straggle_A": summary["lateral_straggle_A"],
        "radial_straggle_A": summary["radial_straggle_A"],
        "ions": summary["n_ions"],
        "peak_vacancy_depth_A": peak_vacancy_depth(results),
    }


if __name__ == "__main__":
    n = int(sys.argv[1]) if len(sys.argv) > 1 else 200
    for key, value in run(n, Path.cwd() / "output").items():
        print(f"{key}: {value:.1f}")
