"""Binned mode: the bins of a distributed beam, the ion allocation over them and a bin's spec."""

import dataclasses
import heapq
import time
import warnings

import numpy as np
import pytest

from srim import TRIM
from srim.beam.binned import (
    AUTO_BINS,
    WARN_BINS,
    BeamBin,
    allocate_ions,
    beam_batch_allocation,
    bin_spec,
    bins_frame,
    make_bins,
)
from srim.spec import RunSpec, SpecError


def base():
    """A plain spec as plain data; a 500 nm gold target."""
    return {
        "ion": {"symbol": "B", "energy_keV": 20.0, "angle_deg": 7.0},
        "number_ions": 200,
        "calculation": 1,
        "seed": 12345,
        "settings": {"description": "binned test", "ranges": 1},
        "target": [
            {
                "name": "Au",
                "thickness_nm": 500.0,
                "density_g_cm3": 19.3,
                "elements": [{"symbol": "Au", "stoich": 1.0}],
            }
        ],
    }


def binned(energy=None, angle=None, beam=None, **ion):
    """A binned spec: ``energy``/``angle`` replace the fixed values when given."""
    data = base()
    if energy is not None:
        data["ion"]["energy_keV"] = energy
    if angle is not None:
        data["ion"]["angle_deg"] = angle
    data["ion"].update(ion)
    data["beam"] = {"mode": "binned", **(beam or {})}
    return data


def reference_allocation(probabilities, total):
    """Seat-by-seat Webster allocation with a heap: the definition, for small totals."""
    p = np.asarray(probabilities, dtype=float)
    p = p / p.sum()
    counts = [0] * len(p)
    heap = [(-(float(p[k]) / 0.5), k) for k in range(len(p)) if p[k] > 0]
    heapq.heapify(heap)
    for _ in range(total):
        _, k = heapq.heappop(heap)
        counts[k] += 1
        heapq.heappush(heap, (-(float(p[k]) / (counts[k] + 0.5)), k))
    return np.array(counts, dtype=np.int64)


# --- bins ------------------------------------------------------------------------------


def test_fixed_energy_and_tilt_give_one_bin():
    bins = make_bins(RunSpec.from_dict(base()))
    assert len(bins) == 1
    b = bins[0]
    assert (b.index, b.energy_keV, b.angle_deg, b.probability) == (0, 20.0, 7.0, 1.0)
    assert b.energy_range is None and b.angle_range is None


def test_uniform_energy_five_bins_with_fixed_angle():
    bins = make_bins(
        RunSpec.from_dict(
            binned({"uniform": {"low": 10, "high": 20}}, 7.0, {"bins": {"energy_keV": 5}})
        )
    )
    assert [b.index for b in bins] == [0, 1, 2, 3, 4]
    assert [b.energy_keV for b in bins] == pytest.approx([11, 13, 15, 17, 19])
    np.testing.assert_allclose(
        [b.energy_range for b in bins], [(10, 12), (12, 14), (14, 16), (16, 18), (18, 20)]
    )
    assert all(b.angle_deg == 7.0 and b.angle_range is None for b in bins)
    assert [b.probability for b in bins] == pytest.approx([0.2] * 5, abs=1e-12)


def test_auto_bins_for_gaussian_energy_and_uniform_tilt():
    spec = RunSpec.from_dict(
        binned({"gaussian": {"mean": 20, "sigma": 1}}, {"uniform": {"low": 0, "high": 6}})
    )
    assert AUTO_BINS == {"energy_keV": 10, "angle_deg": 6}
    bins = make_bins(spec)
    assert len(bins) == 60
    assert sum(b.probability for b in bins) == pytest.approx(1.0, abs=1e-12)
    assert all(b.energy_range is not None and b.angle_range is not None for b in bins)
    # energy-major: the first six bins share the first energy bin
    assert len({b.energy_range for b in bins[:6]}) == 1
    assert bins[0].angle_range == pytest.approx((0.0, 1.0))
    assert bins[1].angle_range == pytest.approx((1.0, 2.0))


def test_bins_above_warning_threshold_warn():
    assert WARN_BINS == 200
    spec = RunSpec.from_dict(
        binned(
            {"uniform": {"low": 10, "high": 20}},
            {"uniform": {"low": 0, "high": 6}},
            {"bins": {"energy_keV": 15, "angle_deg": 20}},
        )
    )
    with pytest.warns(UserWarning, match="300"):
        bins = make_bins(spec)
    assert len(bins) == 300


def test_bins_above_max_bins_refused():
    spec = RunSpec.from_dict(
        binned(
            {"uniform": {"low": 10, "high": 20}},
            {"uniform": {"low": 0, "high": 6}},
            {"bins": {"energy_keV": 50, "angle_deg": 50}, "max_bins": 2500},
        )
    )
    spec.beam.max_bins = 2000  # the spec is valid at 2500; make_bins must refuse it at 2000
    with pytest.raises(SpecError, match="beam.max_bins") as info:
        make_bins(spec)
    assert "2500" in str(info.value)


