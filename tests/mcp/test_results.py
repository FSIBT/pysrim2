"""Results of run folders for the agent: the summary, the plot catalogue, tables, plots and export,
through the backend's folder defaults. No SRIM: the points are copies of test_files/1 (it has the
per-ion RANGE_3D.txt) or test_files/2 (no per-ion output). PNG files are checked by their header.
"""

import json
import shutil
import sys
from copy import deepcopy
from pathlib import Path

import pytest
from run_folders import DONE, finished_scan

from srim.analysis import implant_summary
from srim.app import plots
from srim.app.plots import CATALOGUE
from srim.mcp import results
from srim.mcp.backend import BackendError
from srim.mcp.headless import HeadlessBackend
from srim.output import EXYZ, Backscat, Collision, Range3D, Sputter, Transmit
from srim.output.tables import Results
from srim.scan import Scan
from srim.spec import RunSpec

ROOT = Path(__file__).resolve().parents[2]
ONE = ROOT / "test_files" / "1"  # IONIZ, VACANCY, PHONON, E2RECOIL, RANGE, RANGE_3D
PNG = b"\x89PNG\r\n\x1a\n"
#: complete rows of test_files/1/RANGE_3D.txt
RANGE_ROWS = 996

GOLD = {
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


def spec():
    return RunSpec.from_dict(deepcopy(GOLD))


def run_folder(tmp_path, points=1):
    """A finished run folder (one point) or scan folder (``points`` points) of test_files/1."""
    if points == 1:
        scan = Scan(spec(), seed=7)
    else:
        grid = {"ion.energy_keV": [10.0 + 5.0 * i for i in range(points)]}
        scan = Scan(spec(), grid=grid, seed=7)
    finished_scan(tmp_path / "run", scan, [DONE] * len(scan.points()), source=ONE)
    return tmp_path / "run"


def test_the_depth_profile_is_a_plot_and_a_table(tmp_path):
    folder = run_folder(tmp_path)
    available = {p["key"]: p for p in results.available_plots(folder)}
    assert set(available) == {plot.key for plot in CATALOGUE}
    assert available["range_profile"]["available"] is True
    assert available["range_profile"]["reason"] is None
    assert available["backscattered"]["available"] is False
    assert "BACKSCAT.txt" in available["backscattered"]["reason"]

    table = results.table(folder, None, "range_profile", max_rows=50)
    assert len(table["rows"]) <= 50
    assert table["columns"][0] == "depth_nm"

    png = results.render_plot(folder, None, "range_profile", tmp_path / "plots")
    assert png == tmp_path / "plots" / "range_profile.png"
    assert png.read_bytes()[:8] == PNG


def test_summary_gives_the_mean_depth(tmp_path):
    rows = results.summary(run_folder(tmp_path))
    assert len(rows) == 1
    row = rows[0]
    assert row["n_ions"] == RANGE_ROWS
    assert row["capped"] is False
    expected = implant_summary(Range3D(str(ONE)).df)["mean_depth_A"]
    assert row["mean_depth_A"] == pytest.approx(expected)
    assert row["straggle_depth_A"] > 0
    json.dumps(rows, allow_nan=False)  # plain JSON, no NaN


def test_a_big_per_ion_output_is_never_read_whole(tmp_path, monkeypatch):
    monkeypatch.setattr(plots, "PLOT_ROW_CAP", 10)
    folder = run_folder(tmp_path)
    table = results.table(folder, None, "stopping_map", max_rows=5000)
    assert table["capped"] is True
    assert table["total_rows"] is None
    assert len(table["rows"]) <= 10
    row = results.summary(folder)[0]
    assert row["capped"] is True
    assert row["n_ions"] == 10


def test_a_per_ion_table_takes_the_first_rows(tmp_path):
    folder = run_folder(tmp_path)
    full = results.table(folder, None, "stopping_map", max_rows=5000)
    assert full["capped"] is False
    assert full["total_rows"] == RANGE_ROWS
    first = results.table(folder, None, "stopping_map", max_rows=20)
    assert first["rows"] == full["rows"][:20]
    assert first["capped"] is True


def test_a_profile_table_is_thinned_evenly_over_its_depths(tmp_path):
    folder = run_folder(tmp_path)
    full = results.table(folder, None, "range_profile", max_rows=5000)
    thin = results.table(folder, None, "range_profile", max_rows=10)
    assert len(full["rows"]) > 10
    assert len(thin["rows"]) == 10
    assert thin["rows"][0] == full["rows"][0]
    assert thin["rows"][-1] == full["rows"][-1]
    assert thin["capped"] is True


def test_max_rows_is_capped_at_5000(tmp_path):
    folder = run_folder(tmp_path)
    table = results.table(folder, None, "range_profile", max_rows=10**9)
    assert table["capped"] is False
    assert table["total_rows"] == len(table["rows"])


@pytest.mark.parametrize("bad", [0, -3, True, "5", 2.5])
def test_max_rows_must_be_a_whole_number(tmp_path, bad):
    folder = run_folder(tmp_path)
    with pytest.raises(BackendError, match="max_rows"):
        results.table(folder, None, "range_profile", max_rows=bad)


def test_a_scan_needs_a_point(tmp_path):
    folder = run_folder(tmp_path, points=3)
    with pytest.raises(BackendError, match="point"):
        results.summary(folder)
    with pytest.raises(BackendError, match="point"):
        results.available_plots(folder)
    with pytest.raises(BackendError, match="point"):
        results.table(folder, None, "range_profile")
    with pytest.raises(BackendError, match="point"):
        results.render_plot(folder, None, "range_profile", tmp_path / "plots")


def test_a_point_of_a_scan_is_read_by_its_index(tmp_path):
    pytest.importorskip("matplotlib")
    folder = run_folder(tmp_path, points=3)
    assert results.summary(folder, 2)[0]["n_ions"] == RANGE_ROWS
    first = results.render_plot(folder, 0, "range_profile", tmp_path / "plots")
    last = results.render_plot(folder, 2, "range_profile", tmp_path / "plots")
    assert first.name == "range_profile-point0.png"
    assert last.name == "range_profile-point2.png"
    assert first != last


@pytest.mark.parametrize("bad", [3, -1, "1", True, 1.0])
def test_a_bad_point_is_refused(tmp_path, bad):
    folder = run_folder(tmp_path, points=3)
    with pytest.raises(BackendError, match="point"):
        results.summary(folder, bad)


def test_a_point_without_outputs_is_refused(tmp_path):
    scan = Scan(spec(), grid={"ion.energy_keV": [10.0, 20.0]}, seed=7)
    folder = tmp_path / "run"
    finished_scan(folder, scan, [DONE, "SRIM failed"], source=ONE)
    assert results.summary(folder, 0)[0]["n_ions"] == RANGE_ROWS
    with pytest.raises(BackendError, match="no outputs"):
        results.summary(folder, 1)


def test_a_bad_plot_key_is_refused(tmp_path):
    folder = run_folder(tmp_path)
    with pytest.raises(BackendError, match="nope"):
        results.table(folder, None, "nope")
    with pytest.raises(BackendError, match="nope"):
        results.render_plot(folder, None, "nope", tmp_path / "plots")
    assert not (tmp_path / "plots").exists()


def test_a_plot_whose_output_is_missing_is_refused(tmp_path):
    folder = run_folder(tmp_path)
    with pytest.raises(BackendError, match="BACKSCAT.txt"):
        results.table(folder, None, "backscattered")


def test_plots_need_matplotlib(tmp_path, monkeypatch):
    folder = run_folder(tmp_path)
    for name in ("matplotlib", "matplotlib.figure", "matplotlib.backends.backend_agg"):
        monkeypatch.setitem(sys.modules, name, None)  # an import of it now fails
    with pytest.raises(BackendError, match=r"pysrim2\[plot\]"):
        results.render_plot(folder, None, "range_profile", tmp_path / "plots")
    assert not (tmp_path / "plots").exists()


def test_export_to_csv_writes_the_tables_and_metadata(tmp_path):
    folder = run_folder(tmp_path)
    out = results.export(folder, "csv", tmp_path / "csv")
    names = {Path(path).name for path in out["written"]}
    assert {"ioniz.csv", "range.csv", "metadata.yaml"} <= names
    assert (tmp_path / "csv" / "ioniz.csv").read_text().startswith("depth_A")
    text = (tmp_path / "csv" / "metadata.yaml").read_text()
    assert "pysrim2_version" in text
    assert str(tmp_path) not in text  # no local paths in shared files


def test_export_to_hdf5_and_parquet(tmp_path):
    folder = run_folder(tmp_path)
    pytest.importorskip("tables")
    out = results.export(folder, "hdf5", tmp_path / "run.h5")
    assert out == {"written": [str(tmp_path / "run.h5")]}
    pytest.importorskip("pyarrow")
    out = results.export(folder, "parquet", tmp_path / "parquet")
    assert (tmp_path / "parquet" / "ioniz.parquet").is_file()
    assert out["written"]


def test_a_missing_export_extra_is_a_backend_error(tmp_path, monkeypatch):
    folder = run_folder(tmp_path)
    monkeypatch.setitem(sys.modules, "pyarrow", None)
    with pytest.raises(BackendError, match=r"pysrim2\[parquet\]"):
        results.export(folder, "parquet", tmp_path / "parquet")


def test_an_unknown_export_format_is_refused(tmp_path):
    folder = run_folder(tmp_path)
    with pytest.raises(BackendError, match="csv"):
        results.export(folder, "xlsx", tmp_path / "out")


def test_collision_export_needs_collision_events(tmp_path):
    folder = run_folder(tmp_path)  # test_files/1 has no COLLISON.txt
    with pytest.raises(BackendError, match="collision"):
        results.export(folder, "csv", tmp_path / "csv", include_collision=True)


def test_the_headless_backend_plots_and_tables_a_run_folder(tmp_path):
    pytest.importorskip("matplotlib")
    folder = run_folder(tmp_path)
    backend = HeadlessBackend(tmp_path)
    out = backend.plot(str(folder), None, "range_profile")
    assert out == {"path": str(folder / "plots" / "range_profile.png"), "shown": False}
    assert Path(out["path"]).read_bytes()[:8] == PNG
    assert backend.table(str(folder), None, "range_profile", 20)["columns"][0] == "depth_nm"
    assert backend.summary(str(folder))[0]["n_ions"] == RANGE_ROWS


def test_the_headless_backend_exports_relative_to_its_folder(tmp_path):
    folder = run_folder(tmp_path)
    backend = HeadlessBackend(tmp_path)
    out = backend.export(str(folder), "csv", "out-csv")
    assert (tmp_path / "out-csv" / "ioniz.csv").is_file()
    assert str(tmp_path / "out-csv" / "metadata.yaml") in out["written"]


def test_the_headless_results_need_a_run(tmp_path):
    with pytest.raises(BackendError, match="no run"):
        HeadlessBackend(tmp_path).summary(None)


def test_the_collision_export_lists_the_tables_without_parsing_them(tmp_path, monkeypatch):
    """The include list of an export with collision events is built from the files on disk:
    no per-ion output is parsed to learn that it is there. The export's writer is faked, so
    only the list (and the parsers it would call) is under test."""
    folder = run_folder(tmp_path)  # test_files/1: RANGE_3D.txt, no EXYZ, BACKSCAT, ...
    point = next((folder / "points").iterdir())
    shutil.copy(ROOT / "test_files" / "SiC-C" / "COLLISON.txt", point / "COLLISON.txt")
    parsed = []

    def refuse(self, *args, **kwargs):
        parsed.append(type(self).__name__)
        raise AssertionError(f"{type(self).__name__} was parsed")

    for parser in (Range3D, EXYZ, Backscat, Transmit, Sputter, Collision):
        monkeypatch.setattr(parser, "__init__", refuse)
    seen = {}

    def fake_to_csv(self, directory, spec=None, include=None, chunk_rows=100_000):
        seen["include"] = include
        return {}

    monkeypatch.setattr(Results, "to_csv", fake_to_csv)
    out = results.export(folder, "csv", tmp_path / "out", include_collision=True)
    assert parsed == []
    assert seen["include"] == [
        "ioniz",
        "vacancy",
        "novac",
        "e2recoil",
        "phonon",
        "range",
        "range3d",
        "collision",
    ]
    assert out == {"written": [str(tmp_path / "out" / "metadata.yaml")]}
