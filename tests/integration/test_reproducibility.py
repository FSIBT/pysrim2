"""Does the same spec with the same seed give the same ions? (``pytest -m srim``)

Same machine, same spec, same seed must reproduce exactly (asserted). Whether
other platforms (wine, native Windows, the container) give the same numbers is
only reported: the test writes a digest file that CI compares informationally.
"""

import hashlib
import os
import shutil
import sys
import tempfile
from pathlib import Path

import pandas as pd

from srim import TRIM
from srim.spec import RunSpec

REFERENCE = {
    "ion": {"symbol": "B", "energy_keV": 20.0, "angle_deg": 7.0},
    "number_ions": 200,
    "calculation": 1,
    "seed": 12345,
    "settings": {"ranges": 1},
    "target": [
        {
            "name": "Au",
            "thickness_nm": 100.0,
            "density_g_cm3": 19.3,
            "elements": [{"symbol": "Au", "stoich": 1.0}],
        },
        {
            "name": "Si",
            "thickness_nm": 1000.0,
            "density_g_cm3": 2.33,
            "elements": [{"symbol": "Si", "stoich": 1.0}],
        },
    ],
}


def run_reference(srim_directory):
    trim = TRIM.from_spec(RunSpec.from_dict(REFERENCE))
    return trim.run(srim_directory=str(srim_directory)).range3d.df.copy()


def digest(df):
    """SHA-256 of the sorted table, as text (the file prints fixed decimals)."""
    ordered = df.sort_values(list(df.columns), kind="stable").reset_index(drop=True)
    text = ordered.to_csv(index=False, float_format="%.4f")
    return hashlib.sha256(text.encode()).hexdigest()


def test_same_spec_and_seed_reproduce(srim_copy, tmp_path):
    first = run_reference(srim_copy)
    second_dir = tmp_path / "second copy"
    shutil.copytree(srim_copy, second_dir)
    second = run_reference(second_dir)
    pd.testing.assert_frame_equal(first, second, check_exact=True)

    label = os.environ.get("PYSRIM2_REPRO_LABEL") or sys.platform
    out = os.environ.get("PYSRIM2_REPRO_OUT")
    path = Path(out) if out else Path(tempfile.gettempdir()) / "repro-digest.txt"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        f"label: {label}\n"
        f"digest: {digest(first)}\n"
        f"mean_depth_A: {first['depth_A'].mean():.3f}\n"
        f"n_ions: {first['ion'].nunique()}\n"
    )
