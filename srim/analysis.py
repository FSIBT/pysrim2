"""Summary statistics for SRIM results"""

import numpy as np


def implant_summary(df):
    """Summarize per-ion final positions the way SRIM does in the header of ``RANGE.txt``.

    The values reproduce :attr:`srim.output.Range.summary` (which SRIM computes
    from all ions) to within a percent or two; SRIM's ``RANGE_3D.txt`` can lack
    a few ions.

    Parameters
    ----------
    df : :class:`pandas.DataFrame`
        needs columns ``depth_A``, ``y_A`` and ``z_A`` [Ang]
        (:attr:`srim.output.Range3D.df`)

    Returns
    -------
    :obj:`dict`
        - ``n_ions``: number of ions
        - ``mean_depth_A``: projected range, mean of ``depth_A``
        - ``straggle_depth_A``: projected straggling, sample standard
          deviation (``ddof=1``) of ``depth_A`` (``nan`` for one ion)
        - ``lateral_range_A``: mean of ``(|y| + |z|) / 2``
        - ``lateral_straggle_A``: sample standard deviation of the signed
          ``y`` and ``z`` values taken together (2N values, ``ddof=1``)
        - ``radial_range_A``: mean of ``r = sqrt(y**2 + z**2)``
        - ``radial_straggle_A``: sample standard deviation of ``r``
        - ``skewness``, ``kurtosis``: third and fourth standardized moments
          of ``depth_A`` (population form; kurtosis is 3 for a Gaussian)
    """
    depth = np.asarray(df["depth_A"], dtype=float)
    y = np.asarray(df["y_A"], dtype=float)
    z = np.asarray(df["z_A"], dtype=float)
    r = np.hypot(y, z)
    n = len(depth)
    nan = float("nan")
    yz = np.concatenate([y, z])
    sd = depth.std()
    return {
        "n_ions": n,
        "mean_depth_A": float(depth.mean()),
        "straggle_depth_A": float(depth.std(ddof=1)) if n > 1 else nan,
        "lateral_range_A": float(((np.abs(y) + np.abs(z)) / 2).mean()),
        "lateral_straggle_A": float(yz.std(ddof=1)) if n > 0 and len(yz) > 1 else nan,
        "radial_range_A": float(r.mean()),
        "radial_straggle_A": float(r.std(ddof=1)) if n > 1 else nan,
        "skewness": float(((depth - depth.mean()) ** 3).mean() / sd**3) if sd > 0 else nan,
        "kurtosis": float(((depth - depth.mean()) ** 4).mean() / sd**4) if sd > 0 else nan,
    }
