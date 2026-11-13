"""Run planning and the parallel advice of the app (Qt-free: runs without the app extra)."""

import math
from copy import deepcopy
from datetime import UTC, datetime

import pytest

from srim.app.model import from_spec, to_spec
from srim.app.runs import (
    CONTAINER_SECONDS,
    DEFAULT_IONS_PER_S,
    ParallelChoice,
    open_point,
    outputs_missing,
    parallel_advice,
    plan_run,
    remember_rate,
    run_folder,
    setup_path_for_run,
)
from srim.app.scan_model import scan_from_section, to_scan
from srim.app.settings import AppSettings
from srim.beam import beam_scan
from srim.scan import Scan
from srim.spec import RunSpec

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

NOW = datetime(2026, 11, 13, 9, 5, 7, tzinfo=UTC)


def spec(calculation=1, **changes):
    data = deepcopy(BORON)
    data["calculation"] = calculation
    data.update(changes)
    return RunSpec.from_dict(data)


def document(scan=None, calculation=1):
    doc = from_spec(spec(calculation))
    if scan is not None:
        doc["_scan"] = scan
    return doc


def scanned(calculation=1, **section):
    return document(scan_from_section(section), calculation)


def binned_spec(calculation=2):
    data = deepcopy(BORON)
    data["calculation"] = calculation
    data["ion"]["energy_keV"] = {"uniform": {"low": 10, "high": 20}}
    data["beam"] = {"mode": "binned", "bins": {"energy_keV": 5}}
    return RunSpec.from_dict(data)


# --- run folders ----------------------------------------------------------------------


def test_the_run_folder_is_named_after_the_setup_and_the_utc_time(tmp_path):
    setup = tmp_path / "gold.yaml"
    assert run_folder(setup, NOW) == tmp_path / "gold-runs" / "20261113T090507Z"


def test_a_clash_adds_a_number(tmp_path):
    setup = tmp_path / "gold.yaml"
    first = run_folder(setup, NOW)
    first.mkdir(parents=True)
    second = run_folder(setup, NOW)
    assert second == tmp_path / "gold-runs" / "20261113T090507Z-2"
    second.mkdir()
    assert run_folder(setup, NOW) == tmp_path / "gold-runs" / "20261113T090507Z-3"


# --- plan_run -------------------------------------------------------------------------


def test_a_plain_document_plans_the_beam_scan_of_its_spec():
    doc = document()
    scan = plan_run(doc, None, ParallelChoice())
    expected = beam_scan(to_spec(doc, None))
    assert scan.base.to_dict() == expected.base.to_dict()
    assert scan.keep_seed is True
    assert scan.ions == expected.ions
    assert [p.id for p in scan.points()] == [p.id for p in expected.points()]


def test_a_batch_size_from_the_choice_splits_a_plain_run():
    scan = plan_run(document(), None, ParallelChoice(workers=4, batch_ions=500))
    assert scan.batch_ions == 500
    assert scan.keep_seed is False


def test_a_document_with_a_scan_plans_that_scan():
    doc = scanned(ions=300, grid={"ion.energy_keV": [10.0, 20.0]}, seed=7)
    scan = plan_run(doc, None, ParallelChoice())
    expected = to_scan(doc)
    assert isinstance(scan, Scan)
    assert scan.base.to_dict() == expected.base.to_dict()
    assert [p.id for p in scan.points()] == [p.id for p in expected.points()]
    assert scan.ions == 300


def test_the_choice_batches_a_scan_with_fixed_ions_and_none_of_its_own():
    doc = scanned(ions=300, grid={"ion.energy_keV": [10.0, 20.0]}, seed=7)
    assert plan_run(doc, None, ParallelChoice(batch_ions=100)).batch_ions == 100


def test_a_scan_with_its_own_batch_size_keeps_it():
    doc = scanned(ions=300, grid={"ion.energy_keV": [10.0, 20.0]}, seed=7, batch_ions=50)
    assert plan_run(doc, None, ParallelChoice(batch_ions=100)).batch_ions == 50


