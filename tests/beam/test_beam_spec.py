"""Beam distributions in the run spec: parsing, round trips, overrides and validation."""

import copy
import subprocess
import sys
import warnings
from pathlib import Path

import pytest

from srim.beam import (
    MAX_TRIM_DAT_IONS,
    BeamOptions,
    Histogram,
    PositionSpec,
    Uniform,
    check_beam,
    effective,
)
from srim.spec import RunSpec, SpecError

REPO = Path(__file__).resolve().parent.parent.parent
EXAMPLE_RUN = REPO / "examples" / "scripts" / "run_yaml_example" / "run.yaml"
TEST_FILES = REPO / "test_files"


def base():
    """A plain spec as plain data; the target is 500 nm thick."""
    return {
        "ion": {"symbol": "B", "energy_keV": 20.0, "angle_deg": 7.0},
        "number_ions": 200,
        "calculation": 1,
        "seed": 12345,
        "settings": {"description": "beam test", "ranges": 1},
        "target": [
            {
                "name": "Au",
                "thickness_nm": 500.0,
                "density_g_cm3": 19.3,
                "elements": [{"symbol": "Au", "stoich": 1.0}],
            }
        ],
    }


def distributed(**ion):
    """``base()`` with an energy Gaussian and the given ``ion`` keys (which may replace it)."""
    data = base()
    data["ion"]["energy_keV"] = {"gaussian": {"mean": 20.0, "sigma": 1.0}}
    data["ion"].update(ion)
    return data


# --- plain specs are unchanged ----------------------------------------------------


def test_plain_spec_unchanged():
    from srim import TRIM, Ion, Layer, Target
    from srim.spec import parse_trim_in

    specs = [RunSpec.from_yaml(EXAMPLE_RUN), RunSpec.from_dict(base())]
    au = Layer.from_formula("Au", 19.3, 1000.0)
    specs.append(
        TRIM(Target([au]), Ion("B", energy=20e3), number_ions=200, angle_ions=7.0).to_spec()
    )
    for path in sorted(TEST_FILES.glob("*/TRIM.IN")):
        specs.append(parse_trim_in(path))
    for spec in specs:
        plain = spec.to_dict()
        assert "beam" not in plain
        assert "azimuth_deg" not in plain["ion"]
        assert "position" not in plain["ion"]
        assert plain == spec.to_dict(inline=True)
        assert not spec.is_distributed


def test_to_dict_of_plain_spec_keeps_key_order():
    keys = list(RunSpec.from_dict(base()).to_dict())
    assert keys == ["version", "ion", "number_ions", "calculation", "seed", "settings", "target"]


# --- round trips ----------------------------------------------------------------------


def test_yaml_round_trip(tmp_path):
    data = distributed(
        angle_deg={"uniform": {"low": 0.0, "high": 10.0}},
        azimuth_deg={"histogram": {"edges": [0, 90, 180, 270, 360], "counts": [1, 2, 3, 4]}},
        position={
            "y_nm": {"uniform": {"low": -5.0, "high": 5.0}},
            "z_nm": 2.0,
            "depth_nm": {"uniform": {"low": 10.0, "high": 20.0}},
        },
    )
    data["beam"] = {"mode": "exact"}
    spec = RunSpec.from_dict(data)
    spec.to_yaml(tmp_path / "run.yaml")
    again = RunSpec.from_yaml(tmp_path / "run.yaml")
    assert again.to_dict() == spec.to_dict()
    assert again.is_distributed


def test_infinite_range_survives_yaml(tmp_path):
    data = distributed()
    data["ion"]["energy_keV"] = {
        "gaussian": {"mean": 20.0, "sigma": 1.0, "range": [float("-inf"), float("inf")]}
    }
    spec = RunSpec.from_dict(data)
    spec.to_yaml(tmp_path / "run.yaml")
    again = RunSpec.from_yaml(tmp_path / "run.yaml")
    assert again.to_dict() == spec.to_dict()
    assert again.ion.energy_keV.range == (float("-inf"), float("inf"))


