"""Run the reference example shipped with the agent skill (``pytest -m srim``)."""

import importlib.util
from pathlib import Path

EXAMPLE = (
    Path(__file__).resolve().parents[2]
    / "skills"
    / "pysrim2"
    / "examples"
    / "boron_in_gold_on_silicon.py"
)
REFERENCE_MEAN_DEPTH_A = 282.0


def test_skill_example_mean_depth(tmp_path):
    spec = importlib.util.spec_from_file_location("skill_example", EXAMPLE)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    result = module.run(200, tmp_path / "srim-output")
    assert 0.7 * REFERENCE_MEAN_DEPTH_A <= result["mean_depth_A"] <= 1.3 * REFERENCE_MEAN_DEPTH_A
    assert result["lateral_straggle_A"] > 0
    assert (tmp_path / "srim-output" / "RANGE_3D.txt").is_file()
