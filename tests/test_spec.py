"""run.yaml: spec round trips, seed, validation, overrides, import of TRIM.IN."""

import os
import random
import shutil
from pathlib import Path

import pytest
import yaml

from srim import TRIM, Ion, Layer, Target
from srim.input import TRIMInput
from srim.spec import (
    A_to_nm,
    RunSpec,
    SpecError,
    eV_to_keV,
    keV_to_eV,
    nm_to_A,
    parse_trim_in,
)

TEST_FILES = Path(__file__).resolve().parent.parent / "test_files"
IMPORTABLE = sorted(TEST_FILES.glob("*/TRIM.IN"))


def trim_in_bytes(trim, tmp_path, name="w"):
    directory = tmp_path / name
    directory.mkdir(exist_ok=True)
    cwd = os.getcwd()
    os.chdir(directory)
    try:
        TRIMInput(trim).write()
    finally:
        os.chdir(cwd)
    return (directory / "TRIM.IN").read_bytes()


def reference_trim():
    au = Layer.from_formula("Au", 19.3, 1000.0)
    si = Layer.from_formula("Si", 2.33, 10000.0)
    return TRIM(
        Target([au, si]),
        Ion("B", energy=20e3),
        number_ions=200,
        calculation=1,
        angle_ions=7.0,
        ranges=1,
        random_seed=12345,
    )


def three_layer_trim():
    w = Layer({"W": {"stoich": 1.0, "E_d": 21.0, "lattice": 2.1, "surface": 3.1}}, 19.3, 1000.0)
    sio2 = Layer(
        {
            "Si": {"stoich": 1, "E_d": 22.0, "lattice": 2.2, "surface": 3.2},
            "O": {"stoich": 2, "E_d": 23.0, "lattice": 2.3, "surface": 3.3},
        },
        2.33,
        1234.5,
        name="SiO2",
    )
    si = Layer({"Si": [1.0, 35.0, 0.0, 3.0]}, 2.32, 987.6, phase=1, name="Silicon")
    return TRIM(
        Target([w, sio2, si]),
        Ion("Ni", energy=1.234e6, mass=58.7),
        calculation=2,
        number_ions=77,
        angle_ions=33.3,
        plot_mode=1,
        plot_xmax=3000.0,
        autosave=10,
        collisions=1,
        random_seed=4097,
    )


@pytest.mark.parametrize("make", [reference_trim, three_layer_trim])
def test_trim_spec_trim_in_is_byte_identical(make, tmp_path):
    trim = make()
    again = TRIM.from_spec(trim.to_spec())
    assert trim_in_bytes(again, tmp_path, "b") == trim_in_bytes(trim, tmp_path, "a")
    # through YAML as well
    trim.to_yaml(tmp_path / "run.yaml")
    assert trim_in_bytes(TRIM.from_yaml(tmp_path / "run.yaml"), tmp_path, "c") == trim_in_bytes(
        trim, tmp_path, "a"
    )
    assert TRIM.from_spec(trim.to_spec().to_dict()).to_spec() == trim.to_spec()


def test_reference_spec_content():
    spec = reference_trim().to_spec()
    assert spec.ion.energy_keV == 20.0 and spec.ion.angle_deg == 7.0
    assert [layer.thickness_nm for layer in spec.target] == [100.0, 1000.0]
    assert spec.seed == 12345 and spec.number_ions == 200
    assert spec.settings == {"description": "pysrim run", "ranges": 1}


def test_unit_conversion_is_exact():
    for nm in (100.0, 0.1, 123.45, 1e-3, 5000.0):
        assert A_to_nm(nm_to_A(nm)) == nm
    for kev in (20.0, 0.5, 1234.5, 4500.0, 1e-3):
        assert eV_to_keV(keV_to_eV(kev)) == kev
    assert nm_to_A(0.1) == 1.0 and keV_to_eV(0.001) == 1.0
    rng = random.Random(1)
    for _ in range(2000):
        w = round(rng.uniform(1, 1e6), rng.randint(0, 3))
        assert nm_to_A(A_to_nm(w)) == w
        e = round(rng.uniform(1, 1e7), rng.randint(0, 2))
        assert keV_to_eV(eV_to_keV(e)) == e