def test_a_keep_seed_scan_is_not_split_by_the_choice():
    doc = scanned(ions=300, seed=5, keep_seed=True)
    scan = plan_run(doc, None, ParallelChoice(batch_ions=100))
    assert scan.keep_seed is True
    assert scan.batch_ions is None


# --- parallel advice ------------------------------------------------------------------


def test_a_quick_run_gets_no_advice():
    scan = Scan(spec(calculation=1), ions=1000, seed=1)
    assert parallel_advice(scan, 8, {}, False) is None


def test_full_cascades_without_a_remembered_rate_use_the_default():
    scan = Scan(spec(calculation=2), ions=100_000, seed=1)
    advice = parallel_advice(scan, 8, {}, False)
    assert advice.seconds == 100_000 / 100.0
    assert advice.units == 1
    assert advice.default_workers == 7


def test_a_remembered_rate_replaces_the_default():
    scan = Scan(spec(calculation=2), ions=100_000, seed=1)
    advice = parallel_advice(scan, 8, {"2": 50.0}, False)
    assert advice.seconds == 100_000 / 50.0
    # a fast remembered rate can take the run under the threshold: no advice
    assert parallel_advice(scan, 8, {"2": 1000.0}, False) is None


def test_the_rate_is_per_calculation():
    scan = Scan(spec(calculation=3), ions=100_000, seed=1)
    # a rate remembered for calculation 2 does not apply to calculation 3 (100 per second)
    assert parallel_advice(scan, 8, {"2": 1000.0}, False).seconds == 100_000 / 100.0
    assert DEFAULT_IONS_PER_S[1] == 2000.0


def test_split_is_offered_only_for_one_unbatched_point():
    one = Scan(spec(calculation=2), ions=100_000, seed=1)
    assert parallel_advice(one, 8, {}, False).can_split is True
    two = Scan(spec(calculation=2), grid={"ion.energy_keV": [10.0, 20.0]}, ions=100_000, seed=1)
    advice = parallel_advice(two, 8, {}, False)
    assert advice.can_split is False
    assert advice.units == 2
    batched = Scan(spec(calculation=2), ions=100_000, batch_ions=10_000, seed=1)
    assert parallel_advice(batched, 8, {}, False).can_split is False


def test_a_binned_beam_counts_its_bins_as_units():
    scan = Scan(binned_spec(), ions=100_000, seed=1)
    advice = parallel_advice(scan, 8, {}, False)
    assert advice.units == 5


def test_container_is_offered_only_past_an_hour_with_a_runtime():
    scan = Scan(spec(calculation=2), ions=int(CONTAINER_SECONDS * 100) + 100, seed=1)
    assert parallel_advice(scan, 8, {}, True).offer_container is True
    assert parallel_advice(scan, 8, {}, False).offer_container is False
    short = Scan(spec(calculation=2), ions=100_000, seed=1)
    assert parallel_advice(short, 8, {}, True).offer_container is False


def test_one_core_gets_no_advice():
    scan = Scan(spec(calculation=2), ions=100_000, seed=1)
    assert parallel_advice(scan, 1, {}, False) is None


def test_default_workers_leave_one_core_free_and_never_drop_below_one():
    scan = Scan(spec(calculation=2), ions=100_000, seed=1)
    assert parallel_advice(scan, 2, {}, False).default_workers == 1


def test_a_precision_target_is_costed_at_its_upper_bound():
    from srim.scan import Precision

    precision = Precision(target="sputter_yield", rel=0.05, max_ions=100_000)
    scan = Scan(spec(calculation=2), ions=precision, seed=1)
    advice = parallel_advice(scan, 8, {}, False)
    assert advice.seconds == 100_000 / 100.0
    assert advice.can_split is False


# --- remembered rates -----------------------------------------------------------------


def test_remember_rate_keeps_the_latest_value_per_calculation():
    settings = AppSettings()
    remember_rate(settings, 2, 150.0)
    remember_rate(settings, 2, 200.0)
    remember_rate(settings, 1, 3000.0)
    assert settings.ions_per_s == {"2": 200.0, "1": 3000.0}


