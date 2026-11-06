"""ScanResult: the table built from rows, exports and plot (no SRIM runs)."""

import math
import sys

import pandas as pd
import pytest

from srim.scan import Scan
from srim.scan.result import ScanResult
from srim.spec import RunSpec, SpecError

BASE = {
    "ion": {"symbol": "B", "energy_keV": 20.0, "angle_deg": 0.0},
    "number_ions": 200,
    "seed": 7,
    "target": [
        {
            "name": "Si",
            "thickness_nm": 100.0,
            "density_g_cm3": 2.33,
            "elements": [{"symbol": "Si", "stoich": 1.0}],
        }
    ],
}


@pytest.fixture
def scan():
    base = RunSpec.from_dict(BASE)
    return Scan(
        base,
        grid={"ion.energy_keV": [10.0, 20.0], "ion.angle_deg": [0.0, 30.0]},
        extract=["sputter_yield"],
        ions=300,
    )


def rows_for(scan, values):
    """One done row per point; ``values`` maps the point index to sputter_yield."""
    out = []
    for p in scan.points():
        out.append(
            {
                "index": p.index,
                "sputter_yield": values[p.index],
                "n_ions": 300,
                "seeds": [11, 12, 13],
                "status": "done",
                "error": None,
                "path": f"points/{p.index:04d}",
            }
        )
    return out


def test_table_columns_and_order(scan):
    result = ScanResult.from_rows(scan, rows_for(scan, [1.0, 2.0, 3.0, 4.0]))
    assert list(result.table.columns) == [
        "ion.energy_keV",
        "ion.angle_deg",
        "id",
        "sputter_yield",
        "sputter_yield_err",
        "n_ions",
        "seeds",
        "status",
        "error",
        "path",
    ]
    assert len(result.table) == 4


def test_params_and_id_come_from_the_scan_point(scan):
    result = ScanResult.from_rows(scan, rows_for(scan, [1.0, 2.0, 3.0, 4.0]))
    points = scan.points()
    assert result.table["ion.energy_keV"].tolist() == [p.params["ion.energy_keV"] for p in points]
    assert result.table["id"].tolist() == [p.id for p in points]
    assert result.table["seeds"].tolist() == ["11,12,13"] * 4
    assert result.table["sputter_yield"].tolist() == [1.0, 2.0, 3.0, 4.0]


def test_errors_are_nan_unless_given_and_every_value_has_one(scan):
    rows = rows_for(scan, [1.0, 2.0, 3.0, 4.0])
    rows[0]["sputter_yield_err"] = 0.1
    result = ScanResult.from_rows(scan, rows)
    err = result.table["sputter_yield_err"]
    assert err.iloc[0] == 0.1
    assert all(math.isnan(v) for v in err.iloc[1:])


def test_failed_rows_keep_the_point_with_status_and_error(scan):
    rows = rows_for(scan, [1.0, float("nan"), 3.0, 4.0])
    rows[1].update(status="failed", error="SRIM crashed", n_ions=None, seeds=None)
    result = ScanResult.from_rows(scan, rows)
    assert result.table["status"].tolist() == ["done", "failed", "done", "done"]
    assert result.table["error"].iloc[1] == "SRIM crashed"
    assert pd.isna(result.table["seeds"].iloc[1])


@pytest.mark.parametrize(
    "change, message",
    [
        ({"index": 9}, "row needs 'index'"),
        ({"index": None}, "row needs 'index'"),
        ({"status": "finished"}, "status must be one of"),
        ({"status": None}, "status must be one of"),
    ],
)
def test_bad_rows_are_rejected(scan, change, message):
    rows = rows_for(scan, [1.0, 2.0, 3.0, 4.0])
    rows[0].update(change)
    with pytest.raises(SpecError, match=message):
        ScanResult.from_rows(scan, rows)