def test_seed_is_recorded_when_not_given(tmp_path):
    layer = Layer.from_formula("Si", 2.33, 1000.0)
    trim = TRIM(Target([layer]), Ion("He", 1e6))
    spec = trim.to_spec()
    assert spec.seed == trim.settings.random_seed
    again = TRIM.from_spec(spec)
    assert again.settings.random_seed == spec.seed
    assert trim_in_bytes(again, tmp_path, "b") == trim_in_bytes(trim, tmp_path, "a")


def test_missing_seed_in_dict_is_picked():
    data = reference_trim().to_spec().to_dict()
    del data["seed"]
    spec = RunSpec.from_dict(data)
    assert isinstance(spec.seed, int) and 0 <= spec.seed <= 100000


def test_yaml_layout(tmp_path):
    spec = reference_trim().to_spec()
    spec.to_yaml(tmp_path / "run.yaml", {"host": "h"})
    document = yaml.safe_load((tmp_path / "run.yaml").read_text())
    assert list(document) == ["version", "spec", "provenance"]
    assert document["version"] == 1 and "version" not in document["spec"]
    assert document["spec"]["ion"]["energy_keV"] == 20.0
    assert document["provenance"] == {"host": "h"}
    assert RunSpec.from_yaml(tmp_path / "run.yaml") == spec


def test_settings_only_non_defaults():
    settings = three_layer_trim().to_spec().settings
    assert settings == {
        "description": "pysrim run",
        "autosave": 10,
        "plot_mode": 1,
        "plot_xmax": 3000.0,
        "ranges": 0,
        "collisions": 1,
    }


@pytest.mark.parametrize("path", IMPORTABLE, ids=lambda p: p.parent.name)
def test_import_test_files_round_trip(path, tmp_path):
    spec = parse_trim_in(path)
    trim = TRIM.from_spec(spec)
    written = trim_in_bytes(trim, tmp_path, "a")
    # the spec survives YAML, and re-importing what pysrim wrote gives the same spec
    spec.to_yaml(tmp_path / "run.yaml")
    assert RunSpec.from_yaml(tmp_path / "run.yaml") == spec
    assert trim_in_bytes(TRIM.from_yaml(tmp_path / "run.yaml"), tmp_path, "b") == written
    assert parse_trim_in(tmp_path / "a" / "TRIM.IN") == spec
    assert trim.to_spec() == spec


def test_import_values_of_native_srim_file():
    spec = parse_trim_in(TEST_FILES / "3" / "TRIM.IN")
    assert (spec.ion.symbol, spec.ion.mass_amu, spec.ion.energy_keV) == ("B", 11.0, 200.0)
    assert spec.number_ions == 99999 and spec.calculation == 2 and spec.seed == 0
    assert [layer.thickness_nm for layer in spec.target] == [100.0] * 3
    assert [[e.symbol for e in layer.elements] for layer in spec.target] == [
        ["W"],
        ["Si", "O"],
        ["Si"],
    ]
    assert spec.target[1].elements[0].stoich == 0.333333
    assert spec.target[1].elements[0].displacement_eV == 22.0
    assert spec.target[0].elements[0].mass_amu == 183.0  # differs from the database
    assert spec.settings["plot_mode"] == 1 and spec.settings["bragg_correction"] == 0.0


def test_import_rejects_garbage(tmp_path):
    bad = tmp_path / "TRIM.IN"
    bad.write_text("hello\nworld\n")
    with pytest.raises(SpecError, match="not a TRIM.IN"):
        parse_trim_in(bad)


# --- validation -------------------------------------------------------------------


def good():
    return reference_trim().to_spec().to_dict()


def test_valid_spec_passes():
    RunSpec.from_dict(good()).validate()


