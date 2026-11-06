"""Scan events: kinds, JSON-safe dicts and the time stamp (no SRIM)."""

import dataclasses
import json
import math
from pathlib import Path

import numpy as np
import pytest

from srim.scan import events
from srim.scan.events import EVENT_TYPES, PointFailed, PointFinished, ScanFinished


def test_every_kind_is_registered_once():
    assert sorted(EVENT_TYPES) == sorted(
        [
            "scan_started",
            "point_queued",
            "point_started",
            "point_cached",
            "point_progress",
            "point_finished",
            "point_failed",
            "point_retried",
            "point_stopped",
            "batch_finished",
            "worker_stats",
            "scan_paused",
            "scan_finished",
        ]
    )
    for kind, cls in EVENT_TYPES.items():
        assert cls.kind == kind


def test_to_dict_starts_with_kind_and_has_a_time_stamp():
    event = PointFailed(index=2, id="ab", error="boom", attempt=1, final=False)
    data = event.to_dict()
    assert list(data)[0] == "kind"
    assert data["kind"] == "point_failed"
    assert data["index"] == 2 and data["final"] is False
    assert isinstance(data["time"], float) and data["time"] > 1.6e9  # UTC epoch seconds


def test_to_json_is_one_line_and_round_trips():
    event = ScanFinished(n_done=3, n_failed=1, seconds=2.5, stopped=True)
    text = event.to_json()
    assert "\n" not in text
    assert json.loads(text) == event.to_dict()


def test_numpy_values_and_non_finite_floats_become_json_safe():
    event = PointFinished(
        index=0,
        id="x",
        values={"mean": np.float64(1.5), "count": np.int64(4), "bad": float("nan")},
        n_ions=np.int64(10),
        seconds=0.25,
    )
    text = event.to_json()  # would raise with allow_nan=False if NaN got through
    data = json.loads(text)
    assert data["values"] == {"mean": 1.5, "count": 4, "bad": None}
    assert data["n_ions"] == 10


def test_nan_inside_an_array_becomes_null():
    event = PointFinished(
        index=0, id="x", values={"v": np.array([1.0, np.nan])}, n_ions=1, seconds=0.0
    )
    assert json.loads(event.to_json())["values"] == {"v": [1.0, None]}


def test_paths_become_strings():
    event = events.ScanStarted(n_points=1, workers=1, base_seed=3, scan_dir=Path("/x/y"))
    assert event.to_dict()["scan_dir"] == str(Path("/x/y"))


def test_events_are_frozen():
    event = PointFinished(index=0, id="x", values={}, n_ions=1, seconds=0.0)
    with pytest.raises(dataclasses.FrozenInstanceError):
        event.index = 5


def test_worker_stats_cpu_is_optional_and_none_by_default():
    stats = events.WorkerStats(worker=1, ions_per_s=None)
    assert stats.cpu_percent is None
    assert stats.to_dict()["ions_per_s"] is None
    assert not math.isnan(stats.time)


def test_an_event_reads_back_from_its_json_line():
    originals = [
        ScanFinished(n_done=3, n_failed=1, seconds=2.5, stopped=True),
        PointFinished(index=0, id="x", values={"mean": 1.5}, n_ions=10, seconds=0.25),
        events.BatchFinished(index=1, batch=2, ions=300, value_estimates={"v": [1.0, None]}),
        events.WorkerStats(worker=0, ions_per_s=None),
        events.ScanPaused(paused=True),
    ]
    for original in originals:
        back = events.event_from_dict(json.loads(original.to_json()))
        assert type(back) is type(original)
        assert back.to_dict() == original.to_dict()


def test_an_event_from_a_newer_writer_ignores_the_fields_it_does_not_know():
    data = {"kind": "scan_paused", "paused": False, "time": 5.0, "future_field": 1}
    assert events.event_from_dict(data) == events.ScanPaused(paused=False, time=5.0)


def test_an_unknown_kind_or_a_missing_field_is_an_error():
    with pytest.raises(ValueError, match="unknown event kind 'nope'"):
        events.event_from_dict({"kind": "nope"})
    with pytest.raises(TypeError):
        events.event_from_dict({"kind": "scan_paused"})  # no 'paused'