def test_list_edges_inside_support_keep_all_mass():
    spec = RunSpec.from_dict(
        binned({"uniform": {"low": 10, "high": 20}}, 7.0, {"bins": {"energy_keV": [10, 15, 20]}})
    )
    with warnings.catch_warnings(record=True) as seen:
        warnings.simplefilter("always")
        bins = make_bins(spec)
    assert not [w for w in seen if issubclass(w.category, UserWarning)]
    assert [b.energy_keV for b in bins] == pytest.approx([12.5, 17.5])
    assert [b.probability for b in bins] == pytest.approx([0.5, 0.5])


def test_list_edges_outside_support_are_clipped_renormalised_and_warned():
    # the bin (18, 20] of the support is dropped (0.2 of the mass); [10, 15) and [15, 18)
    # are kept with 0.5 and 0.3 and renormalised to 5/8 and 3/8
    spec = RunSpec.from_dict(
        binned({"uniform": {"low": 10, "high": 20}}, 7.0, {"bins": {"energy_keV": [10, 15, 18]}})
    )
    with pytest.warns(UserWarning, match="energy_keV"):
        bins = make_bins(spec)
    np.testing.assert_allclose([b.energy_range for b in bins], [(10, 15), (15, 18)])
    assert [b.probability for b in bins] == pytest.approx([5 / 8, 3 / 8])


def test_list_edges_clipped_to_support_without_warning_when_drop_is_small():
    # the support is [10, 20]; edges beyond it are clipped, and mass outside the edges is
    # only 0.05 % here, under the 0.1 % that warns
    spec = RunSpec.from_dict(
        binned({"uniform": {"low": 10, "high": 20}}, 7.0, {"bins": {"energy_keV": [10.005, 25]}})
    )
    with warnings.catch_warnings(record=True) as seen:
        warnings.simplefilter("always")
        bins = make_bins(spec)
    assert not [w for w in seen if issubclass(w.category, UserWarning)]
    assert len(bins) == 1
    assert bins[0].energy_range == pytest.approx((10.005, 20.0))
    assert bins[0].probability == pytest.approx(1.0, abs=1e-12)


def test_list_edges_with_no_probability_refused():
    spec = RunSpec.from_dict(
        binned({"uniform": {"low": 10, "high": 20}}, 7.0, {"bins": {"energy_keV": [30, 40]}})
    )
    with pytest.raises(SpecError, match="beam.bins.energy_keV"):
        make_bins(spec)


def test_zero_probability_bins_are_dropped():
    spec = RunSpec.from_dict(
        binned(
            {"histogram": {"edges": [10, 11, 12, 13], "counts": [1, 0, 1]}},
            7.0,
            {"bins": {"energy_keV": [10, 11, 12, 13]}},
        )
    )
    bins = make_bins(spec)
    np.testing.assert_allclose([b.energy_range for b in bins], [(10, 11), (12, 13)])
    assert [b.probability for b in bins] == pytest.approx([0.5, 0.5])


def test_bins_frame_columns_and_nan_for_fixed():
    spec = RunSpec.from_dict(
        binned({"uniform": {"low": 10, "high": 20}}, 7.0, {"bins": {"energy_keV": 2}})
    )
    bins = make_bins(spec)
    frame = bins_frame(bins, [3, 4])
    assert list(frame.columns) == [
        "bin",
        "energy_keV",
        "angle_deg",
        "energy_low_keV",
        "energy_high_keV",
        "angle_low_deg",
        "angle_high_deg",
        "probability",
        "n_ions",
    ]
    assert list(frame["bin"]) == [0, 1]
    assert list(frame["n_ions"]) == [3, 4]
    assert frame["energy_low_keV"].tolist() == pytest.approx([10.0, 15.0])
    assert frame["energy_high_keV"].tolist() == pytest.approx([15.0, 20.0])
    assert frame["angle_low_deg"].isna().all() and frame["angle_high_deg"].isna().all()
    assert frame["angle_deg"].tolist() == [7.0, 7.0]


def test_bins_frame_rejects_wrong_count():
    bins = make_bins(RunSpec.from_dict(base()))
    with pytest.raises(ValueError):
        bins_frame(bins, [1, 2])


def test_beam_bin_is_frozen():
    b = BeamBin(0, 1.0, 2.0, 1.0, None, None)
    with pytest.raises(dataclasses.FrozenInstanceError):
        b.index = 3  # type: ignore[misc]


# --- allocation -----------------------------------------------------------------------


def test_allocate_examples():
    assert allocate_ions([0.5, 0.3, 0.2], 10).tolist() == [5, 3, 2]
    assert allocate_ions([0.5, 0.5], 1).tolist() == [1, 0]
    assert allocate_ions([0.0, 1.0, 0.0], 7).tolist() == [0, 7, 0]
    assert allocate_ions([0.5, 0.0, 0.5], 4).tolist() == [2, 0, 2]


def test_allocate_zero_total_and_empty():
    assert allocate_ions([0.5, 0.5], 0).tolist() == [0, 0]
    assert allocate_ions([1.0, 2.0], 0).tolist() == [0, 0]


def test_allocate_normalises_probabilities():
    assert allocate_ions([5.0, 3.0, 2.0], 10).tolist() == [5, 3, 2]


