# Export

`Results` writes its tables to Parquet, HDF5 or CSV, with metadata that says
where the data came from.

```python
results = trim.run()  # or Results("path/to/srim/output")
results.to_parquet("out")  # out/ioniz.parquet, out/range3d.parquet, ...
results.to_hdf5("run.h5")  # one file, key per table: /ioniz, /range3d, ...
results.to_csv("csv")  # csv/ioniz.csv, ... and csv/metadata.yaml
```

Each method returns what was written (`to_parquet` and `to_csv`: table name to
path; `to_hdf5`: the path). The tables are those of `Results.to_dataframes()`
(see {doc}`dataframes`) that exist for the run. `include=["ioniz", "range3d"]`
selects tables; asking for a table that is not available raises `ValueError`.

## Optional dependencies

Parquet needs `pysrim2[parquet]` (pyarrow) and HDF5 needs `pysrim2[hdf5]`
(PyTables); CSV needs nothing extra. Without the extra, the call raises an
`ImportError` that names it, e.g. `pip install 'pysrim2[parquet]'`.

```shell
uv tool install 'pysrim2[parquet,hdf5]'   # or: pip install 'pysrim2[parquet,hdf5]'
```

## Metadata

Every table carries the same kind of record:

| Key | Content |
|---|---|
| `pysrim2_version` | pysrim2 version that wrote the file |
| `exported_utc` | export time, ISO 8601, UTC |
| `table` | table name (`ioniz`, ...) |
| `metadata` | the output's `.metadata` (`ion`, `energy_eV`, `num_ions`, `source`, ...) |
| `spec` | the run spec (as in {doc}`run-spec`), or `null` |

`spec` defaults to `results.spec`, which `TRIM.run()` fills in; for `Results`
created from a folder pass `spec=` (a dict, e.g. `RunSpec.to_dict()`) yourself.
The provenance section of `run.yaml` and the host name are never exported, and
`metadata["source"]` is the file name only, not a path, so no user names or local
directories end up in files you share.

Where the record is stored:

- Parquet: JSON text in the schema's key-value metadata, key `pysrim2`:
  `pyarrow.parquet.read_schema("out/ioniz.parquet").metadata[b"pysrim2"]`.
- HDF5: JSON text in the node attribute `pysrim2`:
  `pd.HDFStore("run.h5").get_storer("ioniz").attrs.pysrim2`. Tables are written
  in pandas' `table` format; read them with `pd.read_hdf("run.h5", "ioniz")`. An
  existing file is overwritten.
- CSV: one `metadata.yaml` for the whole directory, with `pysrim2_version`,
  `exported_utc`, `spec` and `tables` (the `.metadata` of each CSV file).

## Collision events

`collision` can have millions of rows, so it is exported only when requested:

```python
results.to_parquet("out", include=["collision"])
results.collision.to_parquet("collision.parquet")  # or directly
results.collision.to_csv("collision.csv")
```

The events are streamed from `COLLISON.txt` (`Collision.iter_events()`) and
written in chunks of `chunk_rows` events (default 100 000; also an argument of
`Results.to_parquet`, `to_hdf5` and `to_csv`): Parquet gets one row group per
chunk, HDF5 is appended, CSV is written chunk by chunk. Memory use is
proportional to one chunk, not to the file. The columns are fixed (the same in
every chunk), with fields that do not apply to an event kind null (`NaN` in
pandas); see {doc}`dataframes`.

Measured with a 78 MB `COLLISON.txt` (Parquet export, peak extra memory):

| `chunk_rows` | extra memory | time |
|---|---|---|
| 1 000 | 22 MiB | 2.7 s |
| 500 000 | 168 MiB | 2.3 s |
| the whole file in one chunk | 1654 MiB | |

The default of 100 000 sits between the first two rows (roughly a fifth of the
500 000 figure; it was chosen from these measurements, not measured itself). For a
multi-gigabyte file lower `chunk_rows` if memory is tight; the time barely
changes. Compare with loading `Collision.df` (a 100 MB file: 2.9 s, 281 MiB peak,
of which 150 MB is the frame), which is what `iter_events()` and the chunked
export avoid. Reading `EXYZ.txt` (80 MB) takes 3.9 s and about 256 MB resident.

## Reading back

```python
import pandas as pd

pd.read_parquet("out/ioniz.parquet")
pd.read_hdf("run.h5", "range3d")
pd.read_csv("csv/ioniz.csv")
```

Floating-point values are written at full precision. Exports are not
byte-identical between runs, because the metadata holds the export time.
