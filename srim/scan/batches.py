"""Split a calculation into batches with recorded seeds and merge the results.

``run_batches(spec, n_ions=10000, batch_ions=1000, seed=1)`` runs ten TRIM calculations
of 1000 ions, each with its own deterministic seed, and returns a :class:`MergedResults`
that behaves like :class:`srim.output.Results` for the table extractors
(``to_dataframes()``, ``.range3d.df`` ...).
"""

import hashlib
import math
import random
import shutil
import tempfile
from concurrent.futures import (
    FIRST_EXCEPTION,
    ProcessPoolExecutor,
    ThreadPoolExecutor,
    wait,
)
from dataclasses import dataclass, field

import numpy as np
import pandas as pd

#: largest seed handed to SRIM (a signed 32-bit integer; pysrim2 itself accepts any
#: non-negative integer, but SRIM's own limit is undocumented, so this stays conservative)
MAX_SEED = 2**31 - 1

_PER_ION = ("range3d", "exyz", "backscat", "transmit", "sputter")
_PROFILES = ("ioniz", "vacancy", "novac", "e2recoil", "phonon", "range")
#: merged table name -> attribute of :class:`srim.output.Results`
_RESULT_ATTRIBUTE = {"e2recoil": "etorecoils", "phonon": "phonons"}


def derive_seed(base_seed, batch_index):
    """Seed of batch ``batch_index`` for the base seed ``base_seed``.

    ``int(SHA-256(f"{base_seed}:{batch_index}"))`` of the first 8 bytes, modulo
    ``2**31 - 1``, plus one: a value in ``1 .. 2**31 - 1``. It only uses integer
    arithmetic and a fixed hash, so it is the same on every Python version and platform.
    Batch 0 does *not* reuse ``base_seed``: all batches are derived the same way, which
    makes them independent of the number of batches.
    """
    if int(base_seed) < 0 or int(batch_index) < 0:
        raise ValueError("base_seed and batch_index must be non-negative")
    digest = hashlib.sha256(f"{int(base_seed)}:{int(batch_index)}".encode()).digest()
    return int.from_bytes(digest[:8], "big") % MAX_SEED + 1


@dataclass
class BatchInfo:
    """One batch: its ``index``, ``seed``, number of ions and ``summary``
    (:func:`srim.analysis.implant_summary` of its ``RANGE_3D.txt``; ``None`` without).

    ``columns`` are extra columns of the merged per-ion tables, put right after ``batch`` in
    the order given (a binned beam's unit has its beam batch, bin and bin value)."""

    index: int
    seed: int
    n_ions: int
    summary: dict | None = None
    directory: str | None = field(default=None, repr=False)
    columns: dict | None = field(default=None, repr=False)


def _check_same_bins(frames, name):
    first = frames[0]
    for frame in frames[1:]:
        if list(frame.columns) != list(first.columns) or len(frame) != len(first):
            raise ValueError(f"cannot merge {name!r}: the batches have different columns or bins")
        if not np.allclose(frame["depth_A"], first["depth_A"], rtol=1e-9, atol=0.0):
            raise ValueError(f"cannot merge {name!r}: the batches have different depth bins")


class _MergedTable:
    """Merged output with the ``.df`` / ``.metadata`` interface of the single-run outputs."""

    def __init__(self, merged, name, kind):
        self._merged = merged
        self._name = name
        self._kind = kind
        self._df = None

    def _parts(self):
        return [getattr(p, self._name) for p in self._merged._parts]

    @property
    def df(self):
        """Merged :class:`pandas.DataFrame` (built on first access, then cached)"""
        if self._df is None:
            self._df = self._build()
        return self._df

    def _build(self):
        parts = self._parts()
        frames = [p.df for p in parts]
        if self._kind == "profile":
            _check_same_bins(frames, self._name)
            weights = np.array([b.n_ions for b in self._merged.batches], dtype=float)
            weights /= weights.sum()
            result = frames[0].copy()
            for column in result.columns:
                if column != "depth_A":
                    result[column] = sum(
                        w * f[column].to_numpy() for w, f in zip(weights, frames, strict=True)
                    )
            return result
        offset = 0
        out = []
        for info, frame in zip(self._merged.batches, frames, strict=True):
            frame = frame.copy()
            frame["ion"] = frame["ion"] + offset
            frame.insert(1, "batch", info.index)
            for position, (name, value) in enumerate((info.columns or {}).items(), start=2):
                frame.insert(position, name, value)
            offset += info.n_ions
            out.append(frame)
        return pd.concat(out, ignore_index=True)

    @property
    def metadata(self):
        """Metadata of the first batch with the total ``num_ions``, all ``seeds`` and
        the ``source`` of every batch"""
        parts = self._parts()
        meta = dict(parts[0].metadata)
        meta["num_ions"] = self._merged.n_ions
        meta["seeds"] = self._merged.seeds
        meta["source"] = [p.metadata.get("source") for p in parts]
        return meta

    @property
    def num_ions(self):
        return self._merged.n_ions


