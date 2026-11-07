import math

import beam_funcs
import numpy as np
import pytest
from numpy.random import PCG64, Generator

from srim.beam import (
    GRID_POINTS,
    Fixed,
    Function,
    Gaussian,
    Histogram,
    LogNormal,
    Triangular,
    Uniform,
    shape_from_dict,
)
from srim.beam import shapes as shapes_module
from srim.spec import SpecError


def test_grid_points():
    assert GRID_POINTS == 4097


def test_gaussian_moments():
    s = Gaussian(100, 5).sample(Generator(PCG64(1)), 200_000)
    assert abs(s.mean() - 100) < 0.05
    assert abs(s.std() - 5) < 0.05
    assert Gaussian(100, 5).support() == (75.0, 125.0)


def test_gaussian_range_truncates():
    g = Gaussian(100, 5, range=(100, 125))
    assert g.cdf(100) == 0
    assert g.mean() == pytest.approx(103.989, abs=0.01)


def test_uniform_probabilities():
    u = Uniform(10, 20)
    assert u.probabilities([10, 15, 20]).tolist() == [0.5, 0.5]
    assert u.interval_mean(10, 15) == pytest.approx(12.5, abs=1e-9)
    assert Uniform(0, 10).equal_probability_edges(5) == pytest.approx([0, 2, 4, 6, 8, 10])


def test_triangular():
    t = Triangular(0, 1, 2)
    assert t.cdf(1) == pytest.approx(0.5, abs=1e-3)
    assert t.mean() == pytest.approx(1, abs=1e-3)


def test_lognormal_median():
    assert LogNormal(0, 0.5).cdf(1.0) == pytest.approx(0.5, abs=1e-3)


def test_histogram_exact():
    h = Histogram([0, 1, 3], [1, 1])
    assert h.probabilities([0, 1, 3]).tolist() == [0.5, 0.5]
    assert h.pdf([0.5, 2]).tolist() == [0.5, 0.25]
    assert h.interval_mean(1, 3) == 2
    assert Histogram.from_centres([1, 2, 4], [1, 1, 2]).edges == [0.5, 1.5, 3, 5]


def test_histogram_range_truncates():
    h = Histogram([0, 1, 3], [1, 1], range=(0.5, 2))
    assert h.support() == (0.5, 2.0)
    assert h.mean() == pytest.approx(1.125, abs=1e-12)


def test_from_centres_needs_two_centres():
    with pytest.raises(SpecError):
        Histogram.from_centres([1], [1])


def test_function_ramp():
    f = Function("python:beam_funcs:ramp", range=(0, 1))
    assert f.cdf(0.5) == pytest.approx(0.25, abs=1e-4)
    assert f.mean() == pytest.approx(2 / 3, abs=1e-4)


def test_function_callable_reference():
    f = Function(beam_funcs.ramp, range=(0, 1))
    assert f.to_dict() == {"function": {"ref": "python:beam_funcs:ramp", "range": [0.0, 1.0]}}


def test_function_inline_contains_source():
    d = Function("python:beam_funcs:ramp", range=(0, 1)).to_dict(inline=True)
    assert "def ramp" in d["function"]["source"]


def test_source_of_falls_back_to_reference():
    assert shapes_module._source_of(math.sin) == "python:math:sin"


def test_function_errors():
    with pytest.raises(SpecError):
        Function("python:beam_funcs:ramp", range=None)
    with pytest.raises(SpecError):
        shape_from_dict({"function": {"ref": "python:beam_funcs:bad", "range": [0, 1]}}, "k")
    with pytest.raises(SpecError):
        Function("python:beam_funcs:bad", range=(0, 1)).cdf(0.5)
    assert Function("python:beam_funcs:flat", range=(1, 1)).degenerate() == 1.0


def test_degenerate_becomes_fixed():
    assert shape_from_dict({"gaussian": {"mean": 100, "sigma": 0}}, "ion.energy_keV") == 100.0
    assert shape_from_dict({"uniform": {"low": 3, "high": 3}}, "k") == 3.0
    assert shape_from_dict({"fixed": {"value": 2}}, "k") == 2.0
    assert Fixed(2).degenerate() == 2.0
    assert shape_from_dict(5, "k") == 5.0


def test_clip():
    assert Gaussian(1, 2).clip(0, math.inf, "ion.energy_keV").support()[0] == 0
    with pytest.raises(SpecError, match="ion.energy_keV"):
        Uniform(-5, -1).clip(0, math.inf, "ion.energy_keV")


def test_round_trip():
    shapes = [
        Gaussian(100, 5),
        Gaussian(100, 5, range=(100, 125)),
        Uniform(10, 20),
        Triangular(0, 1, 2),
        LogNormal(0, 0.5),
        Histogram([0, 1, 3], [1, 1]),
        Histogram.from_centres([1, 2, 4], [1, 1, 2]),
        Function("python:beam_funcs:ramp", range=(0, 1)),
    ]
    for s in shapes:
        assert shape_from_dict(s.to_dict(), "k").to_dict() == s.to_dict()


def test_unknown():
    with pytest.raises(SpecError, match="ion.energy_keV") as info:
        shape_from_dict({"gauss": {"mean": 1, "sigma": 1}}, "ion.energy_keV")
    assert "gaussian" in str(info.value)
    with pytest.raises(SpecError, match="ion.energy_keV") as info:
        shape_from_dict({"gaussian": {"mean": 1}}, "ion.energy_keV")
    assert "sigma" in str(info.value)


def test_same_seed_same_sample():
    a = Gaussian(100, 5).sample(Generator(PCG64(7)), 10)
    b = Gaussian(100, 5).sample(Generator(PCG64(7)), 10)
    assert np.array_equal(a, b)
