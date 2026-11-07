"""Probability shapes for beam parameters (energy, angles, position, depth).

Every shape describes a distribution of one number. Continuous shapes are tabulated on
:data:`GRID_POINTS` points of their support; the sampled distribution is the piecewise-linear
CDF through the nodes of :meth:`Shape.nodes`, so sampling, probabilities and means all use the
same numbers. Shapes with piecewise-constant densities (``Uniform``, ``Histogram``) use their
own edges as nodes and are exact.

A YAML distribution is a one-key mapping, ``{gaussian: {mean: 100, sigma: 5}}``; see
:func:`shape_from_dict`. Every shape also takes an optional ``range`` (truncation).
"""

import copy
import inspect
import math
import os
from collections.abc import Callable, Mapping
from pathlib import Path
from typing import Any, ClassVar

import numpy as np

from ..scan.extract import PYTHON_PREFIX, _import_reference, reference
from ..spec import SpecError
from .histfile import edges_from_centres, read_histogram

#: number of tabulation points of a continuous shape's support
GRID_POINTS = 4097

#: default support of a Gaussian or log-normal: mean ± this many sigma (log-normal: of ln x)
SIGMA_SPAN = 5.0


def _source_of(fn: Callable) -> str:
    """Source text of a Python distribution function (its reference when no source exists)."""
    try:
        return inspect.getsource(fn)
    except (OSError, TypeError):
        try:
            return reference(fn)
        except ValueError:
            return repr(fn)


def _bound(value: Any, name: str) -> float:
    """A real number (infinities allowed, NaN not) as a float."""
    if isinstance(value, bool) or not isinstance(value, (int, float, np.integer, np.floating)):
        raise SpecError(f"{name} must be a number, got {value!r}")
    value = float(value)
    if math.isnan(value):
        raise SpecError(f"{name} must not be NaN")
    return value


def _number(value: Any, name: str) -> float:
    """A finite number as a float."""
    value = _bound(value, name)
    if not math.isfinite(value):
        raise SpecError(f"{name} must be finite, got {value!r}")
    return value


def _range_arg(value: Any) -> tuple[float, float] | None:
    if value is None:
        return None
    if isinstance(value, (str, bytes)) or not isinstance(value, (list, tuple)) or len(value) != 2:
        raise SpecError(f"range must be [low, high], got {value!r}")
    lo = _bound(value[0], "range low")
    hi = _bound(value[1], "range high")
    if hi < lo:
        raise SpecError(f"range must have low <= high, got [{lo}, {hi}]")
    return lo, hi


def _array(values: Any, name: str) -> np.ndarray:
    """A one-dimensional array of finite numbers (a copy, so later changes to the input
    do not change the shape)."""
    try:
        arr = np.array(values, dtype=float)
    except (TypeError, ValueError):
        raise SpecError(f"{name} must be a list of numbers, got {values!r}") from None
    if arr.ndim != 1 or not np.all(np.isfinite(arr)):
        raise SpecError(f"{name} must be a flat list of finite numbers, got {values!r}")
    return arr


def _shaped(out: np.ndarray) -> Any:
    """A 0-d result as a float, arrays unchanged."""
    return float(out) if out.ndim == 0 else out


