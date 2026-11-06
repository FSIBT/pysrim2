"""Scan folders: the marker file that ``scan clean --all`` requires, and the lock that a
running scan holds (a second scan in the same folder is refused, nothing is written)."""

from pathlib import Path

import pytest

from srim.output import Results
from srim.scan import Scan, prepare, run_scan
from srim.scan.executor import LOCK_NAME, MARKER_NAME, ScanBusy, scan_lock
from srim.spec import RunSpec

FIXTURE = Path(__file__).resolve().parents[2] / "test_files" / "1"
BASE = RunSpec.from_dict(
    {
        "ion": {"symbol": "B", "energy_keV": 20.0},
        "number_ions": 10,
        "seed": 1,
        "settings": {"ranges": 1},
        "target": [
            {
                "name": "Si",
                "thickness_nm": 100.0,
                "density_g_cm3": 2.33,
                "elements": [{"symbol": "Si", "stoich": 1.0}],
            }
        ],
    }
)


def fixture_runner(spec):
    """Stands in for SRIM: the outputs of test_files/1."""
    return Results(str(FIXTURE))


def one_point_scan():
    return Scan(BASE, grid={"ion.energy_keV": [10.0]}, seed=3)


def test_a_run_marks_its_folder_and_releases_the_lock(tmp_path):
    folder = tmp_path / "scan"
    run_scan(one_point_scan(), workers=1, scan_dir=folder, runner=fixture_runner, cache=False)
    marker = (folder / MARKER_NAME).read_text(encoding="utf-8")
    assert marker.startswith("pysrim2 scan folder, pysrim2 ")
    lock = scan_lock(folder).acquire()  # released at the end of the run
    lock.release()


def test_prepare_marks_the_folder(tmp_path):
    folder = prepare(one_point_scan(), tmp_path / "slurm")
    assert (folder / MARKER_NAME).is_file()


def test_a_second_scan_in_a_locked_folder_is_refused_and_writes_nothing(tmp_path):
    folder = tmp_path / "scan"
    folder.mkdir()
    held = scan_lock(folder).acquire()
    try:
        with pytest.raises(ScanBusy, match="already running"):
            run_scan(one_point_scan(), workers=1, scan_dir=folder, runner=fixture_runner)
    finally:
        held.release()
    assert not (folder / "scan.yaml").exists()
    assert not (folder / MARKER_NAME).exists()
    assert (folder / LOCK_NAME).is_file()  # the lock file stays; only the lock is released