def edit(path, value):
    data = good()
    node = data
    keys = path.split(".")
    for key in keys[:-1]:
        node = node[int(key)] if key.isdigit() else node[key]
    if value is DELETE:
        del node[keys[-1]]
    else:
        node[keys[-1]] = value
    return data


DELETE = object()


@pytest.mark.parametrize(
    "path, value, message",
    [
        ("ion.energy_keV", "20", "ion.energy_keV must be a number"),
        ("ion.energy_kev", 20.0, "unknown key 'ion.energy_kev'"),
        ("ion.energy_keV", DELETE, "missing required key 'ion.energy_keV'"),
        ("ion.energy_keV", -1.0, "ion.energy_keV must be > 0"),
        ("ion.symbol", "Xx", "ion.symbol 'Xx' is not a chemical element"),
        ("ion.angle_deg", 90.0, r"ion.angle_deg must be in \[0, 90\)"),
        ("ion.angle_deg", -1.0, "ion.angle_deg"),
        ("number_ions", 0, "number_ions must be > 0"),
        ("number_ions", 1.5, "number_ions must be an integer"),
        ("number_ions", True, "number_ions must be an integer"),
        ("calculation", 9, "calculation must be 1..7"),
        ("target.0.thickness_nm", 0.0, r"target\[0\].thickness_nm must be > 0"),
        ("target.1.thickness_nm", -5.0, r"target\[1\].thickness_nm must be > 0"),
        ("target.0.thickness_nm", DELETE, r"missing required key 'target\[0\].thickness_nm'"),
        ("target.0.thickness_A", 5.0, r"unknown key 'target\[0\].thickness_A'"),
        ("target.0.density_g_cm3", float("nan"), "finite"),
        ("target.0.phase", 2, "phase must be 0"),
        ("target.0.elements", [], "at least one element"),
        ("target.0.elements.0.stoich", 0.0, r"elements\[0\].stoich must be > 0"),
        ("target.0.elements.0.surface", 3.0, r"unknown key 'target\[0\].elements\[0\].surface'"),
        ("settings.ranges", 2, "settings"),
        ("settings.ranges", "yes", "settings.ranges must be an integer"),
        ("settings.random_seed", 5, "unknown key 'settings.random_seed'"),
        ("target", "Au", "target must be a list"),
        ("version", 2, "version 2 is not supported"),
        ("seed", -3, "seed must be >= 0"),
        ("colour", 1, "unknown key 'colour'"),
    ],
)
def test_validation_errors_name_the_key(path, value, message):
    with pytest.raises(SpecError, match=message) as info:
        RunSpec.from_dict(edit(path, value))
    assert isinstance(info.value, ValueError)


def test_yaml_errors_have_spec_prefix(tmp_path):
    data = good()
    data["ion"]["energy_keV"] = "high"
    data.pop("version")
    (tmp_path / "run.yaml").write_text(yaml.safe_dump({"version": 1, "spec": data}))
    with pytest.raises(SpecError, match=r"spec\.ion\.energy_keV must be a number"):
        RunSpec.from_yaml(tmp_path / "run.yaml")
    (tmp_path / "run.yaml").write_text("- just\n- a list\n")
    with pytest.raises(SpecError, match="mapping"):
        RunSpec.from_yaml(tmp_path / "run.yaml")
    (tmp_path / "run.yaml").write_text("version: 1\n")
    with pytest.raises(SpecError, match="missing required key 'spec'"):
        RunSpec.from_yaml(tmp_path / "run.yaml")


def test_validate_catches_bad_dataclass():
    spec = reference_trim().to_spec()
    spec.target[0].thickness_nm = 0
    with pytest.raises(SpecError, match=r"target\[0\].thickness_nm"):
        spec.validate()
    with pytest.raises(SpecError):
        spec.to_yaml("/nonexistent-dir/run.yaml")


