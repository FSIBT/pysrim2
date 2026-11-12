"""The run setup document: defaults, cleaning, the problems list, history and the save copies.

Nothing here imports PySide6, so these tests run without the app extra.
"""

import warnings

import pytest

from srim import TRIM, Ion, Layer, Target
from srim.app.guidance import Guidance
from srim.app.model import (
    BINNED_AUTO,
    EXACT_TEXT,
    History,
    best_unit,
    clean,
    default_beam_mode,
    from_spec,
    is_distributed,
    new_document,
    new_element,
    new_layer,
    prepare_for_save,
    problems,
    target_for_key,
    to_spec,
)
from srim.spec import RunSpec, SpecError


def boron_trim():
    """The boron reference case of docs/first-simulation.md."""
    gold = Layer.from_formula("Au", density=19.3, width=1000.0)  # 100 nm
    silicon = Layer.from_formula("Si", density=2.33, width=10000.0)  # 1000 nm
    return TRIM(
        Target([gold, silicon]),
        Ion("B", energy=20e3),
        number_ions=200,
        angle_ions=7.0,
        ranges=1,
        random_seed=12345,
    )


def boron_dict():
    return boron_trim().to_spec().to_dict()


def boron_doc():
    """The same case as the editors hold it."""
    return {
        "version": 1,
        "ion": {"symbol": "B", "energy_keV": 20.0, "angle_deg": 7.0},
        "number_ions": 200,
        "calculation": 1,
        "seed": 12345,
        "settings": {"description": "pysrim run", "ranges": 1},
        "target": [
            {
                "name": "Au",
                "thickness_nm": 100.0,
                "density_g_cm3": 19.3,
                "phase": 0,
                "elements": [new_element("Au")],
            },
            {
                "name": "Si",
                "thickness_nm": 1000.0,
                "density_g_cm3": 2.33,
                "phase": 0,
                "elements": [new_element("Si")],
            },
        ],
    }


def test_a_new_document_says_what_is_missing_in_order():
    texts = [g.text for g in problems(new_document())]
    assert texts == [
        "Choose the ion: pick an element on the Beam tab",
        "Enter the ion energy on the Beam tab",
        "Add a target layer on the Geometry tab",
    ]


def test_a_layer_without_density_points_at_its_cell():
    doc = boron_doc()
    doc["target"][0]["density_g_cm3"] = None
    (only,) = problems(doc)
    assert only.text == "Layer 1 (Au): enter the density"
    assert only.target == "geometry/0/density"


def test_a_layer_without_elements_or_thickness_and_an_unnamed_layer():
    doc = boron_doc()
    doc["target"][1] = new_layer()
    texts = [(g.text, g.target) for g in problems(doc)]
    assert texts == [
        ("Layer 2 (new layer): add at least one element", "geometry/1/elements"),
        ("Layer 2 (new layer): enter the density", "geometry/1/density"),
        ("Layer 2 (new layer): enter the thickness", "geometry/1/thickness"),
    ]


def test_an_element_without_an_amount_is_named_by_symbol():
    doc = boron_doc()
    doc["target"][1]["elements"][0]["stoich"] = None
    (only,) = problems(doc)
    assert only.text == "Layer 2 (Si): enter the amount of Si"
    assert only.target == "geometry/1/elements"


def test_library_errors_point_at_the_field():
    doc = boron_doc()
    doc["ion"]["angle_deg"] = 95.0
    (only,) = problems(doc)
    assert only.text.startswith("ion.angle_deg must be in [0, 90)")
    assert only.target == "beam/angle_deg"


def test_a_complete_document_has_no_problems():
    assert problems(boron_doc()) == []


def test_target_for_key():
    assert target_for_key("spec.target[2].elements[0].stoich") == "geometry/2/elements"
    assert target_for_key("ion.position.depth_nm") == "beam/depth_nm"
    assert target_for_key("ion.position") == "beam/y_nm"
    assert target_for_key("settings.collisions") == "settings/collisions"
    assert target_for_key("calculation") == "settings/calculation"
    assert target_for_key("seed") == "settings/seed"
    assert target_for_key("target[1].thickness_nm") == "geometry/1/thickness"
    assert target_for_key("target") == "geometry"
    assert target_for_key("beam.max_bins") == "beam/mode"
    assert target_for_key("a histogram needs at least two edges") is None


def test_a_complete_document_gives_the_same_trim_in_as_the_script():
    spec = to_spec(boron_doc())
    assert spec.to_dict() == boron_trim().to_spec().to_dict()


def test_isotope_and_natural():
    doc = boron_doc()
    doc["ion"]["mass_amu"] = 10.0129370
    assert to_spec(doc).ion.mass_amu == pytest.approx(10.0129370)
    del doc["ion"]["mass_amu"]
    assert "mass_amu" not in clean(doc)["ion"]


def test_beam_key_is_dropped_for_a_plain_beam_and_added_for_monolayer():
    doc = boron_doc()
    doc["beam"] = {"mode": "exact"}
    assert "beam" not in clean(doc)
    doc["ion"]["energy_keV"] = {"gaussian": {"mean": 20.0, "sigma": 1.0}}
    doc["calculation"] = 3
    del doc["beam"]
    assert clean(doc)["beam"] == {"mode": "binned"}
    assert default_beam_mode(doc) == ("binned", BINNED_AUTO)


def test_default_beam_mode_is_exact_for_calculations_one_and_two():
    doc = boron_doc()
    doc["ion"]["energy_keV"] = {"gaussian": {"mean": 20.0, "sigma": 1.0}}
    assert default_beam_mode(doc) == ("exact", EXACT_TEXT)
    doc["calculation"] = 2
    assert default_beam_mode(doc)[0] == "exact"


