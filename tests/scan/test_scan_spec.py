"""Scan definition: grid and point expansion, dotted keys, spec hash, run.yaml round trip."""

import pandas as pd
import pytest
import yaml

from srim.scan import Precision, Scan, ScanPoint
from srim.scan.spec import normalise_key, spec_hash
from srim.spec import RunSpec, SpecError

BASE = {
    "ion": {"symbol": "B", "energy_keV": 20.0, "angle_deg": 0.0},
    "number_ions": 200,
    "seed": 7,
    "target": [
        {
            "name": "Si",
            "thickness_nm": 100.0,
            "density_g_cm3": 2.33,
            "elements": [{"symbol": "Si", "stoich": 1.0}],
        }
    ],
}


@pytest.fixture
def base():
    return RunSpec.from_dict(BASE)


def test_grid_is_a_cartesian_product_with_the_last_key_fastest(base):
    scan = Scan(
        base,
        grid={"ion.energy_keV": [10.0, 20.0], "target[0].thickness_nm": [50.0, 100.0, 150.0]},
    )
    points = scan.points()
    assert len(points) == 6
    assert [(p.params["ion.energy_keV"], p.params["target.0.thickness_nm"]) for p in points] == [
        (10.0, 50.0),
        (10.0, 100.0),
        (10.0, 150.0),
        (20.0, 50.0),
        (20.0, 100.0),
        (20.0, 150.0),
    ]
    assert [p.index for p in points] == list(range(6))
    assert scan.param_names == ["ion.energy_keV", "target.0.thickness_nm"]


def test_point_specs_carry_the_values(base):
    point = Scan(base, grid={"target[0].thickness_nm": [42.0]}).points()[0]
    assert isinstance(point, ScanPoint)
    assert isinstance(point.spec, RunSpec)
    assert point.spec.target[0].thickness_nm == 42.0
    assert point.spec.ion.energy_keV == 20.0
    assert point.id == spec_hash(point.spec)


def test_zipped_points_are_used_row_by_row(base):
    rows = [
        {"ion.energy_keV": 10.0, "ion.angle_deg": 0.0},
        {"ion.energy_keV": 20.0, "ion.angle_deg": 30.0},
        {"ion.energy_keV": 30.0, "ion.angle_deg": 60.0},
    ]
    points = Scan(base, points=rows).points()
    assert [p.params for p in points] == rows
    assert [p.spec.ion.angle_deg for p in points] == [0.0, 30.0, 60.0]


def test_points_from_a_dataframe_match_the_list_form(base):
    rows = [{"ion.energy_keV": 10.0}, {"ion.energy_keV": 20.0}]
    frame = pd.DataFrame(rows)
    from_list = Scan(base, points=rows).points()
    from_frame = Scan(base, points=frame).points()
    assert [p.params for p in from_frame] == [p.params for p in from_list]
    assert [p.id for p in from_frame] == [p.id for p in from_list]


def test_points_and_grid_combine_row_by_row(base):
    scan = Scan(
        base,
        points=[{"ion.energy_keV": 10.0}, {"ion.energy_keV": 20.0}],
        grid={"ion.angle_deg": [0.0, 45.0]},
    )
    assert [(p.params["ion.energy_keV"], p.params["ion.angle_deg"]) for p in scan.points()] == [
        (10.0, 0.0),
        (10.0, 45.0),
        (20.0, 0.0),
        (20.0, 45.0),
    ]


def test_neither_grid_nor_points_gives_the_base_spec_as_one_point(base):
    (point,) = Scan(base).points()
    assert point.params == {}
    assert point.spec.to_dict() == base.to_dict()


def test_bracket_and_dot_index_forms_are_the_same_key():
    assert normalise_key("target[0].elements[1].stoich") == "target.0.elements.1.stoich"
    assert normalise_key("target.0.thickness_nm") == "target.0.thickness_nm"


def test_spec_hash_ignores_seed_and_ion_count_but_not_physics(base):
    same = RunSpec.from_dict({**BASE, "seed": 99, "number_ions": 5})
    assert spec_hash(base) == spec_hash(same)
    thicker = base.with_overrides(["target.0.thickness_nm=101"])
    assert spec_hash(thicker) != spec_hash(base)
    assert len(spec_hash(base)) == 64


def test_duplicate_points_are_rejected_naming_both(base):
    with pytest.raises(SpecError, match="same run spec") as info:
        Scan(base, points=[{"ion.energy_keV": 20.0}, {"ion.energy_keV": 20}])
    assert "{'ion.energy_keV': 20.0}" in str(info.value)


@pytest.mark.parametrize("key", ["number_ions", "seed"])
def test_ion_count_and_seed_are_not_scan_parameters(base, key):
    with pytest.raises(SpecError, match=f"scan.grid.{key}"):
        Scan(base, grid={key: [1, 2]})


def test_unknown_key_names_the_grid_key(base):
    with pytest.raises(SpecError, match=r"scan\.grid\.ion\.energy_kev: .*unknown key"):
        Scan(base, grid={"ion.energy_kev": [20.0]})


def test_bad_value_names_the_grid_key(base):
    with pytest.raises(SpecError, match=r"scan\.grid\.ion\.energy_keV: .*must be a number"):
        Scan(base, grid={"ion.energy_keV": [20.0, "high"]})


