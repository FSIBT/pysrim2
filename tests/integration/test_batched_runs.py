"""Batched runs agree with one big run (``pytest -m srim``)."""

import numpy as np
from test_reproducibility import REFERENCE

from srim import TRIM
from srim.scan import run_batches
from srim.spec import RunSpec


def test_two_batches_agree_with_one_run():
    spec = RunSpec.from_dict(REFERENCE)
    with run_batches(spec, 200, 100, seed=7) as merged:
        batched = merged.range3d.df.depth_A.to_numpy()
        assert len(set(merged.seeds)) == 2
    with TRIM.from_spec(spec.with_overrides(["number_ions=200", "seed=8"])).run() as single:
        whole = single.range3d.df.depth_A.to_numpy()
    sigma = np.hypot(
        batched.std(ddof=1) / np.sqrt(len(batched)), whole.std(ddof=1) / np.sqrt(len(whole))
    )
    assert abs(batched.mean() - whole.mean()) < 3 * sigma