@pytest.mark.parametrize("value", [0, -1.0, math.nan, math.inf, True])
def test_remember_rate_ignores_a_rate_that_is_not_usable(value):
    settings = AppSettings()
    remember_rate(settings, 2, value)
    assert settings.ions_per_s == {}


# --- open_point -----------------------------------------------------------------------


@pytest.fixture
def readers(monkeypatch):
    """Replace the two readers with recorders: each returns the folder it was given."""
    import srim.app.runs as runs

    monkeypatch.setattr(runs, "Results", lambda path: ("plain", path))
    monkeypatch.setattr(runs, "open_results", lambda path: ("batched", path))


def test_a_folder_without_units_is_read_as_plain_results(tmp_path, readers):
    (tmp_path / "points" / "abc").mkdir(parents=True)
    assert open_point(tmp_path, "points/abc") == ("plain", tmp_path / "points" / "abc")


def test_a_batched_point_is_read_as_merged_results(tmp_path, readers):
    point = tmp_path / "points" / "abc"
    point.mkdir(parents=True)
    (point / "units.csv").write_text("unit\n")
    assert open_point(tmp_path, "points/abc") == ("batched", point)


def test_run_samples_of_a_single_run_are_its_trim_dat_ions(tmp_path, boron_data, monkeypatch):
    from run_folders import DONE, finished_scan

    from srim.app import runs as runs_module
    from srim.beam.exact import sample_ions, write_trim_dat

    spec = RunSpec.from_dict(boron_data)
    scan = beam_scan(spec)
    folder = tmp_path / "setup-runs" / "stamp"
    result = finished_scan(folder, scan, [DONE])
    point_dir = folder / "points" / scan.points()[0].id[:16]
    write_trim_dat(
        sample_ions(spec, n=25, seed=2),
        point_dir / "TRIM.DAT",
        atomic_number=5,
        description="test",
        seed=2,
    )
    frame = runs_module.run_samples(result, folder)
    assert len(frame) == 25
    assert "energy_keV" in frame.columns


def test_run_samples_is_none_without_a_trim_dat_or_for_a_scan(tmp_path, boron_data):
    from run_folders import DONE, finished_scan

    from srim.app.runs import run_samples

    spec = RunSpec.from_dict(boron_data)
    folder = tmp_path / "plain"
    single = finished_scan(folder, beam_scan(spec), [DONE])
    assert run_samples(single, folder) is None  # no TRIM.DAT: no distribution to sample
    grid = Scan(spec, grid={"ion.energy_keV": [10.0, 20.0]}, seed=7)
    two = finished_scan(tmp_path / "grid", grid, [DONE, DONE])
    assert run_samples(two, tmp_path / "grid") is None


def test_run_samples_does_not_read_a_run_longer_than_the_plot_cap(
    tmp_path, boron_data, monkeypatch
):
    from run_folders import DONE, finished_scan

    from srim.app import runs as runs_module

    spec = RunSpec.from_dict({**boron_data, "number_ions": runs_module.PLOT_ROW_CAP + 1})
    folder = tmp_path / "big"
    result = finished_scan(folder, beam_scan(spec, batch_ions=99_999), [DONE])

    def must_not_read(_results):
        raise AssertionError("the TRIM.DAT of a run this long must not be read")

    monkeypatch.setattr(runs_module, "sampled_ions", must_not_read)
    assert runs_module.run_samples(result, folder) is None


# -- reading a run folder back (a finished run, or one that stopped or crashed) -------------


def _two_point_folder(tmp_path, boron_data, outcomes):
    """A scan folder of two points with ``outcomes`` (run_folders.DONE or an error text)."""
    from run_folders import finished_scan

    from srim.app.scan_model import new_scan

    scan_section = new_scan()
    scan_section["grid"] = {"ion.energy_keV": [10.0, 20.0]}
    scan_section["ions"] = 10
    scan = to_scan({**deepcopy(boron_data), "_scan": scan_section}, tmp_path)
    result = finished_scan(tmp_path / "run", scan, outcomes)
    result.to_csv(tmp_path / "run" / "result")  # as the executor leaves a finished run
    return result


