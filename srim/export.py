"""Export SRIM results to Parquet, HDF5 and CSV, with metadata.

The public entry points are :meth:`srim.output.Results.to_parquet`,
:meth:`~srim.output.Results.to_hdf5`, :meth:`~srim.output.Results.to_csv` and
:meth:`srim.output.Collision.to_parquet` / :meth:`~srim.output.Collision.to_csv`; this
module holds the implementation.

Metadata
--------
Every export carries a JSON document (Parquet: schema key-value metadata under the key
``pysrim2``; HDF5: the node attribute ``pysrim2``; CSV: one ``metadata.yaml`` for all
tables) with the keys ``pysrim2_version``, ``exported_utc`` (ISO 8601, UTC), ``table``,
``metadata`` (the ``.metadata`` dict of the output) and ``spec`` (the run specification
if one is passed, else ``null``).

The ``collision`` table can have millions of rows, so it is only exported when asked for
(``include=["collision"]``) and is always written in chunks of ``chunk_rows`` events
streamed from :meth:`srim.output.Collision.iter_events`: memory stays proportional to
one chunk, not to the file. Its columns are fixed (the same in every chunk and file):
integer ``ion``, strings ``kind`` and ``atom``, and 64-bit floats for everything else;
fields that do not apply to an event kind (see :meth:`~srim.output.Collision.iter_events`)
are ``NaN`` (floats) and, in Parquet, null.
"""

import json
import os
from datetime import UTC, datetime
from importlib import import_module
from importlib import metadata as importlib_metadata

import pandas as pd
import yaml

from .output._chunks import iter_row_chunks
from .output.collision import _EVENT_DTYPES, EVENT_COLUMNS

#: table name -> attribute of :class:`srim.output.Results`
TABLE_ATTRIBUTES = {
    "ioniz": "ioniz",
    "vacancy": "vacancy",
    "novac": "novac",
    "e2recoil": "etorecoils",
    "phonon": "phonons",
    "range": "range",
    "range3d": "range3d",
    "exyz": "exyz",
    "backscat": "backscat",
    "transmit": "transmit",
    "sputter": "sputter",
    "collision": "collision",
}
#: tables that are only exported if named in ``include`` (they can be huge)
OPT_IN = ("collision",)
#: events per chunk when streaming ``COLLISON.txt``
DEFAULT_CHUNK_ROWS = 500_000
#: key of the metadata in Parquet files and HDF5 attributes
METADATA_KEY = "pysrim2"


def _require(module, extra):
    """Import ``module`` or raise an :class:`ImportError` naming the extra to install"""
    try:
        return import_module(module)
    except ImportError as exc:
        raise ImportError(
            f"{module.split('.')[0]} is required for this export; install pysrim2[{extra}] "
            f"(pip install 'pysrim2[{extra}]')"
        ) from exc


def _version():
    try:
        return importlib_metadata.version("pysrim2")
    except importlib_metadata.PackageNotFoundError:  # pragma: no cover
        return None


def _jsonable(value):
    """Convert ``value`` (numpy scalars, paths, ...) to plain JSON types"""
    return json.loads(json.dumps(value, default=_default))


def _default(obj):
    if hasattr(obj, "item"):  # numpy scalar
        return obj.item()
    if hasattr(obj, "tolist"):
        return obj.tolist()
    return str(obj)


def _document(name, metadata, spec, exported):
    return {
        "pysrim2_version": _version(),
        "exported_utc": exported,
        "table": name,
        "metadata": _jsonable(metadata),
        "spec": _jsonable(spec),
    }


def _now():
    return datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def _select(results, include):
    """Return ``{name: output object}`` for the tables to export, in table order"""
    if include is None:
        names = [n for n in TABLE_ATTRIBUTES if n not in OPT_IN]
        explicit = False
    else:
        names = [include] if isinstance(include, str) else list(include)
        unknown = [n for n in names if n not in TABLE_ATTRIBUTES]
        if unknown:
            raise ValueError(f"unknown table(s) {unknown}; choose from {list(TABLE_ATTRIBUTES)}")
        names = [n for n in TABLE_ATTRIBUTES if n in names]
        explicit = True
    selected = {}
    for name in names:
        output = getattr(results, TABLE_ATTRIBUTES[name])
        if output is not None:
            selected[name] = output
        elif explicit:
            raise ValueError(f"table {name!r} is not available in these results")
    return selected


def _metadata_of(output):
    return getattr(output, "metadata", None) or {}


def event_chunks(collision, chunk_rows=DEFAULT_CHUNK_ROWS):
    """Stream the events of ``collision`` as DataFrames of at most ``chunk_rows`` rows"""
    rows = ([event[c] for c in EVENT_COLUMNS] for event in collision.iter_events())
    return iter_row_chunks(rows, EVENT_COLUMNS, _EVENT_DTYPES, chunk_rows)


def _check_chunk_rows(chunk_rows):
    if int(chunk_rows) < 1:
        raise ValueError("chunk_rows must be a positive integer")
    return int(chunk_rows)


# ---------------------------------------------------------------- Parquet


def _event_schema(pa):
    fields = []
    for name, dtype in zip(EVENT_COLUMNS, _EVENT_DTYPES, strict=True):
        if dtype is int:
            kind = pa.int64()
        elif dtype is float:
            kind = pa.float64()
        else:
            kind = pa.string()
        fields.append(pa.field(name, kind))
    return pa.schema(fields)


def _parquet_table(pa, df, document):
    table = pa.Table.from_pandas(df, preserve_index=False)
    metadata = dict(table.schema.metadata or {})
    metadata[METADATA_KEY.encode()] = json.dumps(document).encode()
    return table.replace_schema_metadata(metadata)