class Shape:
    """Base of the distribution shapes; subclasses set :attr:`kind` and implement the hooks.

    Attributes
    ----------
    kind : :obj:`str`
        the YAML key of the shape
    range : tuple of float, or None
        user truncation ``(low, high)``; the support is the default support intersected with it
    """

    kind: ClassVar[str] = ""
    #: YAML parameters of the shape (``range`` is always accepted as well)
    _fields: ClassVar[tuple[str, ...]] = ()
    #: parameters that must be given in YAML
    _required: ClassVar[tuple[str, ...]] = ()

    def __init__(self, range: Any = None):
        self.range = _range_arg(range)
        self._nodes_cache: tuple[np.ndarray, np.ndarray] | None = None

    # --- hooks for subclasses -------------------------------------------------------------

    def _default_support(self) -> tuple[float, float]:
        raise NotImplementedError

    def _params_dict(self, inline: bool) -> dict[str, Any]:
        raise NotImplementedError

    def _raw_pdf(self, x: np.ndarray) -> np.ndarray:
        """Unnormalised density on the grid ``x`` (continuous shapes)."""
        raise NotImplementedError

    def _exact_pieces(self) -> tuple[np.ndarray, np.ndarray] | None:
        """``(edges, masses)`` of a piecewise-constant density, or None for a smooth one."""
        return None

    def _grid(self, lo: float, hi: float) -> np.ndarray:
        return np.linspace(lo, hi, GRID_POINTS)

    def _label(self) -> str:
        return f"{self.kind} distribution"

    # --- support and nodes ----------------------------------------------------------------

    def support(self) -> tuple[float, float]:
        """The finite support ``(low, high)``: the default support intersected with ``range``.

        Raises
        ------
        SpecError
            if the range leaves no probability
        """
        dlo, dhi = self._default_support()
        lo, hi = dlo, dhi
        if self.range is not None:
            lo = max(lo, self.range[0])
            hi = min(hi, self.range[1])
        if hi < lo:
            raise SpecError(
                f"no probability in [{self.range[0]}, {self.range[1]}] "
                f"(the distribution covers [{dlo}, {dhi}])"
            )
        return float(lo), float(hi)

    def degenerate(self) -> float | None:
        """The value of a zero-width shape, else None."""
        lo, hi = self.support()
        return lo if lo == hi else None

    def nodes(self) -> tuple[np.ndarray, np.ndarray]:
        """``(x, F)``: increasing ``x`` and the CDF ``F`` from 0 to 1 at those points.

        The sampled distribution is the piecewise-linear CDF through these nodes. A zero-width
        shape gives the step ``x = [v, v]``, ``F = [0, 1]``.
        """
        if self._nodes_cache is None:
            lo, hi = self.support()
            if lo == hi:
                x = np.array([lo, lo])
                F = np.array([0.0, 1.0])
            else:
                pieces = self._exact_pieces()
                if pieces is None:
                    x = self._grid(lo, hi)
                    p = np.asarray(self._raw_pdf(x), dtype=float)
                    self._check_density(p)
                    area = 0.5 * (p[1:] + p[:-1]) * np.diff(x)
                    F = np.concatenate([[0.0], np.cumsum(area)])
                    if not F[-1] > 0:
                        raise SpecError(f"{self._label()}: no probability in [{lo}, {hi}]")
                    F = F / F[-1]
                else:
                    x, F = self._piecewise_nodes(*pieces, lo, hi)
            F[-1] = 1.0
            x.flags.writeable = False
            F.flags.writeable = False
            self._nodes_cache = (x, F)
        return self._nodes_cache

    def _check_density(self, p: np.ndarray) -> None:
        if not np.all(np.isfinite(p)) or np.any(p < 0):
            raise SpecError(f"{self._label()}: the density must be finite and not negative")

    def _piecewise_nodes(
        self, edges: np.ndarray, masses: np.ndarray, lo: float, hi: float
    ) -> tuple[np.ndarray, np.ndarray]:
        """Exact nodes of a piecewise-constant density with bin ``masses`` on ``edges``."""
        cum = np.concatenate([[0.0], np.cumsum(masses)])
        inner = edges[(edges > lo) & (edges < hi)]
        x = np.concatenate([[lo], inner, [hi]])
        at = np.interp(x, edges, cum)
        total = at[-1] - at[0]
        if not total > 0:
            raise SpecError(f"{self._label()}: no probability in [{lo}, {hi}]")
        return x, (at - at[0]) / total

    # --- queries --------------------------------------------------------------------------

    def cdf(self, x: Any) -> Any:
        """P(X <= x)."""
        x = np.asarray(x, dtype=float)
        lo, hi = self.support()
        if lo == hi:
            return _shaped((x >= lo).astype(float))
        xs, F = self.nodes()
        return _shaped(np.interp(x, xs, F))

    def pdf(self, x: Any) -> Any:
        """Density of the piecewise-linear CDF of :meth:`nodes` (zero outside the support).

        A zero-width shape has no density and gives zeros.
        """
        x = np.asarray(x, dtype=float)
        lo, hi = self.support()
        if lo == hi:
            return _shaped(np.zeros_like(x))
        xs, F = self.nodes()
        dens = np.diff(F) / np.diff(xs)
        idx = np.clip(np.searchsorted(xs, x, side="right") - 1, 0, len(dens) - 1)
        inside = (x >= xs[0]) & (x < xs[-1])
        return _shaped(np.where(inside, dens[idx], 0.0))

    def sample(self, rng: np.random.Generator, n: int) -> np.ndarray:
        """``n`` values drawn with ``rng`` (inverse CDF of :meth:`nodes`)."""
        n = int(n)
        lo, hi = self.support()
        if lo == hi:
            return np.full(n, lo)
        xs, F = self.nodes()
        return np.interp(rng.random(n), F, xs)

    def probabilities(self, edges: Any) -> np.ndarray:
        """Probability of each bin between consecutive ``edges``."""
        return np.diff(np.asarray(self.cdf(np.asarray(edges, dtype=float)), dtype=float))

    def interval_mean(self, low: float, high: float) -> float:
        """Mean of the distribution restricted to ``[low, high]`` (exact for :meth:`nodes`).

        Raises
        ------
        ValueError
            if no probability lies in ``[low, high]``
        """
        low = float(low)
        high = float(high)
        lo, hi = self.support()
        if lo == hi:
            if low <= lo <= high:
                return lo
            raise ValueError(f"no probability in [{low}, {high}]")
        xs, F = self.nodes()
        a = np.maximum(xs[:-1], low)
        b = np.minimum(xs[1:], high)
        mass = np.diff(F) / np.diff(xs) * np.clip(b - a, 0.0, None)
        total = mass.sum()
        if not total > 0:
            raise ValueError(f"no probability in [{low}, {high}]")
        return float(np.sum(mass * (a + b) / 2.0) / total)

    def mean(self) -> float:
        """Mean of the (truncated) distribution."""
        lo, hi = self.support()
        return lo if lo == hi else self.interval_mean(lo, hi)

    def equal_probability_edges(self, n: int) -> np.ndarray:
        """``n + 1`` edges that cut the distribution into ``n`` bins of equal probability."""
        n = int(n)
        if n < 1:
            raise ValueError(f"need at least one bin, got {n}")
        lo, hi = self.support()
        if lo == hi:
            return np.full(n + 1, lo)
        xs, F = self.nodes()
        edges = np.interp(np.linspace(0.0, 1.0, n + 1), F, xs)
        edges[0] = lo
        edges[-1] = hi
        return edges

    def clip(self, low: float, high: float, key: str) -> "Shape":
        """This shape truncated to ``[low, high]`` (a physical domain, e.g. energy >= 0).

        Raises
        ------
        SpecError
            naming ``key`` if no probability is left
        """
        low = _bound(low, key)
        high = _bound(high, key)
        message = f"{key}: no probability in [{low}, {high}]"
        if high < low:
            raise SpecError(message)
        cur_lo, cur_hi = self.range if self.range is not None else (-math.inf, math.inf)
        new = copy.copy(self)
        new.range = (max(cur_lo, low), min(cur_hi, high))
        new._nodes_cache = None
        try:
            new.support()
        except SpecError:
            raise SpecError(message) from None
        return new

    # --- serialisation --------------------------------------------------------------------

    def to_dict(self, inline: bool = False) -> dict[str, Any]:
        """``{kind: {params..., "range": [lo, hi]}}``; ``inline`` adds content that is not
        otherwise in the dict (histogram data, function source) for hashing.
        """
        params = dict(self._params_dict(inline))
        if self.range is not None:
            params["range"] = [float(self.range[0]), float(self.range[1])]
        return {self.kind: params}

    @classmethod
    def _from_params(cls, params: Mapping[str, Any], base_dir: Any = None) -> "Shape":
        return cls(**dict(params))