def test_is_distributed_for_mappings_azimuth_and_position():
    doc = boron_doc()
    assert not is_distributed(doc)
    doc["ion"]["angle_deg"] = {"uniform": {"low": 0.0, "high": 10.0}}
    assert is_distributed(doc)
    doc = boron_doc()
    doc["ion"]["azimuth_deg"] = 0.0
    assert is_distributed(doc)


def test_too_many_bins_is_a_note_not_a_blocker():
    doc = boron_doc()
    doc["ion"]["energy_keV"] = {"gaussian": {"mean": 20.0, "sigma": 1.0}}
    doc["ion"]["angle_deg"] = {"uniform": {"low": 0.0, "high": 10.0}}
    doc["beam"] = {"mode": "binned", "bins": {"energy_keV": 20, "angle_deg": 20}}
    (note,) = problems(doc)
    assert note.blocking is False and note.target == "beam/mode"


def test_round_trip_keeps_what_the_editors_do_not_offer():
    spec = RunSpec.from_dict(
        {
            **boron_dict(),
            "calculation": 4,
            "beam": {"mode": "binned"},  # calculation 4 with a distributed beam must be binned
            "settings": {"plot_mode": 0},
            "ion": {
                "symbol": "B",
                "energy_keV": {"histogram": {"edges": [19, 20, 21], "counts": [1, 2]}},
            },
        }
    )
    assert to_spec(from_spec(spec)).to_dict() == spec.to_dict()


def test_from_spec_adds_the_layer_unit():
    unit = [layer["_unit"] for layer in from_spec(boron_trim().to_spec())["target"]]
    assert unit == ["nm", "µm"]


def test_history_undo_redo_and_modified():
    history, doc = History(), new_document()
    history.mark_saved(doc)
    history.push(doc)
    after = {**doc, "seed": 5}
    assert history.is_modified(after)
    assert history.undo(after) == doc and history.can_redo
    assert history.redo(doc) == after


def test_history_is_cleared_and_a_new_edit_drops_redo():
    history, doc = History(), new_document()
    history.push(doc)
    history.undo({**doc, "seed": 1})
    assert history.can_redo
    history.push(doc)
    assert not history.can_redo
    history.clear()
    assert not history.can_undo and not history.can_redo


def test_history_has_a_limit_and_an_unedited_document_is_not_modified():
    history = History(limit=2)
    for seed in range(4):
        history.push({**new_document(), "seed": seed})
    assert history.undo(new_document()) == {**new_document(), "seed": 3}
    assert history.undo(new_document()) == {**new_document(), "seed": 2}
    assert history.undo(new_document()) is None
    assert not History().is_modified(new_document())


def test_prepare_for_save_copies_a_histogram_next_to_the_file(tmp_path):
    source = tmp_path / "elsewhere" / "spread.txt"
    source.parent.mkdir()
    source.write_text("19 1\n20 2\n21 1\n", encoding="utf-8")
    doc = boron_doc()
    doc["ion"]["energy_keV"] = {"histogram": {"file": str(source)}}
    saved, copies = prepare_for_save(doc, tmp_path / "run")
    assert saved["ion"]["energy_keV"]["histogram"]["file"] == "beam/spread.txt"
    assert copies == [(source, tmp_path / "run" / "beam" / "spread.txt")]
    assert doc["ion"]["energy_keV"]["histogram"]["file"] == str(source)  # input unchanged


def test_prepare_for_save_renames_a_second_file_of_the_same_name(tmp_path):
    first, second = tmp_path / "a" / "spread.txt", tmp_path / "b" / "spread.txt"
    for path in (first, second):
        path.parent.mkdir()
        path.write_text("19 1\n20 2\n", encoding="utf-8")
    doc = boron_doc()
    doc["ion"]["energy_keV"] = {"histogram": {"file": str(first)}}
    doc["ion"]["angle_deg"] = {"histogram": {"file": str(second)}}
    saved, copies = prepare_for_save(doc, tmp_path / "run")
    assert saved["ion"]["angle_deg"]["histogram"]["file"] == "beam/spread-2.txt"
    assert copies == [
        (first, tmp_path / "run" / "beam" / "spread.txt"),
        (second, tmp_path / "run" / "beam" / "spread-2.txt"),
    ]


def test_best_unit():
    assert [best_unit(v) for v in (0.5, 10, 2500, 1e7)] == ["Å", "nm", "µm", "mm"]


def test_guidance_blocking_defaults_to_true():
    assert Guidance("x").blocking is True


def test_a_broken_histogram_is_a_problem_naming_the_field():
    doc = boron_doc()
    doc["ion"]["energy_keV"] = {"histogram": {"edges": [19.0], "counts": [1.0]}}
    (only,) = problems(doc)
    assert "at least two edges" in only.text
    assert only.target == "beam/energy_keV"


def test_library_warnings_are_not_left_to_the_terminal():
    doc = boron_doc()
    with warnings.catch_warnings(record=True) as seen:
        warnings.simplefilter("always")
        problems(doc)
    assert seen == []


def test_to_spec_raises_spec_error_for_a_bad_document():
    doc = boron_doc()
    doc["ion"]["energy_keV"] = -1.0
    with pytest.raises(SpecError):
        to_spec(doc)


def test_a_library_message_about_a_field_with_a_digit_is_placed(boron_data):
    # density_g_cm3 has digits; the key in the library's message must be read whole.
    boron_data["target"][0]["density_g_cm3"] = -1.0
    found = [p for p in problems(boron_data) if p.blocking]
    assert found and found[0].target == "geometry/0/density"
    assert target_for_key("target[0].density_g_cm3") == "geometry/0/density"
