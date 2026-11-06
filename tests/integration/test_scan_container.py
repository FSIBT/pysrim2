"""run_scan with the container back end: real SRIM in the pysrim2 image (``pytest -m container``).

Needs a container runtime (podman or docker) on this host and the image in ``PYSRIM2_IMAGE``
(CI builds ``pysrim2:ci``); otherwise the test is skipped. SRIM is not needed on the host.
"""

import os
import signal
import threading

import pytest

from srim.installer import container
from srim.scan import Scan, run_scan
from srim.spec import RunSpec

pytestmark = pytest.mark.container

GUARD_SECONDS = 900

BASE = RunSpec.from_dict(
    {
        "ion": {"symbol": "B", "energy_keV": 20.0},
        "number_ions": 10,
        "seed": 1,
        "settings": {"ranges": 1},
        "target": [
            {
                "name": "Si",
                "thickness_nm": 1000.0,  # thick enough to stop 20 keV B
                "density_g_cm3": 2.33,
                "elements": [{"symbol": "Si", "stoich": 1.0}],
            }
        ],
    }
)


@pytest.fixture(autouse=True)
def hang_guard():
    """Fail (instead of hanging) if the containers take longer than GUARD_SECONDS (POSIX)."""
    if not hasattr(signal, "SIGALRM") or threading.current_thread() is not threading.main_thread():
        yield
        return

    def alarm(signum, frame):
        raise TimeoutError(f"test ran longer than {GUARD_SECONDS} s")

    previous = signal.signal(signal.SIGALRM, alarm)
    signal.alarm(GUARD_SECONDS)
    try:
        yield
    finally:
        signal.alarm(0)
        signal.signal(signal.SIGALRM, previous)


def test_two_points_run_in_one_container_each(tmp_path):
    runtime = container.find_runtime()
    if runtime is None:
        pytest.skip("no container runtime (podman or docker) on this host")
    image = os.environ.get("PYSRIM2_IMAGE")
    if not image:
        pytest.skip("set PYSRIM2_IMAGE to the pysrim2 image to test (CI: pysrim2:ci)")
    scan = Scan(BASE, grid={"ion.energy_keV": [10.0, 20.0]}, seed=11)
    folder = tmp_path / "scan"
    events = []
    result = run_scan(
        scan,
        workers=2,
        backend="container",
        runtime=runtime,
        image=image,
        scan_dir=folder,
        on_event=events.append,
    )
    assert result.table["status"].tolist() == ["done", "done"], result.table["error"].tolist()
    assert result.table["n_ions"].tolist() == [10, 10]
    assert result.table["mean_depth_A"].notna().all()
    for point in scan.points():
        point_dir = folder / "points" / point.id[:16]
        assert (point_dir / "RANGE_3D.txt").is_file()
        assert (point_dir / "run.yaml").is_file()
        assert not (point_dir / "error.log").exists()
    assert events[-1].kind == "scan_finished"
    assert (events[-1].n_done, events[-1].n_failed) == (2, 0)
    assert not list(tmp_path.glob("**/pysrim2-job-*"))  # job folders are removed