class Fixed(Shape):
    """A single value (a degenerate shape; :func:`shape_from_dict` returns it as a float)."""

    kind: ClassVar[str] = "fixed"
    _fields: ClassVar[tuple[str, ...]] = ("value",)
    _required: ClassVar[tuple[str, ...]] = ("value",)

    def __init__(self, value: Any, range: Any = None):
        self.value = _number(value, "value")
        super().__init__(range)

    def _default_support(self):
        return self.value, self.value

    def _params_dict(self, inline):
        return {"value": self.value}


class Gaussian(Shape):
    """Normal distribution with ``mean`` and ``sigma`` (default support mean ± 5 sigma)."""

    kind: ClassVar[str] = "gaussian"
    _fields: ClassVar[tuple[str, ...]] = ("mean", "sigma")
    _required: ClassVar[tuple[str, ...]] = ("mean", "sigma")

    def __init__(self, mean: Any, sigma: Any, range: Any = None):
        # not "self.mean": that name is the mean() method
        self._centre = _number(mean, "mean")
        self._width = _number(sigma, "sigma")
        if self._width < 0:
            raise SpecError(f"sigma must not be negative, got {self._width}")
        super().__init__(range)

    def _default_support(self):
        return self._centre - SIGMA_SPAN * self._width, self._centre + SIGMA_SPAN * self._width

    def _raw_pdf(self, x):
        z = (x - self._centre) / self._width
        return np.exp(-0.5 * z * z)

    def _params_dict(self, inline):
        return {"mean": self._centre, "sigma": self._width}


