"""Plot a beam: the sampled ions as a density histogram, or the bins of a binned beam as bars,
with the defined density drawn over them.

matplotlib is optional (the ``plot`` extra) and is imported only by :func:`plot_beam`.
"""

from pathlib import Path

import numpy as np

from ..spec import RunSpec
from .run import sampled_ions
from .shapes import Shape
from .spec import effective

#: the beam parameters :func:`plot_beam` can plot
PARAMETERS = ("energy_keV", "angle_deg", "azimuth_deg", "y_nm", "z_nm", "depth_nm")
#: a binned beam's bin columns (low, high) of the parameters it bins
BIN_COLUMNS = {
    "energy_keV": ("energy_low_keV", "energy_high_keV"),
    "angle_deg": ("angle_low_deg", "angle_high_deg"),
}
#: points of the drawn density
_CURVE_POINTS = 400


def _spec_of(results) -> RunSpec:
    """The run spec of ``results``: ``results.spec``, else the ``run.yaml`` of its folder."""
    spec = getattr(results, "spec", None)
    if spec is not None:
        return RunSpec.from_dict(spec)
    directory = getattr(results, "directory", None)
    if directory is not None and (Path(directory) / "run.yaml").is_file():
        return RunSpec.from_yaml(Path(directory) / "run.yaml")
    raise ValueError("these results have no run spec (no run.yaml), so there is no beam to plot")


def plot_beam(results, parameter="energy_keV", ax=None, bins=50):
    """Plot a distributed beam's ``parameter``: the defined density and the sampled beam.

    An exact run is drawn as a density histogram of the sampled ions (:func:`sampled_ions`,
    ``bins`` bars) with the defined density (the clipped distribution of
    :func:`srim.beam.spec.effective`) as a line. A binned run is drawn as one bar per bin of
    ``parameter`` (height: probability per unit of the parameter, summed over the bins of the
    other parameter) with the defined density as a line. Binned runs bin ``energy_keV`` and
    ``angle_deg`` only.

    Parameters
    ----------
    results : :class:`srim.output.Results` or :class:`srim.scan.MergedResults`
        the results of :func:`srim.beam.run_beam` (or of a run of the beam)
    parameter : str
        ``energy_keV`` (default), ``angle_deg``, ``azimuth_deg``, ``y_nm``, ``z_nm`` or
        ``depth_nm``
    ax : matplotlib Axes, optional
        where to draw; a new figure's Axes if omitted
    bins : int
        histogram bins of an exact run

    Returns
    -------
    matplotlib.axes.Axes

    Raises
    ------
    ValueError
        for an unknown parameter, a parameter that is fixed in the run (nothing to plot), or a
        binned run and a parameter it does not bin
    ImportError
        when matplotlib is not installed (``pip install "pysrim2[plot]"``)
    """
    try:
        import matplotlib.pyplot as plt
    except ImportError as exc:
        raise ImportError('plot_beam needs matplotlib: pip install "pysrim2[plot]"') from exc
    if parameter not in PARAMETERS:
        raise ValueError(f"parameter must be one of {', '.join(PARAMETERS)}, got {parameter!r}")
    spec = _spec_of(results)
    shape = effective(spec)[parameter]
    if not isinstance(shape, Shape):
        raise ValueError(f"{parameter} is fixed in this run: there is no distribution to plot")
    binned = getattr(results, "beam_bins", None) is not None
    if binned and parameter not in BIN_COLUMNS:
        raise ValueError(
            f"a binned run bins {' and '.join(BIN_COLUMNS)} only; {parameter} is not binned"
        )
    if ax is None:
        _, ax = plt.subplots()

    if binned:
        low, high = BIN_COLUMNS[parameter]
        table = results.beam_bins
        grouped = table.groupby([low, high], as_index=False)["probability"].sum()
        width = (grouped[high] - grouped[low]).to_numpy(dtype=float)
        density = grouped["probability"].to_numpy(dtype=float) / width
        ax.bar(grouped[low].to_numpy(dtype=float), density, width=width, align="edge")
        ax.set_ylabel("probability density")
    else:
        values = sampled_ions(results)[parameter].to_numpy(dtype=float)
        ax.hist(values, bins=bins, density=True)
        ax.set_ylabel("probability density")

    low, high = shape.support()
    curve = np.linspace(low, high, _CURVE_POINTS)
    ax.plot(curve, np.asarray(shape.pdf(curve), dtype=float), label="defined density")
    ax.set_xlabel(parameter)
    return ax
