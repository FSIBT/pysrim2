"""run_scan with real SRIM: four points on two worker processes (``pytest -m srim``)."""

from srim.paths import srim_dir
from srim.scan import Precision, Scan, run_scan
from srim.spec import RunSpec

BASE = RunSpec.from_dict(
    {
        "ion": {"symbol": "B", "energy_keV": 20.0},
        "number_ions": 10,
        "seed": 1,
        "settings": {"ranges": 1},
        "target": [
            {
                "name": "Si",
                "thickness_nm": 1000.0,  # thick enough to stop 40 keV B (range ~130 nm)
                "density_g_cm3": 2.33,
                "elements": [{"symbol": "Si", "stoich": 1.0}],
            }
        ],
    }
)


def test_four_points_on_two_workers(tmp_path):
    scan = Scan(BASE, grid={"ion.energy_keV": [10.0, 20.0, 30.0, 40.0]}, seed=11)
    folder = tmp_path / "scan"
    install_before = sorted(p.name for p in srim_dir().iterdir())
    events = []
    result = run_scan(scan, workers=2, scan_dir=folder, on_event=events.append)

    assert result.table["status"].tolist() == ["done"] * 4, result.table["error"].tolist()
    assert result.table["n_ions"].tolist() == [10] * 4
    assert result.table["mean_depth_A"].notna().all()
    for point in scan.points():
        point_dir = folder / "points" / point.id[:16]
        assert (point_dir / "RANGE_3D.txt").is_file()
        assert (point_dir / "run.yaml").is_file()
        assert not (point_dir / "error.log").exists()
    assert (folder / "result" / "scan.csv").is_file()
    assert {e.worker for e in events if e.kind == "point_started"} <= {0, 1}
    assert events[-1].kind == "scan_finished"
    assert (events[-1].n_done, events[-1].n_failed, events[-1].stopped) == (4, 0, False)
    # the installed SRIM is never written during a run
    assert sorted(p.name for p in srim_dir().iterdir()) == install_before


def test_a_precision_target_completes_with_real_srim(tmp_path):
    """One point in batches of 20 ions until the mean depth is known to 5 %."""
    scan = Scan(
        BASE,
        grid={"ion.energy_keV": [20.0]},
        seed=5,
        ions=Precision(target="mean_depth_A", rel=0.05, min_ions=40, max_ions=200, batch_ions=20),
    )
    folder = tmp_path / "scan"
    result = run_scan(scan, workers=2, scan_dir=folder)

    row = result.table.iloc[0]
    assert row["status"] == "done", row["error"]
    assert row["mean_depth_A_err"] <= 0.05 * abs(row["mean_depth_A"])
    assert 40 <= row["n_ions"] <= 200 and row["n_ions"] % 20 == 0
    point_dir = folder / "points" / scan.points()[0].id[:16]
    batches = sorted(point_dir.glob("batches/*"))
    assert len(batches) == row["n_ions"] // 20
    assert (batches[0] / "RANGE_3D.txt").is_file()
    assert (batches[0] / "run.yaml").is_file()