class _MergedCollision:
    """``collision`` of all batches, streamed: events are read batch after batch."""

    def __init__(self, merged):
        self._merged = merged

    def iter_events(self):
        """Events of every batch in order; ``ion`` is the global ion number and
        ``batch`` the batch index."""
        offset = 0
        for info, part in zip(self._merged.batches, self._merged._parts, strict=True):
            for event in part.collision.iter_events():
                event = dict(event)
                event["ion"] += offset
                event["batch"] = info.index
                event.update(info.columns or {})
                yield event
            offset += info.n_ions

    @property
    def df(self):
        """All events as one DataFrame (loads everything; prefer :meth:`iter_events`)"""
        return pd.DataFrame(list(self.iter_events()))

    @property
    def metadata(self):
        return {
            "source": [p.collision.metadata.get("source") for p in self._merged._parts],
            "seeds": self._merged.seeds,
            "num_ions": self._merged.n_ions,
        }


class MergedResults:
    """Results of :func:`run_batches`, with the surface of :class:`srim.output.Results`.

    Attributes ``ioniz``, ``vacancy``, ``novac``, ``e2recoil``, ``phonon``, ``range``
    (depth profiles: ion-count-weighted means of the batches, which must have identical
    depth bins) and ``range3d``, ``exyz``, ``backscat``, ``transmit``, ``sputter``
    (per-ion tables: concatenated; ``ion`` is the global ion number, ``batch`` the batch
    index) each have ``.df`` and ``.metadata``; an output that is absent is ``None``.
    ``collision`` is streamed over the batches (``collision.iter_events()``).
    ``etorecoils`` and ``phonons`` are aliases of ``e2recoil`` and ``phonon``.

    ``batches`` lists the :class:`BatchInfo`, ``seeds`` the seeds, ``spec`` the run spec
    (dict) with the total ion count and the base seed. Use ``close()`` or ``with`` to
    release the underlying results (and delete their run directories).
    """

    def __init__(
        self,
        parts,
        batches,
        spec=None,
        base_seed=None,
        keep_per_ion=True,
        tmpdir=None,
        beam_bins=None,
    ):
        self._parts = list(parts)
        self.batches = list(batches)
        self.spec = spec
        self.base_seed = base_seed
        #: the bins of a binned beam (:func:`srim.beam.binned.bins_frame`), None otherwise
        self.beam_bins = beam_bins
        self._tmpdir = tmpdir
        self._tables = {}
        for name in _PROFILES + _PER_ION:
            if name in _PER_ION and not keep_per_ion:
                continue
            attribute = _RESULT_ATTRIBUTE.get(name, name)
            if all(getattr(p, attribute, None) is not None for p in self._parts):
                kind = "profile" if name in _PROFILES else "per_ion"
                self._tables[name] = _MergedTable(self, attribute, kind)
        if keep_per_ion and all(getattr(p, "collision", None) is not None for p in self._parts):
            self._tables["collision"] = _MergedCollision(self)

    def __getattr__(self, name):
        tables = self.__dict__.get("_tables")
        if tables is not None:
            if name in _RESULT_ATTRIBUTE.values():
                name = next(k for k, v in _RESULT_ATTRIBUTE.items() if v == name)
            if name in tables:
                return tables[name]
            if name in _PROFILES + _PER_ION + ("collision",):
                return None
        raise AttributeError(name)

    @property
    def seeds(self):
        """Seed of every batch, in order"""
        return [b.seed for b in self.batches]

    @property
    def n_ions(self):
        """Total number of ions"""
        return sum(b.n_ions for b in self.batches)

    @property
    def metadata(self):
        return {
            "n_ions": self.n_ions,
            "base_seed": self.base_seed,
            "seeds": self.seeds,
            "batch_ions": [b.n_ions for b in self.batches],
        }

    def to_dataframes(self, include=None):
        """Tables as DataFrames; same selection rules as
        :meth:`srim.output.Results.to_dataframes`"""
        from ..export import _select

        return {name: out.df for name, out in _select(self, include).items()}

    def to_parquet(self, directory, spec=None, include=None, chunk_rows=100_000):
        """As :meth:`srim.output.Results.to_parquet`"""
        from ..export import results_to_parquet

        return results_to_parquet(
            self, directory, self.spec if spec is None else spec, include, chunk_rows
        )

    def to_hdf5(self, path, spec=None, include=None, chunk_rows=100_000):
        """As :meth:`srim.output.Results.to_hdf5`"""
        from ..export import results_to_hdf5

        return results_to_hdf5(self, path, self.spec if spec is None else spec, include, chunk_rows)

    def to_csv(self, directory, spec=None, include=None, chunk_rows=100_000):
        """As :meth:`srim.output.Results.to_csv`"""
        from ..export import results_to_csv

        return results_to_csv(
            self, directory, self.spec if spec is None else spec, include, chunk_rows
        )

    def close(self):
        """Close every underlying result (deleting their run directories)"""
        parts, self._parts = self._parts, []
        for part in parts:
            close = getattr(part, "close", None)
            if close is not None:
                close()
        tmpdir, self._tmpdir = self._tmpdir, None
        if tmpdir is not None:
            shutil.rmtree(tmpdir, ignore_errors=True)

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()
        return False


