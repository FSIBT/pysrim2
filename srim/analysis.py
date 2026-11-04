"""Summary statistics for SRIM results"""

import numpy as np


def implant_summary(df):
    """Summarize per-ion final positions (:attr:`srim.output.Range3D.df`).

    Parameters
    ----------
    df : :class:`pandas.DataFrame`
        needs columns ``depth_A``, ``y_A`` and ``z_A`` [Ang]

    Returns
    -------
    :obj:`dict`
        - ``n_ions``: number of ions
        - ``mean_depth_A``: mean final depth
        - ``straggle_depth_A``: depth straggle, the *sample* standard deviation
          (``ddof=1``; ``nan`` for a single ion)
        - ``mean_lateral_A``: mean of the radial distance
          ``r = sqrt(y**2 + z**2)`` from the beam axis
        - ``lateral_straggle_A``: root mean square of ``r``,
          ``sqrt(mean(y**2 + z**2))`` (population form, no ``ddof``; this is
          the definition the pysrim2 reference example has always reported)
    """
    depth = np.asarray(df["depth_A"], dtype=float)
    r = np.hypot(np.asarray(df["y_A"], dtype=float), np.asarray(df["z_A"], dtype=float))
    n = len(depth)
    return {
        "n_ions": n,
        "mean_depth_A": float(depth.mean()),
        "straggle_depth_A": float(depth.std(ddof=1)) if n > 1 else float("nan"),
        "mean_lateral_A": float(r.mean()),
        "lateral_straggle_A": float(np.sqrt((r**2).mean())),
    }