def test_a_finished_run_folder_is_read_as_finished(tmp_path, boron_data):
    from run_folders import DONE

    from srim.app.runs import UNFINISHED_TEXT, read_run

    _two_point_folder(tmp_path, boron_data, [DONE, "SRIM failed"])
    result, finished = read_run(tmp_path / "run")
    assert finished is True
    assert list(result.table["status"]) == ["done", "failed"]
    assert UNFINISHED_TEXT == "This run did not finish; showing the points that have results."


def test_a_run_folder_without_its_result_shows_the_points_with_outputs(tmp_path, boron_data):
    import shutil

    from run_folders import DONE

    from srim.app.runs import read_run

    _two_point_folder(tmp_path, boron_data, [DONE, DONE])
    shutil.rmtree(tmp_path / "run" / "result")
    shutil.rmtree(tmp_path / "run" / "points" / _point_id(tmp_path / "run", 1))
    result, finished = read_run(tmp_path / "run")
    assert finished is False
    assert list(result.table["status"]) == ["done", "missing"]
    assert result.table.loc[0, "path"].startswith("points/")
    assert result.table["path"].isna().tolist() == [False, True]


def test_the_state_of_a_loaded_run_has_the_final_statuses(tmp_path, boron_data):
    from run_folders import DONE

    from srim.app.runs import loaded_state, read_run

    _two_point_folder(tmp_path, boron_data, [DONE, "SRIM failed"])
    result, _finished = read_run(tmp_path / "run")
    state = loaded_state(result, tmp_path / "run")
    assert state.finished is True
    assert state.counts()["done"] == 1
    assert state.counts()["failed"] == 1
    assert state.failed_point().index == 1
    assert state.scan_dir == tmp_path / "run"


def test_the_state_of_an_unfinished_run_leaves_the_missing_points_queued(tmp_path, boron_data):
    import shutil

    from run_folders import DONE

    from srim.app.runs import loaded_state, read_run

    _two_point_folder(tmp_path, boron_data, [DONE, DONE])
    shutil.rmtree(tmp_path / "run" / "result")
    shutil.rmtree(tmp_path / "run" / "points" / _point_id(tmp_path / "run", 1))
    result, _finished = read_run(tmp_path / "run")
    state = loaded_state(result, tmp_path / "run")
    assert state.counts()["done"] == 1
    assert state.counts()["queued"] == 1


def _point_id(folder, index):
    """The folder name (``id[:16]``) of point ``index`` of the scan.yaml in ``folder``."""
    from srim.scan import Scan

    return Scan.from_yaml(folder / "scan.yaml").points()[index].id[:16]


def test_the_default_save_as_of_a_run_folder_is_the_setup_beside_its_runs(tmp_path):
    setup = tmp_path / "boron" / "run.yaml"
    folder = run_folder(setup, datetime(2026, 11, 12, tzinfo=UTC))
    assert setup_path_for_run(folder / "scan.yaml") == setup
    plain = tmp_path / "moved" / "20261112T000000Z" / "scan.yaml"
    assert setup_path_for_run(plain) == tmp_path / "moved.yaml"


def test_a_finished_run_whose_point_folders_are_gone_has_its_outputs_missing(tmp_path, boron_data):
    import shutil

    from run_folders import DONE

    from srim.app.runs import read_run

    _two_point_folder(tmp_path, boron_data, [DONE, DONE])
    result, _finished = read_run(tmp_path / "run")
    assert not outputs_missing(result, tmp_path / "run")
    shutil.rmtree(tmp_path / "run" / "points" / _point_id(tmp_path / "run", 1))
    assert not outputs_missing(result, tmp_path / "run")  # the other point still has outputs
    shutil.rmtree(tmp_path / "run" / "points")
    assert outputs_missing(result, tmp_path / "run")


def test_a_run_where_no_point_finished_has_no_outputs_missing(tmp_path, boron_data):
    from srim.app.runs import read_run

    _two_point_folder(tmp_path, boron_data, ["SRIM failed", "SRIM failed"])
    result, _finished = read_run(tmp_path / "run")
    assert not outputs_missing(result, tmp_path / "run")
    assert not outputs_missing(None, tmp_path / "run")