def test_histogram_file_is_read_relative_to_run_yaml(tmp_path):
    folder = tmp_path / "beam"
    folder.mkdir()
    (folder / "hist.txt").write_text("1.0 2\n2.0 3\n3.0 5\n", encoding="utf-8")
    spec_path = folder / "run.yaml"
    spec_path.write_text(
        "version: 1\nspec:\n"
        "  ion: {symbol: B, energy_keV: {histogram: {file: hist.txt}}, angle_deg: 7}\n"
        "  number_ions: 200\n  seed: 1\n  settings: {description: d, ranges: 1}\n"
        "  target:\n    - {name: Au, thickness_nm: 500, density_g_cm3: 19.3,"
        " elements: [{symbol: Au, stoich: 1}]}\n",
        encoding="utf-8",
    )
    spec = RunSpec.from_yaml(spec_path)
    assert isinstance(spec.ion.energy_keV, Histogram)
    assert Path(spec.ion.energy_keV.file) == (folder / "hist.txt").resolve()
    assert spec.to_dict()["ion"]["energy_keV"] == {"histogram": {"file": spec.ion.energy_keV.file}}


# --- overrides ----------------------------------------------------------------------------


def test_overrides_on_distributions():
    spec = RunSpec.from_dict(distributed(angle_deg={"uniform": {"low": 0.0, "high": 10.0}}))
    spec = spec.with_overrides(
        [
            "ion.energy_keV.gaussian.sigma=7",
            "ion.angle_deg.uniform.high=12",
            "beam.mode=binned",
            "beam.bins.energy_keV=20",
        ]
    )
    d = spec.to_dict()
    assert d["ion"]["energy_keV"] == {"gaussian": {"mean": 20.0, "sigma": 7.0}}
    assert d["ion"]["angle_deg"] == {"uniform": {"low": 0.0, "high": 12.0}}
    assert d["beam"] == {"mode": "binned", "bins": {"energy_keV": 20}}


def test_override_sets_a_position_coordinate():
    spec = RunSpec.from_dict(distributed()).with_overrides(["ion.position.y_nm=3"])
    assert spec.to_dict()["ion"]["position"] == {"y_nm": 3.0, "z_nm": 0.0, "depth_nm": 0.0}
    assert spec.is_distributed


def test_override_replaces_shape_with_fixed_value():
    spec = RunSpec.from_dict(distributed()).with_overrides(["ion.energy_keV=90"])
    assert spec.ion.energy_keV == 90.0 and isinstance(spec.ion.energy_keV, float)
    assert not spec.is_distributed


def test_override_position_depth_of_uniform():
    data = distributed(
        position={"depth_nm": {"uniform": {"low": 0.0, "high": 100.0}}},
    )
    spec = RunSpec.from_dict(data).with_overrides(["ion.position.depth_nm.uniform.high=50"])
    assert spec.to_dict()["ion"]["position"]["depth_nm"] == {"uniform": {"low": 0.0, "high": 50.0}}


def test_fixed_energy_cannot_take_a_distribution_parameter():
    spec = RunSpec.from_dict(base())
    with pytest.raises(SpecError, match="ion.energy_keV") as info:
        spec.with_overrides(["ion.energy_keV.gaussian.sigma=7"])
    assert "fixed" in str(info.value)


def test_uniform_energy_cannot_take_a_gaussian_parameter():
    data = distributed()
    data["ion"]["energy_keV"] = {"uniform": {"low": 10.0, "high": 30.0}}
    with pytest.raises(SpecError, match="uniform"):
        RunSpec.from_dict(data).with_overrides(["ion.energy_keV.gaussian.sigma=7"])


def test_zero_sigma_degenerates_to_fixed_energy():
    spec = RunSpec.from_dict(distributed()).with_overrides(["ion.energy_keV.gaussian.sigma=0"])
    assert spec.ion.energy_keV == 20.0 and isinstance(spec.ion.energy_keV, float)


def test_unknown_distribution_parameter_is_refused():
    with pytest.raises(SpecError, match="ion.energy_keV.gaussian.colour"):
        RunSpec.from_dict(distributed()).with_overrides(["ion.energy_keV.gaussian.colour=1"])


def test_list_parameters_are_yaml_only():
    data = distributed()
    data["ion"]["energy_keV"] = {"histogram": {"edges": [1, 2, 3], "counts": [1, 1]}}
    with pytest.raises(SpecError, match="YAML"):
        RunSpec.from_dict(data).with_overrides(["ion.energy_keV.histogram.counts=1"])


def test_bins_must_be_integers_on_the_command_line():
    spec = RunSpec.from_dict(distributed()).with_overrides(["beam.mode=binned"])
    with pytest.raises(SpecError, match=r"beam.bins.energy_keV must be an integer"):
        spec.with_overrides(["beam.bins.energy_keV=2.5"])


# --- is_distributed, effective values -------------------------------------------------------


