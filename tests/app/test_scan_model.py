"""The scan section of the run setup, as plain data (Qt-free: runs without the app extra)."""

from copy import deepcopy

import pytest

from srim.app.guidance import Guidance
from srim.app.model import History, clean, from_spec, problems, target_for_key, to_spec
from srim.app.scan_model import (
    SCAN_KEYS,
    new_scan,
    scan_from_section,
    scan_keys,
    section_from_file,
    to_scan,
    values_from_range,
)
from srim.scan import Precision, Scan
from srim.spec import RunSpec, SpecError

BORON = {
    "version": 1,
    "ion": {"symbol": "B", "energy_keV": 20.0, "angle_deg": 7.0},
    "number_ions": 200,
    "calculation": 1,
    "seed": 12345,
    "settings": {},
    "target": [
        {
            "name": "gold",
            "thickness_nm": 100.0,
            "density_g_cm3": 19.3,
            "phase": 0,
            "elements": [
                {
                    "symbol": "Au",
                    "stoich": 1.0,
                    "displacement_eV": 25.0,
                    "lattice_eV": 3.0,
                    "surface_eV": 3.0,
                }
            ],
        }
    ],
}


def document(scan=None):
    doc = from_spec(RunSpec.from_dict(deepcopy(BORON)))
    if scan is not None:
        doc["_scan"] = scan
    return doc


def scanned(**section):
    """A document with a scan of ``section`` (the keys of the ``scan:`` section, as in a file)."""
    return document(scan_from_section(section))


# --- ranges ---------------------------------------------------------------------------


def test_a_log_range_has_equal_ratios_and_exact_ends():
    values = values_from_range(10, 100, 3, log=True)
    assert values[0] == 10.0 and values[-1] == 100.0
    assert values[1] == pytest.approx(31.6227766, rel=1e-7)


def test_a_linear_range_includes_both_ends():
    assert values_from_range(0, 1, 5, False) == [0.0, 0.25, 0.5, 0.75, 1.0]


@pytest.mark.parametrize(
    "start, stop, steps, log",
    [(0, 1, 5, True), (-1, 1, 5, True), (1, 10, 1, False), (1, 10, 0, False)],
)
def test_a_bad_range_is_a_value_error(start, stop, steps, log):
    with pytest.raises(ValueError):
        values_from_range(start, stop, steps, log=log)


# --- keys --------------------------------------------------------------------------


def test_scan_keys_are_the_beam_then_each_layer_with_labels():
    assert [key for key, _ in SCAN_KEYS] == ["ion.energy_keV", "ion.angle_deg"]
    assert scan_keys(document()) == [
        ("ion.energy_keV", "Ion energy (keV)"),
        ("ion.angle_deg", "Ion angle (degrees)"),
        ("target.0.thickness_nm", "Layer 1 (gold): thickness (nm)"),
        ("target.0.density_g_cm3", "Layer 1 (gold): density (g/cm3)"),
    ]


def test_every_offered_key_is_accepted_by_the_library():
    doc = document()
    base = to_spec(doc)
    for key, _ in scan_keys(doc):
        base.with_overrides([f"{key}=5.0"])  # raises SpecError for an unknown key


def test_a_bracketed_layer_key_is_accepted_by_the_library_too():
    base = to_spec(document())
    changed = base.with_overrides(["target[0].thickness_nm=50"])
    assert changed.to_dict()["target"][0]["thickness_nm"] == 50.0


# --- the section --------------------------------------------------------------------


def test_a_new_scan_is_empty_and_has_the_default_extractor():
    assert new_scan() == {
        "grid": {},
        "extract": ["implant_summary"],
        "ions": None,
        "batch_ions": None,
        "seed": None,
        "keep_seed": False,
    }


def test_section_keys_are_normalised_and_missing_extract_is_the_default():
    section = scan_from_section({"grid": {"target[0].thickness_nm": [50, 100]}})
    assert section["grid"] == {"target.0.thickness_nm": [50, 100]}
    assert section["extract"] == ["implant_summary"]


def test_a_section_with_an_unknown_key_is_refused_at_open():
    with pytest.raises(SpecError, match="unknown key 'scan.gird'"):
        scan_from_section({"gird": {}})


