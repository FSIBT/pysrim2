"""Finished run folders for the app's result tests, built without SRIM.

A point folder (``points/<id[:16]>``) holds a copy of ``test_files/2`` (real SRIM outputs) and the
``run.yaml`` of its spec; the scan folder holds ``scan.yaml``. The :class:`ScanResult` is built
from the rows, as a finished run leaves it. Each test gets its own folder under ``tmp_path``.
"""

import shutil
from pathlib import Path

from srim.scan.result import ScanResult

FIXTURE = Path(__file__).resolve().parents[1] / "test_files" / "2"
#: a point's outcome in :func:`finished_scan`: this marks a point that finished with outputs
DONE = "done"


def finished_scan(folder: Path, scan, outcomes, values=None) -> ScanResult:
    """The result of ``scan`` in ``folder``: one outcome per point, :data:`DONE` (its outputs
    are copied in) or the error text of a point that failed.

    ``values``, when given, is one dict of extracted values per point (the rows of a point
    that is done get them; the others have none).
    """
    folder = Path(folder)
    folder.mkdir(parents=True, exist_ok=True)
    rows = []
    for position, (point, outcome) in enumerate(zip(scan.points(), outcomes, strict=True)):
        if outcome == DONE:
            rel = f"points/{point.id[:16]}"
            point_dir = folder / rel
            shutil.copytree(FIXTURE, point_dir)
            point.spec.to_yaml(point_dir / "run.yaml", provenance={"test": "result"})
            extra = values[position] if values is not None else {}
            rows.append(
                {
                    "index": point.index,
                    "status": "done",
                    "path": rel,
                    "n_ions": point.spec.number_ions,
                    **extra,
                }
            )
        else:
            rows.append({"index": point.index, "status": "failed", "error": outcome})
    scan.to_yaml(folder / "scan.yaml", provenance={"test": "result"})
    return ScanResult.from_rows(scan, rows, base_dir=folder)