def test_defaults_are_filled_in():
    data = {
        "ion": {"symbol": "He", "energy_keV": 100},
        "target": [
            {"thickness_nm": 50, "density_g_cm3": 2.33, "elements": [{"symbol": "Si", "stoich": 1}]}
        ],
        "seed": 1,
    }
    spec = RunSpec.from_dict(data)
    assert spec.ion.mass_amu == 4.0026 or spec.ion.mass_amu > 4
    assert spec.number_ions == 1000 and spec.calculation == 1
    layer = spec.target[0]
    assert (layer.name, layer.phase) == ("Si", 0)
    assert layer.elements[0].displacement_eV == 25.0 and layer.elements[0].surface_eV == 3.0
    TRIM.from_spec(spec)


# --- overrides ----------------------------------------------------------------------


def test_with_overrides_converts_by_schema():
    spec = (
        reference_trim()
        .to_spec()
        .with_overrides(
            [
                "ion.energy_keV=30",
                "number_ions=500",
                "seed=7",
                "target.1.thickness_nm=2000.5",
                "target.0.elements.0.stoich=2",
                "settings.ranges=0",
                "settings.plot_xmax=1000",
                "settings.description=my run",
                "ion.symbol=C",
                "settings.bragg_correction=1.1",
            ]
        )
    )
    assert spec.ion.energy_keV == 30.0 and isinstance(spec.ion.energy_keV, float)
    assert spec.number_ions == 500 and spec.seed == 7
    assert spec.target[1].thickness_nm == 2000.5
    assert spec.target[0].elements[0].stoich == 2.0
    assert spec.settings["ranges"] == 0 and spec.settings["plot_xmax"] == 1000.0
    assert spec.settings["description"] == "my run" and spec.ion.symbol == "C"
    assert spec.settings["bragg_correction"] == 1.1


@pytest.mark.parametrize(
    "assignment, message",
    [
        ("ion.energy_keV", "dotted.key=value"),
        ("ion.energy_keV=abc", "ion.energy_keV must be a number"),
        ("number_ions=1.5", "number_ions must be an integer"),
        ("ion.colour=1", "unknown key 'ion.colour'"),
        ("target.5.thickness_nm=1", "expected an index 0..1"),
        ("target.x.thickness_nm=1", "expected an index"),
        ("target=1", "set one of its keys"),
        ("ion.energy_keV.x=1", "no sub-keys"),
        ("ion.energy_keV=-4", r"ion.energy_keV must be > 0"),
    ],
)
def test_with_overrides_errors(assignment, message):
    with pytest.raises(SpecError, match=message):
        reference_trim().to_spec().with_overrides([assignment])


def test_with_overrides_does_not_change_original():
    spec = reference_trim().to_spec()
    spec.with_overrides(["ion.energy_keV=30"])
    assert spec.ion.energy_keV == 20.0


# --- TRIM.run writes run.yaml ------------------------------------------------------------


@pytest.fixture
def fake_srim(tmp_path, monkeypatch):
    """A SRIM directory whose 'TRIM.exe' just writes minimal outputs (subprocess mocked)."""
    import shutil
    import subprocess

    directory = tmp_path / "srim"
    directory.mkdir()
    (directory / ".pysrim2-installed").write_text("abc123\n")
    monkeypatch.setattr(shutil, "which", lambda name: None)

    def fake_call(cmd, **kw):
        shutil.copy(TEST_FILES / "1" / "RANGE.txt", directory / "RANGE.txt")

    monkeypatch.setattr(subprocess, "check_call", fake_call)

    class FakeResults:
        def __init__(self, path):
            self.path = path

    monkeypatch.setattr("srim.srim.Results", FakeResults)
    return directory


def test_run_writes_run_yaml_with_provenance(fake_srim):
    import socket
    import sys

    import srim

    trim = reference_trim()
    results = trim.run(srim_directory=str(fake_srim))
    document = yaml.safe_load((fake_srim / "run.yaml").read_text())
    prov = document["provenance"]
    assert prov["pysrim2_version"] == srim.__version__
    assert prov["srim_sha256"] == "abc123"
    assert prov["platform"] == sys.platform and prov["backend"] == "native"
    assert prov["host"] == socket.gethostname() and prov["seed"] == 12345
    assert prov["started_utc"] <= prov["finished_utc"] and prov["started_utc"].endswith("Z")
    assert RunSpec.from_yaml(fake_srim / "run.yaml") == trim.to_spec()
    # results carry the spec, but not the provenance (no host name in exports)
    assert results.spec == trim.to_spec().to_dict()
    assert "host" not in str(results.spec)