def test_ions_and_precision_together_are_refused():
    with pytest.raises(SpecError, match="either ions or precision"):
        scan_from_section({"ions": 100, "precision": {"target": "x", "rel": 0.1}})


def test_a_section_that_is_not_a_mapping_is_refused_by_section_from_file(tmp_path):
    path = tmp_path / "run.yaml"
    path.write_text("version: 1\nspec: {}\nscan: [1, 2]\n", encoding="utf-8")
    with pytest.raises(SpecError, match="scan must be a mapping"):
        section_from_file(path)


def test_section_from_file_is_none_for_a_plain_run(tmp_path):
    path = tmp_path / "run.yaml"
    RunSpec.from_dict(deepcopy(BORON)).to_yaml(path)
    assert section_from_file(path) is None


# --- the Scan --------------------------------------------------------------------------


def test_a_document_with_a_grid_gives_a_scan_with_two_points_and_no_problems():
    doc = scanned(grid={"ion.energy_keV": [10, 20]}, extract=["implant_summary"], ions=100)
    scan = to_scan(doc)
    assert isinstance(scan, Scan)
    assert [p.params for p in scan.points()] == [{"ion.energy_keV": 10}, {"ion.energy_keV": 20}]
    assert problems(doc) == []


def test_clean_and_to_spec_ignore_the_scan():
    plain = document()
    scanned_doc = scanned(grid={"ion.energy_keV": [10, 20]}, ions=100)
    assert "_scan" not in clean(scanned_doc)
    assert clean(scanned_doc) == clean(plain)
    assert to_spec(scanned_doc).to_dict() == to_spec(plain).to_dict()


def test_an_empty_grid_asks_for_a_value_to_vary():
    doc = scanned(ions=100)
    assert [(g.text, g.target) for g in problems(doc)] == [
        ("Add a value to vary on the Scan tab", "scan/vary")
    ]


def test_missing_ions_asks_for_the_ions_per_point():
    doc = scanned(grid={"ion.energy_keV": [10, 20]})
    assert [(g.text, g.target) for g in problems(doc)] == [
        ("Enter the ions per point on the Scan tab", "scan/ions")
    ]


def test_a_precision_target_replaces_the_ions_question():
    doc = scanned(
        grid={"ion.energy_keV": [10]},
        precision={"target": "sputter_yield", "rel": 0.05, "min_ions": 100, "max_ions": 1000},
    )
    assert problems(doc) == []
    assert isinstance(to_scan(doc).ions, Precision)


def test_a_grid_value_the_library_refuses_points_at_the_grid():
    doc = scanned(grid={"ion.nonsense": [1]}, ions=100)
    (problem,) = problems(doc)
    assert "unknown key" in problem.text
    assert problem.target == "scan/vary"


def test_an_ion_count_the_library_refuses_points_at_the_ions():
    doc = scanned(grid={"ion.energy_keV": [10]}, ions=0)
    (problem,) = problems(doc)
    assert problem.target == "scan/ions"


def test_a_missing_base_value_is_reported_with_the_scan_questions():
    doc = scanned(ions=100)
    doc["target"][0]["thickness_nm"] = None
    texts = [g.text for g in problems(doc)]
    assert "Layer 1 (gold): enter the thickness" in texts
    assert "Add a value to vary on the Scan tab" in texts


def test_target_for_key_maps_scan_keys_to_tabs():
    assert target_for_key("scan.grid.ion.energy_keV") == "scan/vary"
    assert target_for_key("scan.points[1]") == "scan/vary"
    assert target_for_key("scan.precision.rel") == "scan/precision"
    assert target_for_key("scan.ions") == "scan/ions"
    assert target_for_key("scan.keep_seed") == "scan/keep_seed"


def test_history_sees_a_change_to_the_scan():
    doc = document(new_scan())
    history = History()
    history.mark_saved(doc)
    changed = deepcopy(doc)
    changed["_scan"]["ions"] = 500
    assert history.is_modified(changed)
    assert not history.is_modified(doc)


def test_problems_are_guidance_objects():
    assert all(isinstance(g, Guidance) for g in problems(scanned()))
