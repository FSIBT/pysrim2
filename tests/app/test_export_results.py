"""Saving a run's results in the library's formats (Qt-free: runs without the app extra).

The run folders are built from test_files/2 by ``run_folders`` (no SRIM). The formats that need
pyarrow or tables are skipped when those are not installed; the availability test replaces the
import check instead.
"""

import importlib.util
import json
from copy import deepcopy

import pytest
from run_folders import DONE, finished_scan
from runners import fixture_runner

from srim.app.export_results import (
    FORMATS,
    ExportRequest,
    export_run,
    format_available,
    skipped_points,
    targets,
)
from srim.beam import beam_scan
from srim.scan import Scan, run_scan
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


def spec(**changes):
    data = deepcopy(BORON)
    data.update(changes)
    return RunSpec.from_dict(data)


def single_run(folder):
    """A finished plain run: one point with the spec's own seed (keep_seed)."""
    result = finished_scan(folder, beam_scan(spec()), [DONE])
    result.to_csv(folder / "result")
    return result


def two_point_scan(folder, outcomes):
    scan = Scan(spec(), grid={"ion.energy_keV": [10.0, 20.0]}, seed=7)
    result = finished_scan(folder, scan, outcomes)
    result.to_csv(folder / "result")
    return result


def test_a_batched_point_exports_the_merged_tables_and_its_batches(tmp_path):
    run = tmp_path / "run"
    scan = Scan(spec(), ions=200, batch_ions=100, seed=1)
    result = run_scan(scan, workers=1, scan_dir=run, runner=fixture_runner)
    short = result.scan.points()[0].id[:8]
    out = tmp_path / "out"
    export_run(run, ExportRequest(out, "b", ("csv", "raw")))
    assert (out / f"b-{short}-csv" / "ioniz.csv").is_file()
    assert (out / f"b-{short}-raw" / "batches" / "1" / "TRIM.IN").is_file()
    assert (out / f"b-{short}-raw" / "run.yaml").is_file()


def test_a_run_folder_cannot_be_the_target(tmp_path):
    run = tmp_path / "run"
    single_run(run)
    with pytest.raises(ValueError, match="outside the run folder"):
        export_run(run, ExportRequest(run, "boron", ("csv",)))
    with pytest.raises(ValueError, match="outside the run folder"):
        export_run(run, ExportRequest(run / "points", "boron", ("csv",)))


def test_formats_are_named_in_order():
    assert FORMATS == ("parquet", "hdf5", "csv", "raw")


def test_csv_and_raw_of_a_single_run_are_the_run_outputs(tmp_path):
    run = tmp_path / "run"
    single_run(run)
    out = tmp_path / "out"
    written = export_run(run, ExportRequest(out, "boron", ("csv", "raw")))

    point = next((run / "points").iterdir())
    assert (out / "boron-csv" / "ioniz.csv").is_file()
    assert (out / "boron-raw" / "IONIZ.txt").read_bytes() == (point / "IONIZ.txt").read_bytes()
    assert (out / "boron-raw" / "run.yaml").read_bytes() == (point / "run.yaml").read_bytes()
    assert (out / "boron-run.yaml").read_bytes() == (run / "scan.yaml").read_bytes()
    assert out / "boron-csv" in written
    assert (run / "scan.yaml").is_file() and not (run / "boron-csv").exists()


def test_the_run_folder_is_not_changed(tmp_path):
    run = tmp_path / "run"
    single_run(run)
    before = sorted(p.relative_to(run) for p in run.rglob("*"))
    export_run(run, ExportRequest(tmp_path / "out", "boron", ("csv", "raw")))
    assert sorted(p.relative_to(run) for p in run.rglob("*")) == before


@pytest.mark.skipif(importlib.util.find_spec("pyarrow") is None, reason="needs pyarrow")
def test_parquet_of_a_single_run_carries_the_spec(tmp_path):
    import pyarrow.parquet as pq

    run = tmp_path / "run"
    single_run(run)
    export_run(run, ExportRequest(tmp_path / "out", "boron", ("parquet",)))
    table = tmp_path / "out" / "boron.parquet" / "ioniz.parquet"
    metadata = pq.read_schema(table).metadata[b"pysrim2"]
    assert json.loads(metadata)["spec"]["number_ions"] == 200


