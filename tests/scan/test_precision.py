"""Precision targets of a scan: validation and dict round trip (no estimator here)."""

import pytest

from srim.scan import Precision
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
