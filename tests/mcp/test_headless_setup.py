"""The headless backend edits the run setup without a window: values, sections, files, undo."""

import pytest

from srim.mcp.backend import BackendError, NeedsApp
from srim.mcp.headless import HeadlessBackend
from srim.spec import RunSpec


def n15_ion():
    return {"symbol": "N", "mass_amu": 15.0001, "energy_keV": 40}


def n15_target():
    return [
        {
            "thickness_nm": 200,
            "density_g_cm3": 2.2,
            "elements": [
                {"symbol": "Si", "stoich": 1},
                {"symbol": "O", "stoich": 2},
            ],
        },
        {
            "thickness_nm": 1000,
            "density_g_cm3": 2.33,
            "elements": [{"symbol": "Si", "stoich": 1}],
        },
    ]


def valid_setup(backend):
    """The N-15 example, set up through the backend (ion first, then the layers)."""
    backend.set_section("ion", n15_ion())
    backend.set_section("target", n15_target())
    return backend


def test_the_n15_example_can_be_set_up(tmp_path):
    b = HeadlessBackend(tmp_path)
    b.set_section("ion", n15_ion())
    b.set_section("target", n15_target())
    setup = b.get_setup()
    assert setup["setup"]["ion"]["energy_keV"] == 40
    assert setup["setup"]["ion"]["symbol"] == "N"
    assert setup["setup"]["scan"] is None
    assert not [p for p in setup["problems"] if p["blocking"]]


def test_a_bad_value_changes_nothing(tmp_path):
    b = valid_setup(HeadlessBackend(tmp_path))
    before = b.get_setup()
    with pytest.raises(BackendError, match="energy_keV"):
        b.set_values(["ion.energy_keV=40", "ion.energy_keV=forty"])
    assert b.get_setup() == before


def test_a_bad_value_in_a_section_is_refused_even_before_the_setup_is_complete(tmp_path):
    b = HeadlessBackend(tmp_path)
    before = b.get_setup()
    with pytest.raises(BackendError, match="energy_keV"):
        b.set_section("ion", {"symbol": "N", "energy_keV": "forty"})
    assert b.get_setup() == before


def test_an_unknown_key_is_refused(tmp_path):
    with pytest.raises(BackendError, match="unknown"):
        HeadlessBackend(tmp_path).set_values(["ion.colour=red"])


def test_a_key_without_a_value_is_refused(tmp_path):
    with pytest.raises(BackendError, match="dotted.key=value"):
        HeadlessBackend(tmp_path).set_values(["ion.energy_keV"])


def test_set_values_changes_a_value_of_a_complete_setup(tmp_path):
    b = valid_setup(HeadlessBackend(tmp_path))
    setup = b.set_values(["ion.energy_keV=30", "target.0.thickness_nm=50"])
    assert setup["setup"]["ion"]["energy_keV"] == 30
    assert setup["setup"]["target"][0]["thickness_nm"] == 50
    assert setup["modified"] is True


def test_set_values_works_on_a_setup_that_is_not_complete_yet(tmp_path):
    b = HeadlessBackend(tmp_path)
    setup = b.set_values(["ion.symbol=N", "ion.energy_keV=40"])
    assert setup["setup"]["ion"]["energy_keV"] == 40
    assert any("layer" in p["text"].lower() for p in setup["problems"])


def test_the_scan_section_is_set_through_the_scan_model(tmp_path):
    b = valid_setup(HeadlessBackend(tmp_path))
    setup = b.set_values(["scan.grid.ion.energy_keV=[20, 40]", "scan.ions=50"])
    scan = setup["setup"]["scan"]
    assert scan["ions"] == 50
    assert scan["grid"] == {"ion.energy_keV": [20, 40]}


def test_set_section_scan_none_makes_a_single_run(tmp_path):
    b = valid_setup(HeadlessBackend(tmp_path))
    b.set_values(["scan.ions=50", "scan.grid.ion.energy_keV=[20, 40]"])
    assert b.set_section("scan", None)["setup"]["scan"] is None


def test_set_section_run_sets_the_run_settings_and_refuses_unknown_keys(tmp_path):
    b = valid_setup(HeadlessBackend(tmp_path))
    setup = b.set_section("run", {"number_ions": 500, "seed": 7})
    assert setup["setup"]["number_ions"] == 500
    assert setup["setup"]["seed"] == 7
    with pytest.raises(BackendError, match="colour"):
        b.set_section("run", {"colour": "red"})


def test_an_unknown_section_is_refused(tmp_path):
    with pytest.raises(BackendError, match="unknown section"):
        HeadlessBackend(tmp_path).set_section("beam_line", {})


def test_relative_paths_and_spaces(tmp_path):
    b = valid_setup(HeadlessBackend(tmp_path))
    b.save_setup("my runs/n15 in sio2.yaml")
    assert (tmp_path / "my runs" / "n15 in sio2.yaml").is_file()
    opened = HeadlessBackend(tmp_path).open_setup("my runs/n15 in sio2.yaml")
    assert opened["path"].endswith("n15 in sio2.yaml")
    assert opened["setup"]["ion"]["energy_keV"] == 40