def test_error_column_without_its_value_is_rejected(scan):
    rows = rows_for(scan, [1.0, 2.0, 3.0, 4.0])
    rows[0]["ghost_err"] = 0.5
    with pytest.raises(SpecError, match="ghost_err"):
        ScanResult.from_rows(scan, rows)


def test_parquet_round_trip_with_metadata(scan, tmp_path):
    pytest.importorskip("pyarrow")
    import pyarrow.parquet as pq

    result = ScanResult.from_rows(scan, rows_for(scan, [1.0, 2.0, 3.0, 4.0]))
    path = tmp_path / "scan.parquet"
    result.to_parquet(path)
    back = pd.read_parquet(path)
    assert back["sputter_yield"].tolist() == [1.0, 2.0, 3.0, 4.0]
    assert back["id"].tolist() == result.table["id"].tolist()
    import json

    meta = json.loads(pq.read_schema(path).metadata[b"pysrim2"])
    assert meta["table"] == "scan"
    assert meta["metadata"]["scan"]["grid"] == scan.to_dict()["grid"]
    assert meta["spec"]["number_ions"] == 200


def test_csv_export_writes_table_and_metadata(scan, tmp_path):
    result = ScanResult.from_rows(scan, rows_for(scan, [1.0, 2.0, 3.0, 4.0]))
    written = result.to_csv(tmp_path / "out")
    back = pd.read_csv(written)
    assert back["sputter_yield"].tolist() == [1.0, 2.0, 3.0, 4.0]
    assert (tmp_path / "out" / "metadata.yaml").exists()


def test_hdf5_export_round_trip(scan, tmp_path):
    pytest.importorskip("tables")
    result = ScanResult.from_rows(scan, rows_for(scan, [1.0, 2.0, 3.0, 4.0]))
    path = tmp_path / "scan.h5"
    result.to_hdf5(path)
    back = pd.read_hdf(path, key="scan")
    assert back["sputter_yield"].tolist() == [1.0, 2.0, 3.0, 4.0]
    assert back["status"].tolist() == ["done"] * 4


def test_plot_defaults_and_error_bars(scan):
    plt = pytest.importorskip("matplotlib.pyplot")
    rows = rows_for(scan, [1.0, 2.0, 3.0, 4.0])
    for row in rows:
        row["sputter_yield_err"] = 0.5
    result = ScanResult.from_rows(scan, rows)
    ax = result.plot()
    assert ax.get_xlabel() == "ion.energy_keV"
    assert ax.get_ylabel() == "sputter_yield"
    # hue = second scanned parameter: one errorbar line per angle
    labels = sorted(t.get_text() for t in ax.get_legend().get_texts())
    assert labels == ["ion.angle_deg=0.0", "ion.angle_deg=30.0"]
    plt.close("all")


def test_plot_with_explicit_axes_and_no_hue(scan):
    plt = pytest.importorskip("matplotlib.pyplot")
    result = ScanResult.from_rows(scan, rows_for(scan, [1.0, 2.0, 3.0, 4.0]))
    fig, ax = plt.subplots()
    returned = result.plot(x="ion.angle_deg", y="sputter_yield", hue=False, ax=ax)
    assert returned is ax
    assert ax.get_legend() is None
    plt.close(fig)


def test_plot_names_missing_columns(scan):
    pytest.importorskip("matplotlib.pyplot")
    result = ScanResult.from_rows(scan, rows_for(scan, [1.0, 2.0, 3.0, 4.0]))
    with pytest.raises(ValueError, match="no column 'nope'"):
        result.plot(y="nope")


def test_plot_without_matplotlib_says_how_to_install(scan, monkeypatch):
    result = ScanResult.from_rows(scan, rows_for(scan, [1.0, 2.0, 3.0, 4.0]))
    monkeypatch.setitem(sys.modules, "matplotlib", None)
    monkeypatch.setitem(sys.modules, "matplotlib.pyplot", None)
    with pytest.raises(ImportError, match="pip install matplotlib"):
        result.plot()