def test_is_distributed():
    assert not RunSpec.from_dict(base()).is_distributed
    data = base()
    data["ion"]["azimuth_deg"] = 30.0
    assert RunSpec.from_dict(data).is_distributed
    data = base()
    data["ion"]["azimuth_deg"] = {"uniform": {"low": 0.0, "high": 90.0}}
    assert RunSpec.from_dict(data).is_distributed


def test_effective_of_plain_spec():
    values = effective(RunSpec.from_dict(base()))
    assert values == {
        "energy_keV": 20.0,
        "angle_deg": 7.0,
        "azimuth_deg": 0.0,
        "y_nm": 0.0,
        "z_nm": 0.0,
        "depth_nm": 0.0,
    }


def test_effective_clips_and_keeps_user_values():
    data = distributed(angle_deg={"uniform": {"low": -5.0, "high": 95.0}})
    data["ion"]["energy_keV"] = {"gaussian": {"mean": 1.0, "sigma": 5.0}}
    spec = RunSpec.from_dict(data)
    values = effective(spec)
    assert values["energy_keV"].support()[0] == 0.0
    assert values["angle_deg"].support() == (0.0, 90.0)
    assert isinstance(values["azimuth_deg"], Uniform)
    assert values["azimuth_deg"].support() == (0.0, 360.0)
    assert values["y_nm"] == 0.0 and values["depth_nm"] == 0.0
    # the spec itself keeps what the user wrote
    assert spec.to_dict()["ion"]["angle_deg"] == {"uniform": {"low": -5.0, "high": 95.0}}


def test_effective_keeps_user_azimuth():
    spec = RunSpec.from_dict(distributed(azimuth_deg=45.0))
    assert effective(spec)["azimuth_deg"] == 45.0


# --- validation ------------------------------------------------------------------------------


def with_beam(data, **beam):
    data["beam"] = beam
    return data


@pytest.mark.parametrize(
    "build, key",
    [
        (lambda: with_beam(base(), mode="exact"), "beam"),
        (lambda: with_beam(distributed(), mode="other"), "beam.mode"),
        (
            lambda: with_beam(
                distributed(position={"y_nm": {"uniform": {"low": 0.0, "high": 1.0}}}),
                mode="binned",
            ),
            "ion.position",
        ),
        (
            lambda: with_beam(
                distributed(azimuth_deg={"gaussian": {"mean": 90.0, "sigma": 5.0}}),
                mode="binned",
            ),
            "ion.azimuth_deg",
        ),
        (
            lambda: {**with_beam(distributed(), mode="exact"), "calculation": 3},
            "calculation",
        ),
        (
            lambda: distributed(position={"depth_nm": {"uniform": {"low": 0.0, "high": 600.0}}}),
            "ion.position.depth_nm",
        ),
        (
            lambda: distributed(position={"depth_nm": {"uniform": {"low": -5.0, "high": 10.0}}}),
            "ion.position.depth_nm",
        ),
        (
            lambda: with_beam(distributed(), mode="binned", bins={"angle_deg": 4}),
            "beam.bins.angle_deg",
        ),
        (
            lambda: with_beam(distributed(), mode="binned", bins={"energy_keV": [1.0, 3.0, 2.0]}),
            "beam.bins.energy_keV",
        ),
        (
            lambda: with_beam(distributed(), mode="binned", bins={"energy_keV": 0}),
            "beam.bins.energy_keV",
        ),
        (
            lambda: distributed(energy_keV={"uniform": {"low": -10.0, "high": -1.0}}),
            "ion.energy_keV",
        ),
        (
            lambda: {
                **distributed(),
                "ion": {
                    **distributed()["ion"],
                    "energy_keV": {
                        "function": {"ref": "python:__main__:ramp", "range": [1.0, 10.0]}
                    },
                },
            },
            "ion.energy_keV",
        ),
        (
            lambda: {
                **distributed(),
                "ion": {
                    **distributed()["ion"],
                    "energy_keV": {
                        "function": {"ref": "python:beam_funcs:<lambda>", "range": [1.0, 10.0]}
                    },
                },
            },
            "ion.energy_keV",
        ),
    ],
)
def test_beam_errors_name_the_key(build, key):
    with pytest.raises(SpecError) as info:
        RunSpec.from_dict(build())
    assert key in str(info.value)


def test_binned_with_position_says_exact():
    data = with_beam(
        distributed(position={"y_nm": {"uniform": {"low": 0.0, "high": 1.0}}}), mode="binned"
    )
    with pytest.raises(SpecError, match="exact"):
        RunSpec.from_dict(data)


