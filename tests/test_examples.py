"""Static checks on the examples (no SRIM needed)."""

import ast
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
NAME = "boron_in_gold_on_silicon.py"


def test_skill_example_matches_examples_scripts():
    skill = (ROOT / "skills" / "pysrim2" / "examples" / NAME).read_bytes()
    assert (ROOT / "examples" / "scripts" / NAME).read_bytes() == skill


def test_example_scripts_parse():
    for path in (ROOT / "examples" / "scripts").glob("*.py"):
        ast.parse(path.read_text(encoding="utf-8"), filename=str(path))


def test_run_yaml_example_loads_and_takes_overrides():
    from srim.spec import RunSpec

    spec = RunSpec.from_yaml(ROOT / "examples" / "scripts" / "run_yaml_example" / "run.yaml")
    assert spec.seed == 12345 and spec.ion.energy_keV == 20.0
    assert spec.with_overrides(["ion.energy_keV=30"]).ion.energy_keV == 30.0