@pytest.mark.skipif(importlib.util.find_spec("tables") is None, reason="needs tables")
def test_hdf5_of_a_scan_holds_the_table_and_the_chosen_points(tmp_path):
    run = tmp_path / "run"
    result = two_point_scan(run, [DONE, DONE])
    out = tmp_path / "out"
    export_run(run, ExportRequest(out, "grid", ("hdf5",), points=(1,)))
    import pandas as pd

    table = pd.read_hdf(out / "grid.h5", key="scan")
    assert len(table) == len(result.table)
    chosen = result.scan.points()[1].id[:8]
    assert (out / f"grid-{chosen}.h5").is_file()
    other = result.scan.points()[0].id[:8]
    assert not (out / f"grid-{other}.h5").exists()


def test_a_scan_writes_its_table_and_only_the_chosen_points(tmp_path):
    run = tmp_path / "run"
    result = two_point_scan(run, [DONE, DONE])
    out = tmp_path / "out"
    export_run(run, ExportRequest(out, "grid", ("csv", "raw"), points=(1,)))

    ids = [point.id[:8] for point in result.scan.points()]
    assert (out / "grid-scan-csv" / "scan.csv").is_file()
    assert (out / f"grid-{ids[1]}-csv" / "ioniz.csv").is_file()
    assert (out / f"grid-{ids[1]}-raw" / "IONIZ.txt").is_file()
    assert not (out / f"grid-{ids[0]}-csv").exists()
    assert (out / "grid-run.yaml").is_file()


def test_points_that_did_not_finish_are_skipped_and_listed(tmp_path):
    run = tmp_path / "run"
    result = two_point_scan(run, [DONE, "boom"])
    out = tmp_path / "out"
    export_run(run, ExportRequest(out, "grid", ("csv",)))
    first = result.scan.points()[0].id[:8]
    second = result.scan.points()[1].id[:8]
    assert (out / f"grid-{first}-csv").is_dir()
    assert not (out / f"grid-{second}-csv").exists()
    assert skipped_points(run, ExportRequest(out, "grid", ("csv",))) == [second]


def test_a_second_export_to_the_same_name_raises_before_writing(tmp_path):
    run = tmp_path / "run"
    single_run(run)
    out = tmp_path / "out"
    export_run(run, ExportRequest(out, "boron", ("csv",)))
    with pytest.raises(FileExistsError, match="boron-csv"):
        export_run(run, ExportRequest(out, "boron", ("csv", "raw")))
    assert not (out / "boron-raw").exists()


def test_targets_are_the_paths_export_run_writes(tmp_path):
    run = tmp_path / "run"
    single_run(run)
    request = ExportRequest(tmp_path / "out", "boron", ("csv", "raw"))
    paths = targets(run, request)
    assert tmp_path / "out" / "boron-csv" in paths
    assert tmp_path / "out" / "boron-raw" in paths
    assert tmp_path / "out" / "boron-run.yaml" in paths


def test_a_bad_request_is_refused(tmp_path):
    with pytest.raises(ValueError):
        ExportRequest(tmp_path, "a/b", ("csv",))
    with pytest.raises(ValueError):
        ExportRequest(tmp_path, "", ("csv",))
    with pytest.raises(ValueError):
        ExportRequest(tmp_path, "ok", ("pickle",))


def test_format_available_reports_the_missing_extra(monkeypatch):
    real = importlib.util.find_spec

    def without(name, *args, **kwargs):
        if name in ("pyarrow", "tables"):
            return None
        return real(name, *args, **kwargs)

    monkeypatch.setattr(importlib.util, "find_spec", without)
    assert format_available("parquet") == "needs the parquet extra (pyarrow)"
    assert format_available("hdf5") == "needs the hdf5 extra (tables)"
    assert format_available("csv") is None
    assert format_available("raw") is None


def test_format_available_is_none_when_installed(monkeypatch):
    monkeypatch.setattr(importlib.util, "find_spec", lambda name, *a, **k: object())
    assert format_available("parquet") is None
    assert format_available("hdf5") is None


def test_a_missing_extra_is_refused_before_writing(tmp_path, monkeypatch):
    run = tmp_path / "run"
    single_run(run)
    monkeypatch.setattr(importlib.util, "find_spec", lambda name, *a, **k: None)
    with pytest.raises(ImportError, match="pyarrow"):
        export_run(run, ExportRequest(tmp_path / "out", "boron", ("parquet", "csv")))
    assert not (tmp_path / "out").exists()