def test_non_uniform_azimuth_in_binned_mode_says_exact():
    data = with_beam(
        distributed(azimuth_deg={"gaussian": {"mean": 90.0, "sigma": 5.0}}), mode="binned"
    )
    with pytest.raises(SpecError, match="exact"):
        RunSpec.from_dict(data)


def test_exact_mode_accepts_calculations_1_and_2():
    for calculation in (1, 2):
        data = with_beam(distributed(), mode="exact")
        data["calculation"] = calculation
        RunSpec.from_dict(data)


def test_binned_mode_keeps_the_user_calculation():
    data = with_beam(distributed(), mode="binned")
    data["calculation"] = 3
    RunSpec.from_dict(data)


def test_fixed_angle_with_bins_names_the_fixed_angle():
    data = with_beam(distributed(), mode="binned", bins={"angle_deg": 4})
    with pytest.raises(SpecError, match="fixed") as info:
        RunSpec.from_dict(data)
    assert "beam.bins.angle_deg" in str(info.value)


def test_binned_bins_above_max_bins_are_refused():
    data = with_beam(distributed(angle_deg={"uniform": {"low": 0.0, "high": 10.0}}), mode="binned")
    data["beam"]["bins"] = {"energy_keV": 2000}
    with pytest.raises(SpecError, match="beam.max_bins"):
        RunSpec.from_dict(data)


def test_binned_bins_above_200_warn():
    data = with_beam(distributed(angle_deg={"uniform": {"low": 0.0, "high": 10.0}}), mode="binned")
    data["beam"]["bins"] = {"energy_keV": 50}  # 50 energy bins x 6 tilt bins (default)
    with pytest.warns(UserWarning, match="200"):
        RunSpec.from_dict(data)


def test_binned_with_default_bins_does_not_warn():
    data = with_beam(distributed(angle_deg={"uniform": {"low": 0.0, "high": 10.0}}), mode="binned")
    with warnings.catch_warnings():
        warnings.simplefilter("error")
        RunSpec.from_dict(data)


def test_position_depth_inside_target_is_accepted():
    RunSpec.from_dict(distributed(position={"depth_nm": {"uniform": {"low": 10.0, "high": 20.0}}}))


def test_scalar_depth_at_the_target_end_is_refused():
    with pytest.raises(SpecError, match="ion.position.depth_nm"):
        RunSpec.from_dict(distributed(position={"depth_nm": 500.0}))


def test_beam_options_and_position_spec_are_exported():
    assert MAX_TRIM_DAT_IONS == 99_999
    options = BeamOptions()
    assert options.mode == "exact" and options.bins == {} and options.max_bins == 2000
    position = PositionSpec()
    assert (position.y_nm, position.z_nm, position.depth_nm) == (0.0, 0.0, 0.0)
    assert callable(check_beam)


def test_check_beam_direct_call_on_plain_spec_does_nothing():
    check_beam(RunSpec.from_dict(base()))


def test_python_function_from_main_says_importable_module():
    data = distributed(energy_keV={"function": {"ref": "python:__main__:ramp", "range": [1, 10]}})
    with pytest.raises(SpecError, match="importable module") as info:
        RunSpec.from_dict(data)
    assert "ion.energy_keV" in str(info.value)


def test_python_lambda_reference_is_refused_with_the_key():
    data = distributed(
        energy_keV={"function": {"ref": "python:beam_funcs:<lambda>", "range": [1, 10]}}
    )
    with pytest.raises(SpecError, match="ion.energy_keV"):
        RunSpec.from_dict(data)


# --- import cycle -----------------------------------------------------------------


@pytest.mark.parametrize("module", ["srim.spec", "srim.beam"])
def test_import_in_a_fresh_interpreter(module):
    subprocess.run([sys.executable, "-c", f"import {module}"], check=True, cwd=REPO)


def test_copy_is_not_shared_between_specs():
    data = distributed()
    snapshot = copy.deepcopy(data)
    RunSpec.from_dict(data).with_overrides(["ion.energy_keV.gaussian.sigma=2"])
    assert data == snapshot


# --- TRIM.from_spec and inline (hashing) form ---------------------------------------------


def test_trim_from_spec_refuses_a_binned_spec():
    from srim import TRIM

    with pytest.raises(SpecError, match="srim.beam.run_beam"):
        TRIM.from_spec(RunSpec.from_dict({**distributed(), "beam": {"mode": "binned"}}))