class Uniform(Shape):
    """Uniform distribution on ``[low, high]``."""

    kind: ClassVar[str] = "uniform"
    _fields: ClassVar[tuple[str, ...]] = ("low", "high")
    _required: ClassVar[tuple[str, ...]] = ("low", "high")

    def __init__(self, low: Any, high: Any, range: Any = None):
        self.low = _number(low, "low")
        self.high = _number(high, "high")
        if self.high < self.low:
            raise SpecError(f"high must not be below low, got [{self.low}, {self.high}]")
        super().__init__(range)

    def _default_support(self):
        return self.low, self.high

    def _exact_pieces(self):
        return np.array([self.low, self.high]), np.array([1.0])

    def _params_dict(self, inline):
        return {"low": self.low, "high": self.high}


class Triangular(Shape):
    """Triangular distribution with ``low``, ``mode`` and ``high``."""

    kind: ClassVar[str] = "triangular"
    _fields: ClassVar[tuple[str, ...]] = ("low", "mode", "high")
    _required: ClassVar[tuple[str, ...]] = ("low", "mode", "high")

    def __init__(self, low: Any, mode: Any, high: Any, range: Any = None):
        self.low = _number(low, "low")
        self.mode = _number(mode, "mode")
        self.high = _number(high, "high")
        if not self.low <= self.mode <= self.high:
            raise SpecError(f"need low <= mode <= high, got {self.low}, {self.mode}, {self.high}")
        super().__init__(range)

    def _default_support(self):
        return self.low, self.high

    def _raw_pdf(self, x):
        a, b, c = self.low, self.mode, self.high
        p = np.zeros_like(x, dtype=float)
        if b > a:
            m = (x >= a) & (x <= b)
            p[m] = 2.0 * (x[m] - a) / ((c - a) * (b - a))
        if c > b:
            m = (x > b) & (x <= c)
            p[m] = 2.0 * (c - x[m]) / ((c - a) * (c - b))
        return p

    def _params_dict(self, inline):
        return {"low": self.low, "mode": self.mode, "high": self.high}


class LogNormal(Shape):
    """Log-normal distribution: ``ln x`` is normal with ``mu`` and ``sigma``.

    The default support is ``exp(mu ± 5 sigma)``, tabulated on a logarithmic grid.
    """

    kind: ClassVar[str] = "lognormal"
    _fields: ClassVar[tuple[str, ...]] = ("mu", "sigma")
    _required: ClassVar[tuple[str, ...]] = ("mu", "sigma")

    def __init__(self, mu: Any, sigma: Any, range: Any = None):
        self.mu = _number(mu, "mu")
        self.sigma = _number(sigma, "sigma")
        if self.sigma < 0:
            raise SpecError(f"sigma must not be negative, got {self.sigma}")
        super().__init__(range)

    def _default_support(self):
        with np.errstate(over="ignore"):
            lo, hi = (
                np.exp(self.mu - SIGMA_SPAN * self.sigma),
                np.exp(self.mu + SIGMA_SPAN * self.sigma),
            )
        if not (np.isfinite(lo) and np.isfinite(hi)):
            raise SpecError(f"lognormal mu {self.mu} with sigma {self.sigma} overflows")
        return float(lo), float(hi)

    def _grid(self, lo, hi):
        return np.geomspace(lo, hi, GRID_POINTS)

    def _raw_pdf(self, x):
        z = (np.log(x) - self.mu) / self.sigma
        return np.exp(-0.5 * z * z) / x

    def _params_dict(self, inline):
        return {"mu": self.mu, "sigma": self.sigma}


