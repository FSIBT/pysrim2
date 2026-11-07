"""plot_beam: the sampled beam (a density histogram) or the bins (bars), with the density."""

import os
import sys

import pytest

matplotlib = pytest.importorskip("matplotlib")
matplotlib.use("Agg")

from matplotlib.lines import Line2D  # noqa: E402
from matplotlib.patches import Rectangle  # noqa: E402

from srim.beam import plot_beam, run_beam  # noqa: E402
from test_beam_scan import SPEC_DIR_VAR, beam_base, plain_runner  # noqa: E402
from test_run_beam import dat_runner  # noqa: E402


@pytest.fixture(autouse=True)
def spec_dir(tmp_path, monkeypatch):
    directory = tmp_path / "specs"
    directory.mkdir()
    monkeypatch.setenv(SPEC_DIR_VAR, str(directory))
    yield directory
    pyplot = sys.modules.get("matplotlib.pyplot")  # absent if a test removed matplotlib
    if pyplot is not None:
        pyplot.close("all")


def _bars(ax):
    return [p for p in ax.patches if isinstance(p, Rectangle)]


def test_exact_beam_is_a_histogram_with_the_defined_density(tmp_path):
    spec = beam_base(mode="exact").with_overrides(["number_ions=500"])
    results = run_beam(spec, runner=dat_runner)
    ax = plot_beam(results, "energy_keV")
    assert len(_bars(ax)) == 50  # the default 50 histogram bins
    (line,) = [ln for ln in ax.lines if isinstance(ln, Line2D)]
    x = line.get_xdata()
    assert x.min() == pytest.approx(10.0) and x.max() == pytest.approx(20.0)


def test_exact_batched_beam_plots_the_merged_sample(tmp_path):
    spec = beam_base(mode="exact").with_overrides(["number_ions=150000"])
    with run_beam(spec, runner=dat_runner, output_dir=tmp_path / "out") as results:
        ax = plot_beam(results, "energy_keV", bins=20)
    assert len(_bars(ax)) == 20
    assert any(isinstance(ln, Line2D) for ln in ax.lines)


def test_binned_beam_has_one_bar_per_energy_bin():
    spec = beam_base(mode="binned", bins={"energy_keV": 4})
    with run_beam(spec, runner=plain_runner) as results:
        ax = plot_beam(results, "energy_keV")
    assert len(_bars(ax)) == 4
    assert any(isinstance(ln, Line2D) for ln in ax.lines)


def test_binned_bar_heights_are_probability_per_keV():
    spec = beam_base(mode="binned", bins={"energy_keV": 4})
    with run_beam(spec, runner=plain_runner) as results:
        table = results.beam_bins.copy()
        ax = plot_beam(results, "energy_keV")
    widths = sorted(b.get_width() for b in _bars(ax))
    assert widths == pytest.approx([2.5, 2.5, 2.5, 2.5])
    total = sum(b.get_height() * b.get_width() for b in _bars(ax))
    assert total == pytest.approx(table["probability"].sum())


def test_fixed_parameter_is_refused():
    spec = beam_base(mode="binned", bins={"energy_keV": 2})  # angle is fixed at 7 degrees
    with run_beam(spec, runner=plain_runner) as results:
        with pytest.raises(ValueError, match="angle_deg"):
            plot_beam(results, "angle_deg")


def test_unknown_parameter_is_refused():
    spec = beam_base(mode="binned", bins={"energy_keV": 2})
    with run_beam(spec, runner=plain_runner) as results:
        with pytest.raises(ValueError, match="parameter"):
            plot_beam(results, "charge")


def test_missing_matplotlib_names_the_extra(monkeypatch):
    monkeypatch.setitem(sys.modules, "matplotlib", None)
    spec = beam_base(mode="binned", bins={"energy_keV": 2})
    with run_beam(spec, runner=plain_runner) as results:
        with pytest.raises(ImportError, match=r"pysrim2\[plot\]"):
            plot_beam(results, "energy_keV")
