"""``pysrim2 run`` on the reference run.yaml with real SRIM (``pytest -m srim``)."""

import yaml

from srim import cli
from srim.spec import RunSpec

REFERENCE = {
    "version": 1,
    "spec": {
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
    },
}


def test_pysrim2_run_reference_spec(tmp_path):
    source = tmp_path / "run.yaml"
    source.write_text(yaml.safe_dump(REFERENCE, sort_keys=False))
    out = tmp_path / "out"
    assert cli.main(["run", str(source), "--output", str(out)]) == 0
    assert (out / "RANGE.txt").is_file()
    document = yaml.safe_load((out / "run.yaml").read_text())
    provenance = document["provenance"]
    assert provenance["seed"] == 12345
    for key in ("pysrim2_version", "platform", "backend", "started_utc", "finished_utc", "host"):
        assert provenance[key]
    spec = RunSpec.from_yaml(out / "run.yaml")
    assert spec.seed == 12345 and spec.ion.energy_keV == 20.0