def test_python_function_distribution_round_trips_and_inlines_its_source(tmp_path):
    data = distributed(
        energy_keV={"function": {"ref": "python:beam_funcs:ramp", "range": [1.0, 10.0]}}
    )
    spec = RunSpec.from_dict(data)
    spec.to_yaml(tmp_path / "run.yaml")
    again = RunSpec.from_yaml(tmp_path / "run.yaml")
    assert again.to_dict() == spec.to_dict()
    inline = spec.to_dict(inline=True)["ion"]["energy_keV"]["function"]
    assert "def ramp" in inline["source"]
    assert "source" not in spec.to_dict()["ion"]["energy_keV"]["function"]


def test_inline_histogram_carries_its_content():
    data = distributed(energy_keV={"histogram": {"edges": [1, 2, 3], "counts": [1, 3]}})
    spec = RunSpec.from_dict(data)
    assert spec.to_dict(inline=True)["ion"]["energy_keV"] == {
        "histogram": {"edges": [1.0, 2.0, 3.0], "counts": [1.0, 3.0]}
    }


# --- overrides that make a distribution fixed (scans, --set) -----------------------------


def binned_base(**ion):
    """A binned spec whose energy is a Gaussian with bins; the angle is fixed unless given."""
    data = distributed(**ion)
    data["beam"] = {"mode": "binned", "bins": {"energy_keV": 10}}
    return data


def test_sigma_zero_drops_the_binned_beam_and_matches_the_fixed_spec():
    overridden = RunSpec.from_dict(binned_base()).with_overrides(
        ["ion.energy_keV.gaussian.sigma=0"]
    )
    fixed = RunSpec.from_dict(base()).with_overrides(["ion.energy_keV=20"])
    assert not overridden.is_distributed
    assert "beam" not in overridden.to_dict()
    assert overridden.to_dict() == fixed.to_dict()


def test_sigma_zero_keeps_bins_of_the_still_distributed_parameter():
    data = distributed(angle_deg={"uniform": {"low": 0.0, "high": 10.0}})
    data["beam"] = {"mode": "binned", "bins": {"energy_keV": 10, "angle_deg": 4}}
    spec = RunSpec.from_dict(data).with_overrides(["ion.energy_keV.gaussian.sigma=0"])
    assert spec.is_distributed
    assert spec.to_dict()["beam"] == {"mode": "binned", "bins": {"angle_deg": 4}}


def test_user_written_binned_spec_without_distribution_is_still_refused():
    data = base()
    data["beam"] = {"mode": "binned", "bins": {"energy_keV": 10}}
    with pytest.raises(SpecError, match="beam"):
        RunSpec.from_dict(data)


def test_scan_over_sigma_with_zero_builds_all_points():
    from srim.scan import Scan

    scan = Scan(
        RunSpec.from_dict(binned_base()),
        grid={"ion.energy_keV.gaussian.sigma": [0, 2, 5]},
    )
    points = scan.points()
    assert len(points) == 3
    assert [p.spec.is_distributed for p in points] == [False, True, True]


def test_override_on_binned_spec_with_calculation_3_still_builds():
    data = binned_base()
    data["calculation"] = 3
    spec = RunSpec.from_dict(data).with_overrides(["number_ions=50"])
    assert spec.number_ions == 50 and spec.beam.mode == "binned"
    assert spec.calculation == 3


def test_sigma_override_keeps_binned_calculation_3():
    data = binned_base()
    data["calculation"] = 3
    spec = RunSpec.from_dict(data).with_overrides(["ion.energy_keV.gaussian.sigma=2"])
    assert spec.is_distributed and spec.beam.mode == "binned"
    assert spec.calculation == 3 and spec.beam.bins == {"energy_keV": 10}


def test_sigma_zero_on_calculation_3_binned_spec_prunes():
    data = binned_base()
    data["calculation"] = 3
    spec = RunSpec.from_dict(data).with_overrides(["ion.energy_keV.gaussian.sigma=0"])
    assert not spec.is_distributed and spec.beam is None


def test_exact_spec_with_a_non_ascii_description_is_refused():
    data = distributed()
    data["settings"]["description"] = "B in Au, 20 keV ± 1 keV"
    with pytest.raises(SpecError, match="settings.description must be ASCII"):
        RunSpec.from_dict(data)
    data["ion"]["energy_keV"] = 20.0  # a plain spec keeps its description as it is
    assert RunSpec.from_dict(data).settings["description"].endswith("± 1 keV")