def test_save_without_a_path_needs_a_path_first(tmp_path):
    with pytest.raises(BackendError, match="path"):
        valid_setup(HeadlessBackend(tmp_path)).save_setup(None)


def test_a_setup_with_a_blocking_problem_is_not_saved(tmp_path):
    b = HeadlessBackend(tmp_path)
    b.set_section("ion", n15_ion())
    with pytest.raises(BackendError):
        b.save_setup("early.yaml")
    assert not (tmp_path / "early.yaml").exists()


def test_would_replace_only_files_from_elsewhere(tmp_path):
    (tmp_path / "theirs.yaml").write_text("x")
    b = HeadlessBackend(tmp_path)
    assert b.would_replace("theirs.yaml") is True
    valid_setup(b)
    b.save_setup("mine.yaml")
    assert b.would_replace("mine.yaml") is False
    assert b.would_replace("never-written.yaml") is False


def test_has_unsaved_changes_follows_the_save(tmp_path):
    b = valid_setup(HeadlessBackend(tmp_path))
    assert b.has_unsaved_changes() is True
    b.save_setup("saved.yaml")
    assert b.has_unsaved_changes() is False
    b.set_values(["ion.energy_keV=30"])
    assert b.has_unsaved_changes() is True


def test_undo_and_redo_need_the_app(tmp_path):
    with pytest.raises(NeedsApp):
        HeadlessBackend(tmp_path).undo()
    with pytest.raises(NeedsApp):
        HeadlessBackend(tmp_path).redo()


def trim_in(spec, folder):
    """The TRIM.IN text a script gets from ``spec``."""
    from srim.srim import TRIM

    folder.mkdir(parents=True, exist_ok=True)
    TRIM.from_spec(spec)._write_input_files(str(folder))
    return (folder / "TRIM.IN").read_text(encoding="latin-1")


def test_a_saved_setup_is_the_scripted_one(tmp_path):
    # the file the backend saves gives the same TRIM.IN as the same setup built in a script
    b = valid_setup(HeadlessBackend(tmp_path))
    b.set_section("run", {"number_ions": 200, "seed": 12345})
    b.save_setup("n15.yaml")
    saved = RunSpec.from_yaml(tmp_path / "n15.yaml")
    scripted = RunSpec.from_dict(
        {
            "version": 1,
            "ion": {"symbol": "N", "mass_amu": 15.0001, "energy_keV": 40.0, "angle_deg": 0.0},
            "number_ions": 200,
            "calculation": 1,
            "seed": 12345,
            "settings": {},
            "target": [
                {
                    "name": "",
                    "thickness_nm": 200.0,
                    "density_g_cm3": 2.2,
                    "phase": 0,
                    "elements": [
                        {"symbol": "Si", "stoich": 1.0},
                        {"symbol": "O", "stoich": 2.0},
                    ],
                },
                {
                    "name": "",
                    "thickness_nm": 1000.0,
                    "density_g_cm3": 2.33,
                    "phase": 0,
                    "elements": [{"symbol": "Si", "stoich": 1.0}],
                },
            ],
        }
    )
    assert trim_in(saved, tmp_path / "from_file") == trim_in(scripted, tmp_path / "scripted")


# --- the live option: set_section("run", {"live": bool}) and get_setup's "live" --------------


def test_the_live_option_is_off_in_get_setup_and_set_section_turns_it_on_and_off(tmp_path):
    b = valid_setup(HeadlessBackend(tmp_path))
    assert b.get_setup()["setup"]["live"] is False
    assert b.set_section("run", {"live": True})["setup"]["live"] is True
    assert b.get_setup()["setup"]["live"] is True
    assert b.set_section("run", {"live": False})["setup"]["live"] is False


def test_the_live_option_is_a_run_setting_that_must_be_a_bool(tmp_path):
    b = valid_setup(HeadlessBackend(tmp_path))
    with pytest.raises(BackendError, match="live"):
        b.set_section("run", {"live": "yes"})
    assert b.get_setup()["setup"]["live"] is False


def test_the_live_option_is_refused_for_a_scan_and_says_why(tmp_path):
    b = valid_setup(HeadlessBackend(tmp_path))
    b.set_section("scan", {"grid": {"ion.energy_keV": [10, 20]}, "ions": 50})
    with pytest.raises(BackendError, match="scan"):
        b.set_section("run", {"live": True})
    assert b.get_setup()["setup"]["live"] is False


def test_a_saved_live_setup_is_reopened_with_the_option_on(tmp_path):
    b = valid_setup(HeadlessBackend(tmp_path))
    b.set_section("run", {"number_ions": 200, "live": True})
    b.save_setup("live.yaml")
    reopened = HeadlessBackend(tmp_path / "again")
    reopened.open_setup(str(tmp_path / "live.yaml"))
    assert reopened.get_setup()["setup"]["live"] is True


def test_an_edit_of_a_value_keeps_the_live_option(tmp_path):
    b = valid_setup(HeadlessBackend(tmp_path))
    b.set_section("run", {"live": True})
    b.set_values(["number_ions=150"])
    assert b.get_setup()["setup"]["live"] is True
