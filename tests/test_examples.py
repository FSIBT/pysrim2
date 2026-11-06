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


def test_scan_yaml_example_is_the_script_scan():
    """``scan.yaml`` is the scan of ``angle_energy_scan.py`` written as a file: same points,
    same run specs (so the same ids and seeds), same extractors and ion count."""
    import importlib.util

    from srim.scan import Scan

    path = ROOT / "examples" / "scripts" / "angle_energy_scan.py"
    spec = importlib.util.spec_from_file_location("angle_energy_scan", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    in_script = module.make_scan()
    from_file = Scan.from_yaml(ROOT / "examples" / "scripts" / "scan.yaml")
    assert [p.id for p in from_file.points()] == [p.id for p in in_script.points()]
    assert [p.spec.to_dict() for p in from_file.points()] == [
        p.spec.to_dict() for p in in_script.points()
    ]
    assert (from_file.ions, from_file.seed, from_file.extract) == (
        in_script.ions,
        in_script.seed,
        in_script.extract,
    )
