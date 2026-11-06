"""Precision targets of a scan: validation and dict round trip (no estimator here)."""

import math

import numpy as np
import pytest

from srim.scan import Precision
from srim.scan.precision import batch_error, batches_to_add, goal_of, initial_batches, is_met
from srim.spec import SpecError


def test_defaults_and_round_trip():
    p = Precision("sputter_yield", rel=0.05)
    assert (p.min_ions, p.max_ions, p.batch_ions) == (1000, 100_000, 1000)
    assert p.abs is None
    assert Precision.from_dict(p.to_dict()) == p


def test_absolute_precision_is_accepted():
    p = Precision.from_dict({"target": "sputter_yield", "abs": 0.01, "max_ions": 5000})
    assert p.abs == 0.01 and p.rel is None and p.max_ions == 5000


@pytest.mark.parametrize(
    "kwargs, message",
    [
        ({"rel": None, "abs": None}, "exactly one of rel"),
        ({"rel": 0.1, "abs": 0.1}, "exactly one of rel"),
        ({"rel": 0.0}, "precision.rel must be a finite number > 0"),
        ({"rel": -1.0}, "precision.rel must be a finite number > 0"),
        ({"abs": float("nan")}, "precision.abs must be a finite number"),
        ({"rel": 0.1, "min_ions": 0}, "precision.min_ions must be >= 1"),
        (
            {"rel": 0.1, "min_ions": 10, "max_ions": 5},
            "precision.min_ions must be <= precision.max_ions",
        ),
        ({"rel": 0.1, "batch_ions": 0}, "precision.batch_ions must be >= 1"),
        ({"rel": 0.1, "min_ions": True}, "precision.min_ions must be an integer"),
    ],
)
def test_invalid_values_name_the_key(kwargs, message):
    with pytest.raises(SpecError, match=message):
        Precision("sputter_yield", **kwargs)


@pytest.mark.parametrize("target", ["", None, 3])
def test_target_must_be_a_name(target):
    with pytest.raises(SpecError, match="precision.target"):
        Precision(target, rel=0.1)


def test_from_dict_rejects_unknown_and_missing_keys():
    with pytest.raises(SpecError, match="unknown key 'scan.precision.tolerance'"):
        Precision.from_dict({"target": "x", "rel": 0.1, "tolerance": 1}, prefix="scan.precision.")
    with pytest.raises(SpecError, match="missing required key 'scan.precision.target'"):
        Precision.from_dict({"rel": 0.1}, prefix="scan.precision.")


# -- the estimator and the scheduling rules (pure functions) -------------------------------


def test_batch_error_with_equal_weights_is_the_standard_error_of_the_mean():
    rng = np.random.default_rng(1)
    values = rng.normal(10.0, 2.0, size=7)
    err = batch_error(values, np.full(7, 40.0))
    assert err == pytest.approx(np.std(values, ddof=1) / math.sqrt(7), rel=1e-12)


def test_batch_error_with_ion_weights_matches_the_formula():
    values = [1.0, 2.0, 4.0]
    weights = [10, 20, 30]
    total = sum(weights)
    xbar = sum(w * x for w, x in zip(weights, values, strict=True)) / total
    expected = (
        math.sqrt(sum((w * (x - xbar)) ** 2 for w, x in zip(weights, values, strict=True)))
        / total
        * math.sqrt(3 / 2)
    )
    assert batch_error(values, weights) == pytest.approx(expected, rel=1e-12)


def test_batch_error_needs_two_finite_batches_and_positive_weights():
    assert math.isnan(batch_error([1.0], [10]))
    assert math.isnan(batch_error([1.0, float("nan")], [10, 10]))
    assert math.isnan(batch_error([1.0, float("inf")], [10, 10]))
    with pytest.raises(ValueError, match="weights must be positive"):
        batch_error([1.0, 2.0], [10, 0])
    with pytest.raises(ValueError, match="same length"):
        batch_error([1.0, 2.0], [10])


def test_initial_batches_cover_the_minimum_but_not_beyond_the_maximum():
    p = Precision("x", rel=0.1, min_ions=40, max_ions=200, batch_ions=20)
    assert initial_batches(p) == 2
    p = Precision("x", rel=0.1, min_ions=100, max_ions=200, batch_ions=20)
    assert initial_batches(p) == 5
    p = Precision("x", rel=0.1, min_ions=10, max_ions=50, batch_ions=20)
    assert initial_batches(p) == 2  # at least two batches for an error
    p = Precision("x", rel=0.1, min_ions=5, max_ions=10, batch_ions=20)
    assert initial_batches(p) == 1  # the whole budget is one batch


