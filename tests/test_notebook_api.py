"""The library's reading of run folders for notebooks (no SRIM: the points are test_files/2)."""

import json
import shutil
from copy import deepcopy

import pytest
from run_folders import DONE, FIXTURE, finished_scan

from srim.beam import beam_scan
from srim.notebook import (
    LOADED_FILE,
    NOTEBOOK_DIR,
    RELOAD_FILE,
    LoadedRun,
    load_run,
    loaded_count,
    results_count,
)
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


def _spec():
    return RunSpec.from_dict(deepcopy(BORON))


def _grid():
    return Scan(_spec(), grid={"ion.energy_keV": [10.0, 20.0]}, seed=7)


def test_the_names_of_the_notebook_files():
    assert NOTEBOOK_DIR == ".pysrim2-notebook"
    assert RELOAD_FILE == "reload"
    assert LOADED_FILE == "loaded.json"


def test_a_finished_folder_loads_as_it_was_written(tmp_path):
    folder = tmp_path / "run"
    result = finished_scan(folder, beam_scan(_spec()), [DONE])
    result.to_csv(folder / "result")  # as the executor leaves a finished run
    run = load_run(folder)
    assert isinstance(run, LoadedRun)
    assert run.finished is True
    assert run.points() == [0]
    assert run.point(0).ioniz.df.columns[0] == "depth_A"


def test_an_unfinished_folder_loads_the_points_with_outputs(tmp_path):
    folder = tmp_path / "run"
    finished_scan(folder, _grid(), [DONE, "SRIM failed"])  # no result/ folder: not finished
    run = load_run(folder)
    assert run.finished is False
    assert run.count == 1
    assert run.points() == [0]
    assert run.point(0).ioniz.df.columns[0] == "depth_A"
    with pytest.raises(KeyError):
        run.point(1)


def test_mark_loaded_writes_the_count_and_loaded_count_reads_it(tmp_path):
    folder = tmp_path / "run"
    result = finished_scan(folder, beam_scan(_spec()), [DONE])
    result.to_csv(folder / "result")
    run = load_run(folder)
    assert loaded_count(folder) is None
    run.mark_loaded()
    assert loaded_count(folder) == run.count
    written = folder / NOTEBOOK_DIR / LOADED_FILE
    assert json.loads(written.read_text(encoding="utf-8")) == {"points": 1, "finished": True}
    assert not list((folder / NOTEBOOK_DIR).glob("*.tmp"))


def test_results_count_of_a_missing_folder_is_zero(tmp_path):
    assert results_count(tmp_path / "nowhere") == 0


def test_results_count_counts_the_points_with_outputs(tmp_path):
    folder = tmp_path / "run"
    finished_scan(folder, _grid(), [DONE, "SRIM failed"])
    assert results_count(folder) == 1


def test_a_broken_loaded_file_reads_as_none(tmp_path):
    folder = tmp_path / "run"
    (folder / NOTEBOOK_DIR).mkdir(parents=True)
    (folder / NOTEBOOK_DIR / LOADED_FILE).write_text("{not json", encoding="utf-8")
    assert loaded_count(folder) is None


def test_a_folder_without_scan_yaml_raises_a_readable_error(tmp_path):
    folder = tmp_path / "empty"
    folder.mkdir()
    with pytest.raises(FileNotFoundError, match="empty"):
        load_run(folder)


def test_a_stray_point_folder_is_counted_by_neither_count(tmp_path):
    folder = tmp_path / "run"
    finished_scan(folder, _grid(), [DONE, "SRIM failed"])
    stray = folder / "points" / "xyz"
    stray.mkdir(parents=True)
    (stray / "run.yaml").write_text("stray\n", encoding="utf-8")
    assert results_count(folder) == 1
    run = load_run(folder)
    assert run.count == 1
    assert run.points() == [0]


def test_a_failed_point_with_outputs_is_counted_by_both(tmp_path):
    folder = tmp_path / "run"
    scan = _grid()
    finished_scan(folder, scan, [DONE, "SRIM failed"])
    # the failed point's folder holds outputs (as after a crash that left them behind)
    point = scan.points()[1]
    rel = folder / "points" / point.id[:16]
    shutil.copytree(FIXTURE, rel)
    point.spec.to_yaml(rel / "run.yaml", provenance={"test": "count"})
    run = load_run(folder)
    assert results_count(folder) == run.count == 2
    assert run.points() == [0, 1]
    assert run.point(1).ioniz.df.columns[0] == "depth_A"


def test_results_count_of_a_broken_scan_yaml_is_zero(tmp_path):
    folder = tmp_path / "run"
    folder.mkdir()
    (folder / "scan.yaml").write_text("scan: [not a mapping\n", encoding="utf-8")
    assert results_count(folder) == 0


def test_the_loaded_file_is_readable_by_others(tmp_path):
    folder = tmp_path / "run"
    result = finished_scan(folder, beam_scan(_spec()), [DONE])
    result.to_csv(folder / "result")
    load_run(folder).mark_loaded()
    mode = (folder / NOTEBOOK_DIR / LOADED_FILE).stat().st_mode & 0o777
    assert mode == 0o644