def test_run_without_marker_or_with_write_spec_false(fake_srim):
    (fake_srim / ".pysrim2-installed").unlink()
    reference_trim().run(srim_directory=str(fake_srim))
    document = yaml.safe_load((fake_srim / "run.yaml").read_text())
    assert document["provenance"]["srim_sha256"] is None
    (fake_srim / "run.yaml").unlink()
    reference_trim().run(srim_directory=str(fake_srim), write_spec=False)
    assert not (fake_srim / "run.yaml").exists()


def test_run_removes_stale_run_yaml_on_failure(fake_srim, monkeypatch):
    import subprocess

    from srim.srim import SRIMRunError

    (fake_srim / "run.yaml").write_text("old")
    monkeypatch.setattr(subprocess, "check_call", lambda cmd, **kw: None)
    with pytest.raises(SRIMRunError):
        reference_trim().run(srim_directory=str(fake_srim))
    assert not (fake_srim / "run.yaml").exists()


def test_copy_output_files_copies_run_yaml(fake_srim, tmp_path):
    reference_trim().run(srim_directory=str(fake_srim))
    out = tmp_path / "out"
    out.mkdir()
    TRIM.copy_output_files(str(fake_srim), str(out))
    assert (out / "run.yaml").is_file() and (out / "RANGE.txt").is_file()


def test_results_spec_is_default_for_exports(tmp_path):
    from srim.output import Results

    results = Results(str(TEST_FILES / "1"))
    assert results.spec is None
    spec = reference_trim().to_spec().to_dict()
    results.spec = spec
    results.to_csv(tmp_path, include=["range"])
    exported = yaml.safe_load((tmp_path / "metadata.yaml").read_text())
    assert exported["spec"] == spec


# --- review fixes ------------------------------------------------------------------------


def test_set_ion_symbol_takes_the_new_mass():
    spec = reference_trim().to_spec().with_overrides(["ion.symbol=Au"])
    assert spec.ion.symbol == "Au" and spec.ion.mass_amu == pytest.approx(196.967, abs=0.01)
    spec = reference_trim().to_spec().with_overrides(["ion.symbol=Au", "ion.mass_amu=197"])
    assert spec.ion.mass_amu == 197.0
    spec = reference_trim().to_spec().with_overrides(["target[0].elements[0].symbol=Ag"])
    assert spec.target[0].elements[0].mass_amu is None


def test_set_element_symbol_drops_custom_mass():
    spec = parse_trim_in(TEST_FILES / "3" / "TRIM.IN")
    assert spec.target[0].elements[0].mass_amu == 183.0
    spec = spec.with_overrides(["target.0.elements.0.symbol=Mo"])
    assert spec.target[0].elements[0].mass_amu is None


def test_ion_mass_written_only_when_not_database_value():
    assert "mass_amu" not in reference_trim().to_spec().to_dict()["ion"]
    custom = three_layer_trim().to_spec()
    assert custom.to_dict()["ion"]["mass_amu"] == 58.7
    assert RunSpec.from_dict(custom.to_dict()) == custom
    data = reference_trim().to_spec().to_dict()
    data["ion"]["symbol"] = "Au"  # edited file without mass: database mass
    assert RunSpec.from_dict(data).ion.mass_amu > 190


def test_set_accepts_bracket_keys():
    spec = (
        three_layer_trim()
        .to_spec()
        .with_overrides(["target[0].thickness_nm=50", "target[1].elements[1].stoich=3"])
    )
    assert spec.target[0].thickness_nm == 50.0
    assert spec.target[1].elements[1].stoich == 3.0


