"""Distributed beams in real SRIM: exact energies, exact and binned agreement, tilts
(``pytest -m srim``).

Exact runs write TRIM.DAT (one line per sampled ion) and SRIM runs exactly those ions; the
tests check that the ions SRIM ran are the ones sampled (``sampled_ions``), that they reach the
depths their energies predict, and that the binned mode of the same beam gives the same mean
depth within the statistical error.
"""

from pathlib import Path

import numpy as np

from srim.beam import Gaussian, Histogram, Uniform, run_beam, sampled_ions
from srim.spec import RunSpec

TARGET_NM = 1000.0


def beam_spec(*, energy=20.0, angle=7.0, thickness_nm=TARGET_NM, number_ions, seed, beam=None):
    """B into a Si layer; ``energy`` and ``angle`` are numbers or distribution mappings."""
    data = {
        "ion": {"symbol": "B", "energy_keV": energy, "angle_deg": angle},
        "number_ions": number_ions,
        "calculation": 1,
        "seed": seed,
        "settings": {"ranges": 1},
        "target": [
            {
                "name": "Si",
                "thickness_nm": thickness_nm,
                "density_g_cm3": 2.33,
                "elements": [{"symbol": "Si", "stoich": 1.0}],
            }
        ],
    }
    if beam is not None:
        data["beam"] = beam
    return RunSpec.from_dict(data)


def stopped_depths_nm(range3d_df, thickness_nm):
    """Final depths (nm) of the ions that stopped inside the layer."""
    depth = range3d_df["depth_A"].to_numpy() / 10.0
    return depth[(depth > 0) & (depth < thickness_nm)]


def test_exact_energies_follow_trim_dat():
    energy = Histogram.from_centres([10, 20, 40], [1, 1, 1]).to_dict(inline=True)
    spec = beam_spec(energy=energy, thickness_nm=500.0, number_ions=300, seed=1)
    with run_beam(spec) as results:
        ions = sampled_ions(results)
        depths = results.range3d.df[["ion", "depth_A"]]
        joined = depths.merge(ions[["ion", "energy_keV"]], on="ion")

    assert len(ions) == 300
    assert len(joined) > 0.9 * 300  # almost every ion is in RANGE_3D
    assert joined["energy_keV"].between(5.0, 50.0).all()  # the histogram's support
    # the histogram bins: edges 5, 15, 30, 50 keV around the centres 10, 20, 40
    bin_of_ion = np.digitize(joined["energy_keV"].to_numpy(), [15.0, 30.0])
    mean_depth = joined.assign(bin=bin_of_ion).groupby("bin")["depth_A"].mean().to_numpy()
    assert len(mean_depth) == 3
    assert np.all(np.diff(mean_depth) > 0), mean_depth


def test_exact_and_binned_agree():
    energy = Gaussian(40, 8).to_dict(inline=True)
    with run_beam(beam_spec(energy=energy, number_ions=500, seed=11)) as exact:
        exact_depths = stopped_depths_nm(exact.range3d.df, TARGET_NM)
    binned_beam = {"mode": "binned", "bins": {"energy_keV": 10}}
    with run_beam(beam_spec(energy=energy, number_ions=500, seed=12, beam=binned_beam)) as binned:
        binned_depths = stopped_depths_nm(binned.range3d.df, TARGET_NM)

    assert len(exact_depths) > 0.95 * 500 and len(binned_depths) > 0.95 * 500
    se_exact = exact_depths.std(ddof=1) / np.sqrt(len(exact_depths))
    se_binned = binned_depths.std(ddof=1) / np.sqrt(len(binned_depths))
    difference = abs(exact_depths.mean() - binned_depths.mean())
    assert difference < 3 * np.sqrt(se_exact**2 + se_binned**2), (
        exact_depths.mean(),
        binned_depths.mean(),
    )


def test_exact_tilt_distribution():
    spec = beam_spec(angle=Uniform(0, 60).to_dict(inline=True), number_ions=200, seed=3)
    with run_beam(spec) as results:
        ions = sampled_ions(results)
        trim_in = Path(results.directory, "TRIM.IN").read_text(encoding="ascii")

    assert len(ions) == 200
    # tolerance: TRIM.DAT stores the direction cosines with 7 significant digits
    assert ions["angle_deg"].between(0.0, 60.0 + 1e-4).all()
    # the cascade option is the line after the "Cascades(...)" header: 4 = quick KP for exact
    lines = trim_in.splitlines()
    header = next(i for i, line in enumerate(lines) if line.startswith("Cascades"))
    assert lines[header + 1].split()[0] == "4"
