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
``metadata`` (the ``.metadata`` dict of the output, with ``source`` reduced to the file
name so that no local paths are exported) and ``spec`` (the run specification
if one is passed, else ``null``).

Chunks
------
Every table is written in chunks, so that no export holds a whole big output in memory:

- ``collision`` (opt-in: ``include=["collision"]``) streams its events from
  :meth:`srim.output.Collision.iter_events`. Its columns are fixed (the same in every chunk
  and file): integer ``ion``, strings ``kind`` and ``atom``, and 64-bit floats for everything
  else; fields that do not apply to an event kind are ``NaN`` (floats) and, in Parquet, null.
- the per-ion tables (:data:`PER_ION_TABLES`: ``range3d``, ``exyz``, ``backscat``,
  ``transmit``, ``sputter``) stream their file through ``iter_chunks``; the chunks are the
  rows of ``.df``, in order.
- the depth profiles (``ioniz``, ``range`` ...) are small and are written whole.

Chunks are ``chunk_rows`` rows (events or ions) each. Parquet gets one row group per chunk,
HDF5 appends the chunks to the table, CSV is written chunk by chunk with one header.
"""

import json
import os
from datetime import UTC, datetime
from importlib import import_module
from importlib import metadata as importlib_metadata

import pandas as pd
import yaml

from .output._chunks import concat_chunks, iter_row_chunks
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
#: the per-ion tables: their files are read in chunks (``iter_chunks``) by every export
PER_ION_TABLES = ("range3d", "exyz", "backscat", "transmit", "sputter")
#: events per chunk when streaming ``COLLISON.txt``
DEFAULT_CHUNK_ROWS = 100_000
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


def _public(metadata):
    """Metadata as exported: ``source`` is the file name only (no directories, so no
    user names or local paths end up in shared files)"""
    metadata = dict(metadata or {})
    if isinstance(metadata.get("source"), str):
        metadata["source"] = os.path.basename(metadata["source"].replace("\\", "/"))
    return metadata


def _spec_json(spec):
    """``spec`` as JSON types: ``None``, a mapping or a :class:`srim.spec.RunSpec`"""
    if spec is None:
        return None
    if hasattr(spec, "to_dict") and not isinstance(spec, dict):
        spec = spec.to_dict()
    if not isinstance(spec, dict):
        raise TypeError(f"spec must be a RunSpec or a dict, not {type(spec).__name__}")
    return _jsonable(spec)


def _document(name, metadata, spec, exported):
    return {
        "pysrim2_version": _version(),
        "exported_utc": exported,
        "table": name,
        "metadata": _jsonable(_public(metadata)),
        "spec": _spec_json(spec),
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


def _at_least_one(chunks, columns, dtypes):
    """``chunks``, or one empty frame with ``columns`` and ``dtypes`` when there are none, so
    that every writer gets the columns and dtypes of the table"""
    given = False
    for chunk in chunks:
        given = True
        yield chunk
    if not given:
        yield concat_chunks([], columns, dtypes)


def event_chunks(collision, chunk_rows=DEFAULT_CHUNK_ROWS):
    """Stream the events of ``collision`` as DataFrames of at most ``chunk_rows`` rows"""
    rows = ([event[c] for c in EVENT_COLUMNS] for event in collision.iter_events())
    chunks = iter_row_chunks(rows, EVENT_COLUMNS, _EVENT_DTYPES, chunk_rows)
    return _at_least_one(chunks, EVENT_COLUMNS, _EVENT_DTYPES)


def table_chunks(name, output, chunk_rows=DEFAULT_CHUNK_ROWS):
    """The rows of table ``name`` as DataFrames of at most ``chunk_rows`` rows (at least one).

    ``collision`` streams its events, the per-ion tables (:data:`PER_ION_TABLES`) their file
    (``iter_chunks``); the depth profiles give their ``.df`` as one chunk.
    """
    if name == "collision":
        return event_chunks(output, chunk_rows)
    if name in PER_ION_TABLES:
        return output.iter_chunks(chunk_rows)
    return iter([output.df])


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


def _parquet_chunks(path, chunks, name, metadata=None, spec=None):
    """Write the frames of ``chunks`` (at least one) to one Parquet file, a row group each;
    returns the number of rows"""
    pa = _require("pyarrow", "parquet")
    pq = _require("pyarrow.parquet", "parquet")
    document = _document(name, metadata, spec, _now())
    writer = None
    total = 0
    try:
        for chunk in chunks:
            table = _parquet_table(pa, chunk, document)
            if writer is None:
                writer = pq.ParquetWriter(os.fspath(path), table.schema)
            writer.write_table(table)
            total += len(chunk)
    finally:
        if writer is not None:
            writer.close()
    return total


def write_parquet(df, path, name, metadata=None, spec=None):
    """Write ``df`` to ``path`` with the pysrim2 metadata document in the schema"""
    _parquet_chunks(path, [df], name, metadata, spec)


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
    spec = _spec_json(spec)  # fail early on a bad spec, before writing anything
    _require("pyarrow.parquet", "parquet")
    chunk_rows = _check_chunk_rows(chunk_rows)
    selected = _select(results, include)
    os.makedirs(directory, exist_ok=True)
    written = {}
    for name, output in selected.items():
        path = os.path.join(directory, f"{name}.parquet")
        if name == "collision":
            collision_to_parquet(output, path, chunk_rows, _metadata_of(output), spec, name=name)
        else:
            chunks = table_chunks(name, output, chunk_rows)
            _parquet_chunks(path, chunks, name, _metadata_of(output), spec)
        written[name] = path
    return written


# ------------------------------------------------------------------- HDF5

_HDF_MIN_ITEMSIZE = {"kind": 8, "atom": 4}


def _hdf_write(method, name, df, **kwargs):
    """``store.put``/``append`` without HDF5 object timestamps (``track_times=False``).

    The exported files are still not byte-identical between runs: the metadata holds
    ``exported_utc``."""
    try:
        method(name, df, track_times=False, **kwargs)
    except TypeError:  # pandas versions without the keyword
        method(name, df, **kwargs)


def _hdf_set_metadata(store, name, document):
    store.get_storer(name).attrs[METADATA_KEY] = json.dumps(document)


def _hdf5_chunks(store, name, chunks, document, min_itemsize=None):
    """Append the frames of ``chunks`` (at least one) to the table ``name`` of ``store``; the
    index continues across the chunks (the row positions). Returns the number of rows."""
    offset = 0
    options = {"format": "table"}
    if min_itemsize:
        options["min_itemsize"] = min_itemsize
    for chunk in chunks:
        chunk.index = pd.RangeIndex(offset, offset + len(chunk))
        offset += len(chunk)
        _hdf_write(store.append, name, chunk, **options)
    _hdf_set_metadata(store, name, document)
    return offset


def collision_to_hdf5_store(store, collision, name, chunk_rows, document):
    """Append the events of ``collision`` chunk-wise to the table ``name`` of ``store``"""
    chunks = event_chunks(collision, chunk_rows)
    return _hdf5_chunks(store, name, chunks, document, _HDF_MIN_ITEMSIZE)


def results_to_hdf5(results, path, spec=None, include=None, chunk_rows=DEFAULT_CHUNK_ROWS):
    spec = _spec_json(spec)  # fail early on a bad spec, before writing anything
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
            if name == "collision" or name in PER_ION_TABLES:
                min_itemsize = _HDF_MIN_ITEMSIZE if name == "collision" else None
                chunks = table_chunks(name, output, chunk_rows)
                _hdf5_chunks(store, name, chunks, document, min_itemsize)
            else:
                _hdf_write(store.put, name, output.df, format="table")
                _hdf_set_metadata(store, name, document)
    return os.fspath(path)


# -------------------------------------------------------------------- CSV


def _csv_chunks(path, chunks):
    """Write the frames of ``chunks`` (at least one) to a CSV file, the header once; returns
    the number of rows"""
    total = 0
    with open(path, "w", newline="", encoding="utf-8") as f:
        for number, chunk in enumerate(chunks):
            chunk.to_csv(f, index=False, header=number == 0, lineterminator="\n")
            total += len(chunk)
    return total


def collision_to_csv(collision, path, chunk_rows=DEFAULT_CHUNK_ROWS):
    """Stream the events of ``collision`` into one CSV file at ``path``; returns the row count"""
    chunk_rows = _check_chunk_rows(chunk_rows)
    return _csv_chunks(path, event_chunks(collision, chunk_rows))


def results_to_csv(results, directory, spec=None, include=None, chunk_rows=DEFAULT_CHUNK_ROWS):
    spec = _spec_json(spec)  # fail early on a bad spec, before writing anything
    chunk_rows = _check_chunk_rows(chunk_rows)
    selected = _select(results, include)
    os.makedirs(directory, exist_ok=True)
    exported = _now()
    written = {}
    tables = {}
    for name, output in selected.items():
        path = os.path.join(directory, f"{name}.csv")
        _csv_chunks(path, table_chunks(name, output, chunk_rows))
        tables[name] = _jsonable(_public(_metadata_of(output)))
        written[name] = path
    document = {
        "pysrim2_version": _version(),
        "exported_utc": exported,
        "spec": _spec_json(spec),
        "tables": tables,
    }
    with open(os.path.join(directory, "metadata.yaml"), "w", encoding="utf-8") as f:
        yaml.safe_dump(document, f, sort_keys=False)
    return written
