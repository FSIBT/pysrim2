"""Run the real TRIM and SR programs (``pytest -m srim``).

These are physics sanity checks, not exact numbers: SRIM is Monte Carlo.
"""

import re

import numpy as np

from srim import SR, TRIM, Ion, Layer, Target
from srim.output import Results, SRResults

# Mean ion depth in angstrom for 200 B ions at 20 keV, 7 degrees, into 100 nm Au on Si,
# with random_seed=12345 (wine 10.0, SRIM-2013; reproduced 282.0 in two container runs).
# Checked within +-30 %.
REFERENCE_MEAN_DEPTH_A = 282.0


def mean_depth_3d(directory):
    """Mean of the first coordinate (depth, angstrom) in ``RANGE_3D.txt``."""
    depths = []
    path = next(
        p
        for p in (directory / "RANGE_3D.txt", directory / "SRIM Outputs" / "RANGE_3D.txt")
        if p.is_file()
    )
    with open(path, "rb") as f:
        for line in f.read().decode("latin-1").splitlines():
            parts = line.split()
            if len(parts) == 4 and re.fullmatch(r"\d{7}", parts[0]):
                depths.append(float(parts[1]))
    assert depths, "no ion positions found in RANGE_3D.txt"
    return float(np.mean(depths))


def test_ni_in_ni_quick_kp():
    ion = Ion("Ni", energy=3.0e6)
    layer = Layer.from_formula("Ni", density=8.9, width=30000.0)
    results = TRIM(Target([layer]), ion, number_ions=10, calculation=1, random_seed=12345).run()
    assert isinstance(results, Results)
    assert results.range.ions.sum() > 0


def test_b_into_au_on_si():
    from srim.paths import srim_dir

    ion = Ion("B", energy=20e3)
    au = Layer.from_formula("Au", density=19.3, width=1000.0)
    si = Layer.from_formula("Si", density=2.33, width=10000.0)
    results = TRIM(
        Target([au, si]),
        ion,
        number_ions=200,
        calculation=1,
        angle_ions=7.0,
        ranges=1,
        random_seed=12345,
    ).run()
    assert results.range.depth.shape == (100,)
    assert results.range.ions.sum() > 0
    mean = mean_depth_3d(srim_dir())
    print(f"mean depth from RANGE_3D.txt: {mean:.1f} A")
    low, high = 0.7 * REFERENCE_MEAN_DEPTH_A, 1.3 * REFERENCE_MEAN_DEPTH_A
    assert low <= mean <= high


def test_sr_table_for_he_in_si():
    ion = Ion("He", energy=1.0e6)
    layer = Layer.from_formula("Si", density=2.33, width=10000.0)
    results = SR(layer, ion, output_type=5).run()
    assert isinstance(results, SRResults)
    assert results.data.shape[0] == 6
    assert results.data.shape[1] > 0
    assert (results.data[0] > 0).all()


def test_run_in_directory_with_space(srim_copy):
    ion = Ion("Ni", energy=3.0e6)
    layer = Layer.from_formula("Ni", density=8.9, width=10000.0)
    results = TRIM(Target([layer]), ion, number_ions=5, calculation=1).run(
        srim_directory=str(srim_copy)
    )
    assert results.range.ions.sum() > 0