def test_unknown_key_in_points_names_the_row(base):
    with pytest.raises(SpecError, match=r"scan\.points\[0\]: .*unknown key 'colour'"):
        Scan(base, points=[{"colour": 1}, {"colour": 2}])


def test_points_must_have_the_same_keys(base):
    with pytest.raises(SpecError, match=r"scan\.points\[1\] has keys"):
        Scan(base, points=[{"ion.energy_keV": 1.0}, {"ion.angle_deg": 1.0}])


@pytest.mark.parametrize(
    "kwargs",
    [
        {"grid": {"ion.energy_keV": []}},
        {"grid": {"ion.energy_keV": 20.0}},
        {"grid": {"ion.energy_keV": "20"}},
        {"points": []},
    ],
)
def test_empty_or_conflicting_inputs_are_errors(base, kwargs):
    with pytest.raises(SpecError):
        Scan(base, **kwargs)


def test_grid_and_points_may_not_share_a_key(base):
    with pytest.raises(SpecError, match="is in both grid and points"):
        Scan(base, grid={"ion.energy_keV": [1.0]}, points=[{"ion.energy_keV": 2.0}])


def test_ions_default_to_the_base_count_and_accept_a_precision(base):
    assert Scan(base).ions == 200
    assert Scan(base, ions=500).ions == 500
    prec = Precision("sputter_yield", rel=0.05)
    assert Scan(base, ions=prec).ions == prec


@pytest.mark.parametrize("ions", [0, -3, 2.5, True, "many"])
def test_bad_ion_count_is_an_error(base, ions):
    with pytest.raises(SpecError, match="scan.ions"):
        Scan(base, ions=ions)


def test_extract_defaults_and_validates_names(base):
    assert Scan(base).extract == ["implant_summary"]
    Scan(base, extract=["sputter_yield", {"vacancy_at": {"depth_nm": 50}}])
    with pytest.raises(SpecError, match=r"scan\.extract\[1\]"):
        Scan(base, extract=["sputter_yield", "no_such_extractor"])
    with pytest.raises(SpecError, match=r"scan\.extract\[0\]"):
        Scan(base, extract=[{"vacancy_at": {"depth": 5}}])


def test_seed_must_be_a_non_negative_integer(base):
    assert Scan(base, seed=0).seed == 0
    with pytest.raises(SpecError, match="scan.seed"):
        Scan(base, seed=-1)


def test_yaml_round_trip(base, tmp_path):
    scan = Scan(
        base,
        grid={"ion.energy_keV": [10.0, 20.0], "target[0].thickness_nm": [50.0]},
        extract=["sputter_yield", {"vacancy_at": {"depth_nm": 50.0}}],
        ions=Precision("sputter_yield", rel=0.05, max_ions=5000),
        seed=3,
    )
    path = tmp_path / "run.yaml"
    scan.to_yaml(path, provenance={"pysrim2_version": "test"})
    document = yaml.safe_load(path.read_text(encoding="utf-8"))
    assert set(document) == {"version", "spec", "provenance", "scan"}
    assert document["scan"]["grid"] == {
        "ion.energy_keV": [10.0, 20.0],
        "target.0.thickness_nm": [50.0],
    }
    loaded = Scan.from_yaml(path)
    assert loaded.to_dict() == scan.to_dict()
    assert [p.id for p in loaded.points()] == [p.id for p in scan.points()]
    assert RunSpec.from_yaml(path).to_dict() == base.to_dict()


def test_yaml_round_trip_of_points_and_integer_ions(base, tmp_path):
    scan = Scan(base, points=[{"ion.energy_keV": 10.0}, {"ion.energy_keV": 30.0}], ions=400)
    scan.to_yaml(tmp_path / "run.yaml")
    loaded = Scan.from_yaml(tmp_path / "run.yaml")
    assert loaded.to_dict() == scan.to_dict()
    assert loaded.ions == 400
    assert len(loaded.points()) == 2


def test_yaml_errors_name_the_dotted_key(base, tmp_path):
    spec = base.to_dict()
    path = tmp_path / "run.yaml"
    path.write_text(
        yaml.safe_dump({"version": 1, "spec": spec, "scan": {"grid": {"ion.energy_kev": [1.0]}}}),
        encoding="utf-8",
    )
    with pytest.raises(SpecError, match=r"scan\.grid\.ion\.energy_kev"):
        Scan.from_yaml(path)
    path.write_text(
        yaml.safe_dump({"version": 1, "spec": spec, "scan": {"gird": {}}}), encoding="utf-8"
    )
    with pytest.raises(SpecError, match="unknown key 'scan.gird'"):
        Scan.from_yaml(path)
    path.write_text(
        yaml.safe_dump(
            {
                "version": 1,
                "spec": spec,
                "scan": {"ions": 5, "precision": {"target": "x", "rel": 0.1}},
            }
        ),
        encoding="utf-8",
    )
    with pytest.raises(SpecError, match="either ions or precision"):
        Scan.from_yaml(path)
    path.write_text(yaml.safe_dump({"version": 1, "spec": spec}), encoding="utf-8")
    with pytest.raises(SpecError, match="no 'scan' section"):
        Scan.from_yaml(path)


def test_python_callables_cannot_be_written_to_yaml(base, tmp_path):
    def mine(results, point=None):
        return {"x": 1.0}

    scan = Scan(base, extract=[mine])
    with pytest.raises(SpecError, match="cannot store"):
        scan.to_yaml(tmp_path / "run.yaml")