def test_settings_errors_name_the_key():
    data = good()
    data["settings"]["plot_mode"] = 9
    with pytest.raises(SpecError, match=r"settings\.plot_mode has an invalid value 9"):
        RunSpec.from_dict(data)
    data = good()
    data["settings"].update(plot_xmin=5.0, plot_xmax=1.0)
    with pytest.raises(SpecError, match="settings.plot_xmin must be <="):
        RunSpec.from_dict(data)


def test_run_keeps_results_if_run_yaml_cannot_be_written(fake_srim, monkeypatch):
    def fail(self, path, provenance=None):
        raise OSError("disk full")

    monkeypatch.setattr(RunSpec, "to_yaml", fail)
    with pytest.warns(RuntimeWarning, match="could not write run.yaml"):
        results = reference_trim().run(srim_directory=str(fake_srim))
    assert results.spec["seed"] == 12345


def test_backend_is_native_on_windows(fake_srim, monkeypatch):
    import shutil
    import sys

    monkeypatch.setattr(shutil, "which", lambda name: "/x/wine")
    monkeypatch.setattr(sys, "platform", "win32")
    from datetime import UTC, datetime

    from srim.srim import _provenance

    now = datetime.now(UTC)
    assert _provenance(str(fake_srim), now, now, 1)["backend"] == "native"
    monkeypatch.setattr(sys, "platform", "linux")
    assert _provenance(str(fake_srim), now, now, 1)["backend"] == "wine"


def test_unnamed_layers_get_a_default_name_in_spec_and_trim_in(tmp_path):
    from srim import Ion, Layer, Target

    trim = TRIM(Target([Layer.from_formula("SiC", 3.21, 1000.0)]), Ion("Ni", 1e6), number_ions=5)
    assert trim.to_spec().target[0].name == "SiC"
    assert b"None" not in trim_in_bytes(trim, tmp_path)


def test_unnamed_layer_name_agrees_between_run_yaml_and_layer(tmp_path):
    from srim import Ion, Layer, Target

    layer = Layer({"Si": [1.0, 25.0, 0.0, 3.0], "C": [1.0, 28.0, 0.0, 3.0]}, 3.21, 1000.0)
    assert layer.name == "Si+C"
    trim = TRIM(Target([layer]), Ion("Ni", 1e6), number_ions=5, seed=1)
    data = trim.to_spec().to_dict()
    del data["target"][0]["name"]
    spec = RunSpec.from_dict(data)
    assert spec.target[0].name == "Si+C"
    assert trim_in_bytes(TRIM.from_spec(spec), tmp_path, "a") == trim_in_bytes(trim, tmp_path, "b")


def test_run_yaml_accepts_a_scan_section_and_ignores_it(tmp_path):
    spec = good()
    text = yaml.safe_dump(
        {"version": 1, "spec": spec, "scan": {"grid": {"ion.energy_keV": [10.0, 20.0]}}}
    )
    (tmp_path / "run.yaml").write_text(text)
    loaded = RunSpec.from_yaml(tmp_path / "run.yaml")
    assert loaded.ion.energy_keV == spec["ion"]["energy_keV"]
    with pytest.raises(SpecError, match="unknown key 'colour'"):
        (tmp_path / "bad.yaml").write_text(
            yaml.safe_dump({"version": 1, "spec": spec, "colour": 1})
        )
        RunSpec.from_yaml(tmp_path / "bad.yaml")



def test_copy_output_files_leaves_the_sources_in_place_for_results(tmp_path):
    """Files of SRIM Outputs are copied, so Results of the source keeps reading them."""
    from srim.output import Results

    run = tmp_path / "run"
    shutil.copytree(TEST_FILES / "1", run)
    outputs = run / "SRIM Outputs"
    outputs.mkdir()
    shutil.move(run / "RANGE_3D.txt", outputs / "RANGE_3D.txt")
    results = Results(str(run))
    out = tmp_path / "out"
    out.mkdir()
    TRIM.copy_output_files(str(run), str(out))
    assert (out / "RANGE_3D.txt").is_file()
    assert (outputs / "RANGE_3D.txt").is_file()
    assert len(results.range3d.df) > 0