class Histogram(Shape):
    """Piecewise-constant density: bin ``i`` spans ``edges[i]`` to ``edges[i+1]``.

    ``counts`` are weights (they need not be normalised); the density in a bin is its share of
    the total divided by the bin width. ``file`` is the path a histogram was read from (see
    :meth:`from_file`); it is None for an inline histogram.

    YAML takes exactly one of ``file``; ``edges`` with ``counts``; or ``centres`` with
    ``counts`` (see :func:`shape_from_dict`).
    """

    kind: ClassVar[str] = "histogram"
    _fields: ClassVar[tuple[str, ...]] = ("file", "edges", "counts", "centres")
    _required: ClassVar[tuple[str, ...]] = ()

    def __init__(self, edges: Any, counts: Any, file: Any = None, range: Any = None):
        e = _array(edges, "edges")
        c = _array(counts, "counts")
        if len(e) < 2:
            raise SpecError("a histogram needs at least two edges")
        if len(c) != len(e) - 1:
            raise SpecError(f"a histogram needs one count per bin: {len(e)} edges, {len(c)} counts")
        if np.any(np.diff(e) <= 0):
            raise SpecError("histogram edges must be strictly increasing")
        if np.any(c < 0) or not c.sum() > 0:
            raise SpecError("histogram counts must not be negative and must have a positive sum")
        self._edge_list = [float(v) for v in e]
        self._count_list = [float(v) for v in c]
        self._edges = e
        self._masses = c / c.sum()
        self._edges.flags.writeable = False
        self._masses.flags.writeable = False
        self.file = file
        super().__init__(range)

    @property
    def edges(self) -> list[float]:
        """The bin edges (a copy; the sampled distribution is fixed at construction)."""
        return list(self._edge_list)

    @property
    def counts(self) -> list[float]:
        """The bin counts (a copy; the sampled distribution is fixed at construction)."""
        return list(self._count_list)

    @classmethod
    def from_centres(cls, centres: Any, counts: Any, file: Any = None, range: Any = None):
        """A histogram from bin centres: inner edges at the midpoints, outer edges half a
        neighbouring width beyond the first and last centre.
        """
        c = _array(centres, "centres")
        if len(c) < 2:
            raise SpecError("a histogram from centres needs at least two centres")
        if np.any(np.diff(c) <= 0):
            raise SpecError("histogram centres must be strictly increasing")
        return cls(edges_from_centres(c), counts, file=file, range=range)

    @classmethod
    def from_file(cls, path: Any, base_dir: Any = None, range: Any = None) -> "Histogram":
        """A histogram read from a text file (see :mod:`srim.beam.histfile` for the layout).

        A relative ``path`` is taken from ``base_dir`` (the working directory when None). The
        histogram's ``file`` is the absolute, resolved path.

        Raises
        ------
        SpecError
            naming the file, and the line for a bad row
        """
        if not isinstance(path, (str, os.PathLike)) or str(path) == "":
            raise SpecError(f"a histogram file must be a path, got {path!r}")
        resolved = Path(path)
        if not resolved.is_absolute() and base_dir is not None:
            resolved = Path(base_dir) / resolved
        resolved = resolved.resolve()
        edges, counts = read_histogram(resolved)
        return cls(edges, counts, file=str(resolved), range=range)

    @classmethod
    def _from_params(cls, params, base_dir=None):
        rng = params.get("range")
        given = [name for name in cls._fields if name in params]
        if "file" in params:
            if given != ["file"]:
                raise SpecError(_histogram_forms(given))
            return cls.from_file(params["file"], base_dir=base_dir, range=rng)
        if "edges" in params and "centres" not in params:
            if "counts" not in params:
                raise SpecError(_histogram_forms(given))
            return cls(params["edges"], params["counts"], range=rng)
        if "centres" in params and "edges" not in params:
            if "counts" not in params:
                raise SpecError(_histogram_forms(given))
            return cls.from_centres(params["centres"], params["counts"], range=rng)
        raise SpecError(_histogram_forms(given))

    def _default_support(self):
        return float(self._edges[0]), float(self._edges[-1])

    def _exact_pieces(self):
        return self._edges, self._masses

    def _params_dict(self, inline):
        if self.file is not None and not inline:
            return {"file": str(self.file)}
        return {"edges": self.edges, "counts": self.counts}


def _histogram_forms(given: list[str]) -> str:
    return (
        "a histogram needs exactly one of: file; edges with counts; centres with counts "
        f"(got {', '.join(given) if given else 'none of them'})"
    )


