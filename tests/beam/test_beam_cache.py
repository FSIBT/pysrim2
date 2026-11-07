"""Cache key, scan point id and job transport cover beam distributions.

A distribution's definition, histogram content and Python-function source change the cache
key and the point id; a spec without distributions hashes exactly as before; a job written
for another process is self-contained (histograms inline) and reads back to the same spec.
"""

import copy
import math
from pathlib import Path

import srim.beam.shapes as shapes_module
from srim.beam import fingerprint
from srim.scan.backends import read_job, write_job
from srim.scan.cache import cache_key
from srim.scan.spec import spec_hash
from srim.spec import RunSpec

EXAMPLE = Path(__file__).resolve().parents[2] / "examples/scripts/run_yaml_example/run.yaml"

#: pinned on the unchanged code (91fcea3): plain specs must keep these values
PINNED_CACHE_KEY = "b69dade4fcecd50ec00250313f3a73f4b101b24bfc47975bed450b1b072672de"
PINNED_SPEC_HASH = "4dcb58b1ad447a8757da11b10d2cb24d314f54ba187d9ab59c36594a7f1711ea"


def plain_spec() -> RunSpec:
    return RunSpec.from_yaml(EXAMPLE)


def base_data() -> dict:
    """A plain spec as plain data (for building distributions on top of it)."""
    return copy.deepcopy(plain_spec().to_dict())


def histogram_spec(path: Path) -> RunSpec:
    """The plain spec with its energy given by the histogram file ``path``."""
    data = base_data()
    data["ion"]["energy_keV"] = {"histogram": {"file": str(path)}}
    data["beam"] = {"mode": "exact"}
    return RunSpec.from_dict(data, base_dir=path.parent)


def function_spec(ref: str = "python:beam_funcs:ramp") -> RunSpec:
    data = base_data()
    data["ion"]["energy_keV"] = {"function": {"ref": ref, "range": [10.0, 30.0]}}
    data["beam"] = {"mode": "exact"}
    return RunSpec.from_dict(data)


def keys(spec: RunSpec) -> tuple[str, str]:
    return cache_key(spec, spec.seed), spec_hash(spec)


def test_plain_keys_unchanged():
    spec = plain_spec()
    assert cache_key(spec, 1) == PINNED_CACHE_KEY
    assert spec_hash(spec) == PINNED_SPEC_HASH
    assert fingerprint(spec) is None


def test_histogram_content_changes_key(tmp_path):
    path = tmp_path / "energy.txt"
    path.write_text("19.0 1\n20.0 3\n21.0 1\n")
    first = keys(histogram_spec(path))
    assert first == keys(histogram_spec(path))  # stable for the same content

    path.write_text("19.0 1\n20.0 1\n21.0 3\n")  # same file name and path, other counts
    second = keys(histogram_spec(path))
    assert second[0] != first[0]
    assert second[1] != first[1]


def test_function_source_changes_key(monkeypatch):
    spec = function_spec()
    monkeypatch.setattr(shapes_module, "_source_of", lambda fn: "def ramp(x):\n    return x\n")
    first_key, first_id = keys(spec)
    first_print = fingerprint(spec)
    monkeypatch.setattr(shapes_module, "_source_of", lambda fn: "def ramp(x):\n    return 2 * x\n")
    second_key, second_id = keys(spec)
    assert first_print is not None and fingerprint(spec) != first_print
    assert second_key != first_key
    assert second_id != first_id


def test_infinite_range_hashes_stably():
    data = base_data()
    data["ion"]["energy_keV"] = {"gaussian": {"mean": 20.0, "sigma": 5.0, "range": [0.0, math.inf]}}
    data["beam"] = {"mode": "exact"}
    spec = RunSpec.from_dict(data)
    assert keys(spec) == keys(spec)
    assert keys(spec) == keys(RunSpec.from_dict(copy.deepcopy(data)))


def test_job_yaml_inline(tmp_path):
    spec = histogram_spec(_write_histogram(tmp_path))
    job = write_job(tmp_path / "job", [("unit", spec)])
    text = (job / "job.yaml").read_text(encoding="utf-8")
    assert "edges:" in text and "counts:" in text
    assert "file:" not in text and "energy.txt" not in text
    ((name, back),) = read_job(job)
    assert name == "unit"
    assert back.to_dict(inline=True) == spec.to_dict(inline=True)
    assert keys(back) == keys(spec)


def test_function_job_round_trip(tmp_path):
    spec = function_spec()
    job = write_job(tmp_path / "job", [("unit", spec)])
    ((_, back),) = read_job(job)
    assert back.to_dict(inline=True) == spec.to_dict(inline=True)
    assert keys(back) == keys(spec)


def _write_histogram(tmp_path: Path) -> Path:
    path = tmp_path / "energy.txt"
    path.write_text("19.0 1\n20.0 3\n21.0 1\n")
    return path