@pytest.mark.parametrize(
    "probabilities, total",
    [([0.5, -0.1, 0.6], 10), ([0.5, 0.5], -1), ([0.0, 0.0], 3), ([], 3)],
)
def test_allocate_rejects_bad_input(probabilities, total):
    with pytest.raises(ValueError):
        allocate_ions(probabilities, total)


def test_allocate_matches_heap_reference_on_random_cases():
    rng = np.random.default_rng(20261106)
    for _ in range(200):
        k = int(rng.integers(1, 9))
        p = rng.random(k)
        if rng.random() < 0.3:  # zero-probability bins, and repeated values for ties
            p[rng.integers(0, k)] = 0.0
            p = np.round(p, 1)
        if p.sum() == 0:
            p[0] = 1.0
        total = int(rng.integers(0, 60))
        got = allocate_ions(p, total)
        assert got.sum() == total
        assert np.array_equal(got, reference_allocation(p, total))


def test_allocate_is_house_monotone_on_random_cases():
    rng = np.random.default_rng(7)
    for _ in range(200):
        k = int(rng.integers(1, 12))
        p = rng.random(k)
        total = int(rng.integers(0, 500))
        a = allocate_ions(p, total)
        b = allocate_ions(p, total + 1)
        assert np.all(b >= a)
        assert b.sum() == total + 1


def test_allocate_ties_go_to_lower_index():
    assert allocate_ions([1, 1, 1], 1).tolist() == [1, 0, 0]
    assert allocate_ions([1, 1, 1], 2).tolist() == [1, 1, 0]


def test_allocate_ten_million_over_two_thousand_bins_is_fast():
    rng = np.random.default_rng(3)
    p = rng.random(2000)
    start = time.perf_counter()
    counts = allocate_ions(p, 10_000_000)
    elapsed = time.perf_counter() - start
    assert counts.sum() == 10_000_000
    # the brief asks for 1 s; the test allows 2 s so slow CI machines do not flake
    assert elapsed <= 2.0, f"allocation took {elapsed:.2f} s"


def test_batch_allocation_is_difference_and_never_negative():
    p = [0.5, 0.3, 0.2]
    done, size = 3, 4
    batch = beam_batch_allocation(p, done, size)
    assert np.array_equal(batch, allocate_ions(p, done + size) - allocate_ions(p, done))
    assert np.all(batch >= 0)
    assert batch.sum() == size


def test_batch_allocation_over_many_batches_adds_up():
    p = np.array([0.1, 0.6, 0.3])
    done = np.zeros(3, dtype=np.int64)
    for size in (7, 1, 11, 0, 5):
        done = done + beam_batch_allocation(p, int(done.sum()), size)
    assert np.array_equal(done, allocate_ions(p, 24))


# --- the spec of one bin ----------------------------------------------------------------


def _bin_for(spec):
    bins = make_bins(spec)
    return bins[len(bins) // 2]


def test_bin_spec_is_fixed_plain_and_matches_hand_written_spec(tmp_path):
    spec = RunSpec.from_dict(
        binned(
            {"uniform": {"low": 10, "high": 20}},
            {"uniform": {"low": 0, "high": 6}},
            azimuth_deg={"uniform": {"low": 0, "high": 360}},
            position={"y_nm": {"uniform": {"low": -5, "high": 5}}},
            beam={"mode": "exact", "bins": {"energy_keV": 4, "angle_deg": 3}},
        )
    )
    b = _bin_for(spec)
    got = bin_spec(spec, b, 1234, 99)

    assert isinstance(got, RunSpec)
    assert got.is_distributed is False
    assert got.beam is None
    assert got.ion.azimuth_deg is None
    assert got.ion.position is None
    assert got.number_ions == 1234
    assert got.seed == 99
    assert got.ion.energy_keV == b.energy_keV
    assert got.ion.angle_deg == b.angle_deg

    hand = base()
    hand["ion"]["energy_keV"] = float(b.energy_keV)
    hand["ion"]["angle_deg"] = float(b.angle_deg)
    hand["number_ions"] = 1234
    hand["seed"] = 99
    hand_spec = RunSpec.from_dict(hand)
    assert got.to_dict() == hand_spec.to_dict()

    # TRIM.IN of the bin is byte-identical to the one of the hand-written fixed spec
    (tmp_path / "bin").mkdir()
    (tmp_path / "hand").mkdir()
    TRIM.from_spec(got)._write_input_files(str(tmp_path / "bin"))
    TRIM.from_spec(hand_spec)._write_input_files(str(tmp_path / "hand"))
    assert (tmp_path / "bin" / "TRIM.IN").read_bytes() == (
        tmp_path / "hand" / "TRIM.IN"
    ).read_bytes()


def test_bin_spec_of_a_plain_spec_keeps_it_plain(tmp_path):
    spec = RunSpec.from_dict(base())
    got = bin_spec(spec, make_bins(spec)[0], 50, 3)
    assert got.is_distributed is False
    assert got.to_dict() == RunSpec.from_dict({**base(), "number_ions": 50, "seed": 3}).to_dict()
