"""B ions into 100 nm Au on a Si substrate: depth and lateral straggle from RANGE_3D.txt.

Run natively (``python boron_in_gold_on_silicon.py``) or in the container::

    podman run --rm --userns=keep-id:uid=1000,gid=1000 \
        -v "$PWD:/data:Z" ghcr.io/fsibt/pysrim2:<tag> \
        python /data/boron_in_gold_on_silicon.py

Quick Kinchin-Pease with 200 ions takes seconds (about a minute under QEMU). With
seed 12345 the mean depth is about 282 angstrom. Copies of the SRIM output files
are written to ``./output`` so they survive the container.

Until pysrim2 0.8.0 adds DataFrames to ``Results``, per-ion final positions come
from ``RANGE_3D.txt``, parsed by ``read_range_3d`` below.
"""

import re
import shutil
import sys
from pathlib import Path

import pandas as pd

from srim import TRIM, Ion, Layer, Target
from srim.paths import srim_dir

ION_LINE = re.compile(r"\d{7}")


def read_range_3d(directory) -> pd.DataFrame:
    """Parse RANGE_3D.txt into a DataFrame with columns ion, depth_A, y_A, z_A."""
    directory = Path(directory)
    path = next(
        p
        for p in (directory / "RANGE_3D.txt", directory / "SRIM Outputs" / "RANGE_3D.txt")
        if p.is_file()
    )
    rows = []
    for line in path.read_bytes().decode("latin-1").splitlines():
        parts = line.split()
        if len(parts) == 4 and ION_LINE.fullmatch(parts[0]):
            rows.append((int(parts[0]), *map(float, parts[1:])))
    if not rows:
        raise ValueError(f"no ion positions found in {path}")
    return pd.DataFrame(rows, columns=["ion", "depth_A", "y_A", "z_A"])


def lateral_straggle(df: pd.DataFrame) -> float:
    """Standard deviation (angstrom) of the lateral distance from the beam axis."""
    return float((df["y_A"] ** 2 + df["z_A"] ** 2).mean() ** 0.5)


def peak_vacancy_depth(results) -> float:
    """Depth (angstrom) where the total vacancy production per ion peaks."""
    vac = results.vacancy
    total = vac.knock_ons + vac.vacancies.sum(axis=1)
    return float(vac.depth[total.argmax()])


def run(number_ions: int = 200, outdir=None) -> dict:
    """Run TRIM and return mean depth and lateral straggle (angstrom)."""
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
    df = read_range_3d(srim_dir())
    if outdir is not None:
        outdir = Path(outdir)
        outdir.mkdir(parents=True, exist_ok=True)
        for name in ("RANGE_3D.txt", "RANGE.txt", "VACANCY.txt", "IONIZ.txt"):
            for src in (srim_dir() / name, srim_dir() / "SRIM Outputs" / name):
                if src.is_file():
                    shutil.copy(src, outdir / name)
                    break
    return {
        "mean_depth_A": float(df["depth_A"].mean()),
        "lateral_straggle_A": lateral_straggle(df),
        "ions": len(df),
        "peak_vacancy_depth_A": peak_vacancy_depth(results),
    }


if __name__ == "__main__":
    n = int(sys.argv[1]) if len(sys.argv) > 1 else 200
    for key, value in run(n, Path.cwd() / "output").items():
        print(f"{key}: {value:.1f}")