def write_parquet(df, path, name, metadata=None, spec=None):
    """Write ``df`` to ``path`` with the pysrim2 metadata document in the schema"""
    pa = _require("pyarrow", "parquet")
    pq = _require("pyarrow.parquet", "parquet")
    document = _document(name, metadata, spec, _now())
    pq.write_table(_parquet_table(pa, df, document), os.fspath(path))


def collision_to_parquet(
    collision, path, chunk_rows=DEFAULT_CHUNK_ROWS, metadata=None, spec=None, name="collision"
):
    """Stream the events of ``collision`` into one Parquet file at ``path``

    One row group per chunk. Returns the number of rows written.
    """
    pa = _require("pyarrow", "parquet")
    pq = _require("pyarrow.parquet", "parquet")
    chunk_rows = _check_chunk_rows(chunk_rows)
    schema = _event_schema(pa)
    document = _document(name, metadata, spec, _now())
    schema = schema.with_metadata({METADATA_KEY: json.dumps(document)})
    total = 0
    with pq.ParquetWriter(os.fspath(path), schema) as writer:
        for chunk in event_chunks(collision, chunk_rows):
            writer.write_table(pa.Table.from_pandas(chunk, schema=schema, preserve_index=False))
            total += len(chunk)
    return total


def results_to_parquet(results, directory, spec=None, include=None, chunk_rows=DEFAULT_CHUNK_ROWS):
    _require("pyarrow.parquet", "parquet")
    selected = _select(results, include)
    os.makedirs(directory, exist_ok=True)
    written = {}
    for name, output in selected.items():
        path = os.path.join(directory, f"{name}.parquet")
        if name == "collision":
            collision_to_parquet(output, path, chunk_rows, _metadata_of(output), spec, name=name)
        else:
            write_parquet(output.df, path, name, _metadata_of(output), spec)
        written[name] = path
    return written


# ------------------------------------------------------------------- HDF5

_HDF_MIN_ITEMSIZE = {"kind": 8, "atom": 4}


def _hdf_write(method, name, df, **kwargs):
    """``store.put``/``append`` without HDF5 creation times (files are then reproducible)"""
    try:
        method(name, df, track_times=False, **kwargs)
    except TypeError:  # pandas versions without the keyword
        method(name, df, **kwargs)


def _hdf_set_metadata(store, name, document):
    store.get_storer(name).attrs[METADATA_KEY] = json.dumps(document)


def collision_to_hdf5_store(store, collision, name, chunk_rows, document):
    """Append the events of ``collision`` chunk-wise to the table ``name`` of ``store``"""
    offset = 0
    for chunk in event_chunks(collision, chunk_rows):
        chunk.index = pd.RangeIndex(offset, offset + len(chunk))
        offset += len(chunk)
        _hdf_write(store.append, name, chunk, format="table", min_itemsize=_HDF_MIN_ITEMSIZE)
    if offset == 0:
        empty = pd.DataFrame(
            {c: pd.Series(dtype=d) for c, d in zip(EVENT_COLUMNS, _EVENT_DTYPES, strict=True)}
        )
        _hdf_write(store.append, name, empty, format="table", min_itemsize=_HDF_MIN_ITEMSIZE)
    _hdf_set_metadata(store, name, document)
    return offset


def results_to_hdf5(results, path, spec=None, include=None, chunk_rows=DEFAULT_CHUNK_ROWS):
    _require("tables", "hdf5")
    selected = _select(results, include)
    chunk_rows = _check_chunk_rows(chunk_rows)
    exported = _now()
    parent = os.path.dirname(os.fspath(path))
    if parent:
        os.makedirs(parent, exist_ok=True)
    # mode "w": the file holds exactly the tables of this export
    with pd.HDFStore(os.fspath(path), mode="w", complevel=4, complib="zlib") as store:
        for name, output in selected.items():
            document = _document(name, _metadata_of(output), spec, exported)
            if name == "collision":
                collision_to_hdf5_store(store, output, name, chunk_rows, document)
            else:
                _hdf_write(store.put, name, output.df, format="table")
                _hdf_set_metadata(store, name, document)
    return os.fspath(path)


# -------------------------------------------------------------------- CSV


def collision_to_csv(collision, path, chunk_rows=DEFAULT_CHUNK_ROWS):
    """Stream the events of ``collision`` into one CSV file at ``path``; returns the row count"""
    chunk_rows = _check_chunk_rows(chunk_rows)
    total = 0
    with open(path, "w", newline="", encoding="utf-8") as f:
        for chunk in event_chunks(collision, chunk_rows):
            chunk.to_csv(f, index=False, header=total == 0, lineterminator="\n")
            total += len(chunk)
        if total == 0:
            f.write(",".join(EVENT_COLUMNS) + "\n")
    return total


def results_to_csv(results, directory, spec=None, include=None, chunk_rows=DEFAULT_CHUNK_ROWS):
    selected = _select(results, include)
    os.makedirs(directory, exist_ok=True)
    exported = _now()
    written = {}
    tables = {}
    for name, output in selected.items():
        path = os.path.join(directory, f"{name}.csv")
        if name == "collision":
            collision_to_csv(output, path, chunk_rows)
        else:
            output.df.to_csv(path, index=False, lineterminator="\n")
        tables[name] = _jsonable(_metadata_of(output))
        written[name] = path
    document = {
        "pysrim2_version": _version(),
        "exported_utc": exported,
        "spec": _jsonable(spec),
        "tables": tables,
    }
    with open(os.path.join(directory, "metadata.yaml"), "w", encoding="utf-8") as f:
        yaml.safe_dump(document, f, sort_keys=False)
    return written
