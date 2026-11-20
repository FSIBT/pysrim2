"""Summary statistics for SRIM results"""

import math

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
          ``y`` and ``z`` values taken together (2N values, ``ddof=1``). Agrees
          with SRIM's ``RANGE.txt`` header within ~1 % on the reference data;
          SRIM's exact formula is undocumented.
        - ``radial_range_A``: mean of ``r = sqrt(y**2 + z**2)``
        - ``radial_straggle_A``: sample standard deviation of ``r``
        - ``skewness``, ``kurtosis``: third and fourth standardized moments
          of ``depth_A`` (population form; kurtosis is 3 for a Gaussian)
    """
    if len(df) == 0:
        return _empty_summary()
    nan = float("nan")
    depth = np.asarray(df["depth_A"], dtype=float)
    y = np.asarray(df["y_A"], dtype=float)
    z = np.asarray(df["z_A"], dtype=float)
    r = np.hypot(y, z)
    n = len(depth)
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


def _empty_summary():
    """The summary of a table without ions: every value ``nan`` and ``n_ions`` 0."""
    return dict.fromkeys(
        (
            "mean_depth_A",
            "straggle_depth_A",
            "lateral_range_A",
            "lateral_straggle_A",
            "radial_range_A",
            "radial_straggle_A",
            "skewness",
            "kurtosis",
        ),
        float("nan"),
    ) | {"n_ions": 0}


class _Moments:
    """Count, mean and the central sums ``M2``, ``M3``, ``M4`` of the values seen so far.

    Two parts are merged with the one-pass update of Pebay (2008), which works on sums of
    deviations from the mean, never on sums of raw values. A large offset (a depth of 1e6
    Angstrom with a spread of 1e2) therefore does not cancel.
    """

    __slots__ = ("n", "mean", "m2", "m3", "m4")

    def __init__(self, n=0, mean=0.0, m2=0.0, m3=0.0, m4=0.0):
        self.n, self.mean, self.m2, self.m3, self.m4 = n, mean, m2, m3, m4

    @classmethod
    def of(cls, values):
        """The moments of one array, about its own mean."""
        n = values.size
        if n == 0:
            return cls()
        mean = float(values.mean())
        d = values - mean
        d2 = d * d
        return cls(n, mean, float(d2.sum()), float(np.sum(d2 * d)), float(np.sum(d2 * d2)))

    def merge(self, other):
        """The moments of the union of the two parts."""
        if other.n == 0:
            return self
        if self.n == 0:
            return other
        na, nb = float(self.n), float(other.n)
        n = na + nb
        delta = other.mean - self.mean
        mean = self.mean + delta * nb / n
        m2 = self.m2 + other.m2 + delta**2 * na * nb / n
        m3 = (
            self.m3
            + other.m3
            + delta**3 * na * nb * (na - nb) / n**2
            + 3 * delta * (na * other.m2 - nb * self.m2) / n
        )
        m4 = (
            self.m4
            + other.m4
            + delta**4 * na * nb * (na * na - na * nb + nb * nb) / n**3
            + 6 * delta**2 * (na * na * other.m2 + nb * nb * self.m2) / n**2
            + 4 * delta * (na * other.m3 - nb * self.m3) / n
        )
        return _Moments(self.n + other.n, mean, m2, m3, m4)


class _Stream:
    """One quantity of the chunks: its moments, about a shift taken from its first value.

    The shift (the first value) keeps the means that are merged of the order of the spread,
    not of the offset: the rounding of a mean of values near 1e6 is then not in the skewness.
    """

    __slots__ = ("shift", "moments")

    def __init__(self):
        self.shift = None
        self.moments = _Moments()

    def add(self, values):
        if values.size == 0:
            return
        if self.shift is None:
            self.shift = float(values[0])
        self.moments = self.moments.merge(_Moments.of(values - self.shift))

    @property
    def n(self):
        return self.moments.n

    @property
    def mean(self):
        return self.shift + self.moments.mean if self.shift is not None else float("nan")


def implant_summary_chunks(chunks):
    """Summarize per-ion final positions from chunks of rows, the same as :func:`implant_summary`.

    Every statistic of :func:`implant_summary` is a mean or a central moment, so the chunks
    are accumulated one at a time and the whole table is never in memory at once. Use it
    with :meth:`srim.output.Range3D.iter_chunks`, for example
    ``implant_summary_chunks(results.range3d.iter_chunks())``.

    Parameters
    ----------
    chunks : iterable of :class:`pandas.DataFrame`
        each needs columns ``depth_A``, ``y_A`` and ``z_A`` [Ang]; chunks may be empty, and
        no chunks at all gives the empty summary

    Returns
    -------
    :obj:`dict`
        the keys, types and values of :func:`implant_summary` (``n_ions`` is an ``int``).
        Equal to it to near machine precision, however the rows are split.
    """
    depth = _Stream()
    half_lateral = _Stream()
    radial = _Stream()
    yz = _Stream()
    for df in chunks:
        y = np.asarray(df["y_A"], dtype=float)
        z = np.asarray(df["z_A"], dtype=float)
        depth.add(np.asarray(df["depth_A"], dtype=float))
        half_lateral.add((np.abs(y) + np.abs(z)) / 2)
        radial.add(np.hypot(y, z))
        yz.add(np.concatenate([y, z]))
    d = depth.moments
    n = d.n
    if n == 0:
        return _empty_summary()
    nan = float("nan")
    sd = math.sqrt(d.m2 / n)  # population form, as depth.std() in implant_summary
    return {
        "n_ions": n,
        "mean_depth_A": depth.mean,
        "straggle_depth_A": math.sqrt(d.m2 / (n - 1)) if n > 1 else nan,
        "lateral_range_A": half_lateral.mean,
        "lateral_straggle_A": math.sqrt(yz.moments.m2 / (yz.n - 1)) if yz.n > 1 else nan,
        "radial_range_A": radial.mean,
        "radial_straggle_A": math.sqrt(radial.moments.m2 / (n - 1)) if n > 1 else nan,
        "skewness": (d.m3 / n) / sd**3 if sd > 0 else nan,
        "kurtosis": (d.m4 / n) / sd**4 if sd > 0 else nan,
    }