def test_goal_is_the_absolute_error_or_the_relative_error_of_the_value():
    assert goal_of(Precision("x", abs=0.5), 1000.0) == 0.5
    assert goal_of(Precision("x", rel=0.05), -20.0) == pytest.approx(1.0)


def test_a_zero_or_nan_value_never_meets_a_relative_target():
    rel = Precision("x", rel=0.05)
    assert not is_met(0.0, goal_of(rel, 0.0))
    assert not is_met(0.0, goal_of(rel, float("nan")))
    assert is_met(0.04, goal_of(rel, 1.0))
    assert not is_met(0.06, goal_of(rel, 1.0))
    assert not is_met(float("nan"), 1.0)  # one batch: no error


def test_batches_to_add_follows_the_error_scaling_and_is_capped_by_the_maximum():
    p = Precision("x", abs=1.0, min_ions=40, max_ions=1000, batch_ions=20)
    # error twice the goal: about 4x the ions so far (times 1.1): 40 -> 176 -> 7 more batches
    assert batches_to_add(p, ions=40, err=2.0, goal=1.0, n_batches=2) == 7
    # the maximum leaves room for 3 batches only
    tight = Precision("x", abs=1.0, min_ions=40, max_ions=100, batch_ions=20)
    assert batches_to_add(tight, ions=40, err=2.0, goal=1.0, n_batches=2) == 3
    assert batches_to_add(tight, ions=100, err=2.0, goal=1.0, n_batches=5) == 0


def test_batches_to_add_always_adds_one_when_the_error_is_already_small_enough():
    p = Precision("x", abs=1.0, min_ions=40, max_ions=1000, batch_ions=20)
    assert batches_to_add(p, ions=40, err=0.0, goal=1.0, n_batches=2) == 1


def test_batches_to_add_doubles_when_the_goal_cannot_be_computed():
    p = Precision("x", rel=0.05, min_ions=40, max_ions=1000, batch_ions=20)
    assert batches_to_add(p, ions=40, err=1.0, goal=0.0, n_batches=2) == 2
    assert batches_to_add(p, ions=40, err=float("nan"), goal=float("nan"), n_batches=3) == 3


def simulate_until(precision, seed, sigma=1.0, mean=10.0):
    """The scheduling loop of run_scan on synthetic batch means: the estimate is the mean
    of the batch values (equal weights per batch of ``batch_ions``), noise ``sigma``."""
    rng = np.random.default_rng(seed)
    values, sizes = [], []

    def run_batch(size):
        sizes.append(size)
        values.append(rng.normal(mean, sigma))

    first = initial_batches(precision)
    for _ in range(first):
        run_batch(precision.batch_ions)
    while True:
        ions = sum(sizes)
        err = batch_error(values, sizes)
        value = float(np.average(values, weights=sizes))
        goal = goal_of(precision, value)
        if is_met(err, goal):
            return "done", ions, value, err
        if ions >= precision.max_ions:
            return "max_ions", ions, value, err
        for _ in range(batches_to_add(precision, ions, err, goal, len(values))):
            size = min(precision.batch_ions, precision.max_ions - sum(sizes))
            run_batch(size)


def test_synthetic_noise_converges_to_the_goal_within_the_maximum():
    p = Precision("x", rel=0.02, min_ions=40, max_ions=20_000, batch_ions=20)
    status, ions, value, err = simulate_until(p, seed=3)
    assert status == "done"
    assert err <= 0.02 * abs(value)
    assert 40 <= ions <= 20_000
    # the ideal is 25 batches (500 ions) for an error of 0.2; the rule may overshoot when
    # the first error estimates (two batches) are noisy, so only the bound is checked


def test_synthetic_noise_that_never_converges_stops_at_the_maximum():
    p = Precision("x", abs=1e-9, min_ions=40, max_ions=300, batch_ions=20)
    status, ions, _, err = simulate_until(p, seed=3)
    assert status == "max_ions"
    assert ions == 300
    assert err > 1e-9


def test_synthetic_runs_are_deterministic_given_the_seed():
    p = Precision("x", rel=0.02, min_ions=40, max_ions=20_000, batch_ions=20)
    assert simulate_until(p, seed=7) == simulate_until(p, seed=7)