class Function(Shape):
    """A density given by a Python function ``f(x)`` (vectorised over numpy arrays).

    ``range`` is required and is the support. The density is tabulated on :data:`GRID_POINTS`
    points; it must be finite and not negative. ``fn_or_ref`` is either a module-level function
    (stored as ``python:<module>:<name>``) or such a reference string, imported when first used.
    """

    kind: ClassVar[str] = "function"
    _fields: ClassVar[tuple[str, ...]] = ("ref",)
    _required: ClassVar[tuple[str, ...]] = ("ref", "range")

    def __init__(self, fn_or_ref: Any, range: Any = None):
        if range is None:
            raise SpecError("a function distribution needs a range (its support)")
        super().__init__(range)
        if self.range is None or not all(math.isfinite(v) for v in self.range):
            raise SpecError("the range of a function distribution must be finite")
        self._fn: Callable | None = None
        if isinstance(fn_or_ref, str):
            if not fn_or_ref.startswith(PYTHON_PREFIX):
                raise SpecError(
                    f"a function distribution is a python:<module>:<name> reference, "
                    f"got {fn_or_ref!r}"
                )
            self.ref = fn_or_ref
        elif callable(fn_or_ref):
            try:
                self.ref = reference(fn_or_ref)
            except ValueError as exc:
                raise SpecError(str(exc)) from None
            self._fn = fn_or_ref
        else:
            raise SpecError("a function distribution needs a callable or a reference string")

    @classmethod
    def _from_params(cls, params, base_dir=None):
        return cls(params["ref"], params["range"])

    def _label(self):
        return self.ref

    def _resolve(self) -> Callable:
        if self._fn is None:
            try:
                self._fn = _import_reference(self.ref)
            except ValueError as exc:
                raise SpecError(str(exc)) from None
        return self._fn

    def _default_support(self):
        return self.range

    def _raw_pdf(self, x):
        fn = self._resolve()
        try:
            values = fn(x)
            return np.array(np.broadcast_to(np.asarray(values, dtype=float), x.shape))
        except (TypeError, ValueError) as exc:
            raise SpecError(
                f"{self.ref}: the density must accept a numpy array of the same shape ({exc})"
            ) from None

    def _params_dict(self, inline):
        params: dict[str, Any] = {"ref": self.ref}
        if inline:
            params["source"] = _source_of(self._resolve())
        return params


_KINDS: dict[str, type[Shape]] = {
    cls.kind: cls for cls in (Fixed, Gaussian, Uniform, Triangular, LogNormal, Histogram, Function)
}


def shape_from_dict(value: Any, key: str, base_dir: Any = None) -> float | Shape:
    """The distribution given by a YAML value.

    A number, or a mapping ``{fixed: {value: v}}``, gives a float. A one-key mapping
    ``{kind: {params...}}`` gives a :class:`Shape`; a zero-width shape gives its float.
    The shape is validated here (a function's density too), so a bad distribution fails when the
    spec is read. A relative histogram ``file`` is taken from ``base_dir`` (the working directory
    when None).

    Raises
    ------
    SpecError
        naming ``key`` for an unknown kind or parameter, a missing parameter or an invalid value
    """
    if not isinstance(value, Mapping) and not isinstance(value, bool):
        return _number(value, key)
    if not isinstance(value, Mapping):
        raise SpecError(f"{key}: expected a number or a distribution, got {value!r}")
    if len(value) != 1:
        raise SpecError(
            f"{key}: expected exactly one distribution such as "
            f"{{gaussian: {{mean: ..., sigma: ...}}}}, got keys {sorted(map(str, value))}"
        )
    ((kind, params),) = value.items()
    if kind not in _KINDS:
        raise SpecError(
            f"{key}: unknown distribution {kind!r}; allowed: {', '.join(sorted(_KINDS))}"
        )
    cls = _KINDS[kind]
    if params is None:
        params = {}
    if not isinstance(params, Mapping):
        raise SpecError(f"{key}.{kind}: expected a mapping of parameters, got {params!r}")
    allowed = cls._fields + ("range",)
    for name in params:
        if name not in allowed:
            raise SpecError(
                f"{key}.{kind}: unknown parameter {name!r}; allowed: {', '.join(allowed)}"
            )
    for name in cls._required:
        if name not in params:
            raise SpecError(f"{key}.{kind}: missing {name!r} (needs {', '.join(cls._required)})")
    try:
        shape = cls._from_params(params, base_dir)
        point = shape.degenerate()
        if point is not None:
            return point
        shape.nodes()
    except SpecError as exc:
        raise SpecError(f"{key}.{kind}: {exc}") from None
    except (TypeError, ValueError) as exc:
        raise SpecError(f"{key}.{kind}: {exc}") from None
    return shape