def _default_runner(spec):
    from ..srim import TRIM

    return TRIM.from_spec(spec).run()


def _run_to_directory(spec_dict, directory, runner=None):
    """Worker of the process pool: run, leave the outputs in ``directory``.

    Results objects cannot cross process boundaries, so the parent re-opens the directory.
    A custom ``runner`` (must be picklable) returns a ``Results``; its directory is copied.
    """
    from ..spec import RunSpec

    spec = RunSpec.from_dict(spec_dict)
    if runner is None:
        from ..srim import TRIM

        TRIM.from_spec(spec).run(output_dir=directory).close()
    else:
        results = runner(spec)
        try:
            shutil.copytree(results.directory, directory)
        finally:
            results.close()
    return directory


def run_batches(
    spec,
    n_ions,
    batch_ions,
    seed=None,
    *,
    runner=None,
    parallel=None,
    keep_per_ion=True,
    pool=None,
):
    """Run ``n_ions`` ions as batches of ``batch_ions`` ions and merge the results.

    Parameters
    ----------
    spec : :class:`srim.spec.RunSpec`
        the calculation; its ``number_ions`` and ``seed`` are replaced per batch
    n_ions, batch_ions : :obj:`int`
        total ions and ions per batch; there are ``ceil(n_ions / batch_ions)`` batches,
        the last one smaller if needed
    seed : :obj:`int`, optional
        base seed; batch ``i`` runs with ``derive_seed(seed, i)``. Default: a random
        base seed (recorded in the result)
    runner : callable, optional
        ``runner(spec) -> Results`` (anything with the output attributes and ``close()``).
        Default: ``TRIM.from_spec(spec).run()``; for tests or custom back ends. With
        ``parallel > 1`` it is called from several threads and must be thread-safe.
    parallel : :obj:`int`, optional
        number of batches running at once. The default runner uses a process pool, a
        custom ``runner`` a thread pool. Default: one after the other. When the first
        batch fails, batches that have not started are cancelled; running ones finish.
    keep_per_ion : :obj:`bool`
        if False the per-ion tables (``range3d``, ``exyz`` ...) and ``collision`` are not
        provided by the result (``None``); the batch summaries are still computed.
    pool : ``"thread"`` or ``"process"``, optional
        pool type for ``parallel > 1`` (default ``"process"`` without ``runner``, else
        ``"thread"``). A process pool needs a picklable ``runner`` (whose results have a
        ``directory`` that is copied) and, on macOS, Windows and Python 3.14 (spawn or
        forkserver), the calling script must be guarded by ``if __name__ == "__main__":``.

    Returns
    -------
    :class:`MergedResults`
        its tables' ``metadata["num_ions"]`` is the *requested* total, whereas the tables'
        own unique-ion counts can differ slightly (SRIM may omit a few ions).

    Raises
    ------
    Exception
        the first failing batch's exception, with a note naming the batch and its seed.
    """
    n_ions, batch_ions = int(n_ions), int(batch_ions)
    if n_ions < 1 or batch_ions < 1:
        raise ValueError("n_ions and batch_ions must be positive")
    if parallel is not None and int(parallel) < 1:
        raise ValueError("parallel must be positive")
    if pool not in (None, "thread", "process"):
        raise ValueError("pool must be 'thread' or 'process'")
    base = random.randrange(0, MAX_SEED) if seed is None else int(seed)
    count = math.ceil(n_ions / batch_ions)
    sizes = [min(batch_ions, n_ions - i * batch_ions) for i in range(count)]
    seeds = [derive_seed(base, i) for i in range(count)]
    specs = [
        spec.with_overrides([f"number_ions={n}", f"seed={s}"])
        for n, s in zip(sizes, seeds, strict=True)
    ]
    workers = min(int(parallel or 1), count)
    pool = pool or ("process" if runner is None else "thread")

    def annotate(exc, i):
        exc.add_note(f"batch {i} of {count}, seed {seeds[i]}, ions {sizes[i]}")

    tmpdir = None
    results = []
    try:
        if workers <= 1:
            run = runner or _default_runner
            for i, one in enumerate(specs):
                try:
                    results.append(run(one))
                except BaseException as exc:
                    annotate(exc, i)
                    raise
        elif pool == "thread":
            with ThreadPoolExecutor(workers) as executor:
                futures = [executor.submit(runner or _default_runner, one) for one in specs]
                results.extend(_collect(executor, futures, annotate, lambda r: r))
        else:
            from ..output import Results

            tmpdir = tempfile.mkdtemp(prefix="pysrim2-batches-")
            dirs = [f"{tmpdir}/batch-{i}" for i in range(count)]
            with ProcessPoolExecutor(workers) as executor:
                futures = [
                    executor.submit(_run_to_directory, one.to_dict(inline=True), d, runner)
                    for one, d in zip(specs, dirs, strict=True)
                ]
                results.extend(_collect(executor, futures, annotate, Results))
        total = spec.with_overrides([f"number_ions={n_ions}", f"seed={base}"])
        batches = [
            BatchInfo(i, s, n, _summary(r))
            for i, (s, n, r) in enumerate(zip(seeds, sizes, results, strict=True))
        ]
        return MergedResults(results, batches, total.to_dict(), base, keep_per_ion, tmpdir)
    except BaseException:
        for r in results:
            close = getattr(r, "close", None)
            if close is not None:
                close()
        if tmpdir is not None:
            shutil.rmtree(tmpdir, ignore_errors=True)
        raise


def _collect(executor, futures, annotate, convert):
    """Results in submission order. On the first failure the batches that have not
    started are cancelled, running ones are awaited (and their results closed), and the
    failure is raised with a note naming its batch."""
    wait(futures, return_when=FIRST_EXCEPTION)
    failed = next((i for i, f in enumerate(futures) if f.done() and f.exception()), None)
    if failed is None:
        return [convert(f.result()) for f in futures]
    executor.shutdown(wait=True, cancel_futures=True)
    for f in futures:
        if f.done() and not f.cancelled() and f.exception() is None:
            close = getattr(convert(f.result()), "close", None)
            if close is not None:
                close()
    exc = futures[failed].exception()
    annotate(exc, failed)
    raise exc


def _summary(results):
    range3d = getattr(results, "range3d", None)
    if range3d is None:
        return None
    from ..analysis import implant_summary

    return implant_summary(range3d.df)
