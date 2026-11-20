"""The big per-ion tables are exported in chunks (``chunk_rows``) and the files hold what the
whole-table export would hold: the same CSV bytes, the same Parquet rows, dtypes and metadata,
the same HDF5 data. No export reads the whole table (``.df``) of a per-ion output.
"""

import json
import os
import re

import pandas as pd
import pytest
import yaml

from srim import export
from srim.output import EXYZ, Backscat, Ioniz, Range3D, Sputter, Transmit
from srim.output._common import SRIM_Output, _StreamedTable, _TableScan
from srim.scan.batches import BatchInfo, MergedResults, _MergedTable

pa = pytest.importorskip("pyarrow")
pq = pytest.importorskip("pyarrow.parquet")
pytest.importorskip("tables")

D = "test_files"
EV = os.path.join(D, "outputs-0.8")
SPEC = {"ion": "He", "energy_keV": 50}
# (table name, parser, fixture directory): every per-ion file, and its comma-decimal copy
CASES = [
    ("range3d", Range3D, os.path.join(D, "1")),
    ("range3d", Range3D, os.path.join(D, "comma")),
    ("exyz", EXYZ, os.path.join(EV, "exyz")),
    ("exyz", EXYZ, os.path.join(EV, "exyz", "comma")),
    ("backscat", Backscat, os.path.join(EV, "thin")),
    ("backscat", Backscat, os.path.join(EV, "thin", "comma")),
    ("transmit", Transmit, os.path.join(EV, "thin")),
    ("transmit", Transmit, os.path.join(EV, "thin", "comma")),
    ("sputter", Sputter, os.path.join(EV, "sputter")),
    ("sputter", Sputter, os.path.join(EV, "sputter", "comma")),
]


def ids(case):
    name, _, directory = case
    return f"{name}-{'comma' if 'comma' in directory else 'dot'}"


class Outputs:
    """Stands in for :class:`srim.output.Results`: the given tables, every other one ``None``"""

    def __init__(self, **tables):
        self._tables = tables

    def __getattr__(self, name):
        return self.__dict__.get("_tables", {}).get(name)


def whole_csv(df):
    """The CSV bytes of the whole-table export (what ``DataFrame.to_csv`` writes)"""
    return df.to_csv(index=False, lineterminator="\n").encode("utf-8")


def _boom(*_args, **_kwargs):
    raise AssertionError("the whole table was read")


@pytest.mark.parametrize("chunk_rows", [1, 3, 100_000])
@pytest.mark.parametrize("case", CASES, ids=ids)
def test_csv_is_byte_identical_to_the_whole_table(case, chunk_rows, tmp_path):
    name, cls, directory = case
    obj = cls(directory)
    export.results_to_csv(Outputs(**{name: obj}), tmp_path, include=[name], chunk_rows=chunk_rows)
    assert (tmp_path / f"{name}.csv").read_bytes() == whole_csv(obj.df)
    meta = yaml.safe_load((tmp_path / "metadata.yaml").read_text())
    assert meta["tables"][name] == json.loads(json.dumps(export._public(obj.metadata)))


@pytest.mark.parametrize("case", CASES, ids=ids)
def test_parquet_has_the_rows_schema_and_metadata_of_the_whole_table(case, tmp_path):
    name, cls, directory = case
    obj = cls(directory)
    whole = obj.df
    export.results_to_parquet(
        Outputs(**{name: obj}), tmp_path, spec=SPEC, include=[name], chunk_rows=3
    )
    path = tmp_path / f"{name}.parquet"
    with pq.ParquetFile(path) as pf:
        groups = [pf.metadata.row_group(i).num_rows for i in range(pf.num_row_groups)]
        assert len(groups) == -(-len(whole) // 3) > 1
        assert max(groups) <= 3 and sum(groups) == len(whole)
        reference = pa.Table.from_pandas(whole, preserve_index=False).schema
        assert [(f.name, f.type) for f in pf.schema_arrow] == [(f.name, f.type) for f in reference]
        back = pf.read().to_pandas()
    pd.testing.assert_frame_equal(back, whole, check_exact=True)
    document = json.loads(pq.read_schema(path).metadata[b"pysrim2"])
    assert re.fullmatch(r"\d{4}-\d\d-\d\dT\d\d:\d\d:\d\dZ", document.pop("exported_utc"))
    assert document["table"] == name
    assert document["spec"] == SPEC
    assert document["metadata"] == json.loads(json.dumps(export._public(obj.metadata)))


@pytest.mark.parametrize("case", CASES, ids=ids)
def test_hdf5_has_the_rows_and_metadata_of_the_whole_table(case, tmp_path):
    name, cls, directory = case
    obj = cls(directory)
    path = tmp_path / "out.h5"
    export.results_to_hdf5(Outputs(**{name: obj}), path, spec=SPEC, include=[name], chunk_rows=3)
    pd.testing.assert_frame_equal(pd.read_hdf(path, name), obj.df, check_exact=True)
    with pd.HDFStore(path, mode="r") as store:
        assert store.get_storer(name).nrows == len(obj.df)
        document = json.loads(store.get_storer(name).attrs.pysrim2)
    assert document["table"] == name and document["spec"] == SPEC
    assert document["metadata"] == json.loads(json.dumps(export._public(obj.metadata)))


@pytest.mark.parametrize("case", CASES, ids=ids)
def test_export_never_reads_the_whole_table(case, tmp_path, monkeypatch):
    name, cls, directory = case
    whole = cls(directory).df  # the reference is read before the patch
    expected = whole_csv(whole)
    monkeypatch.setattr(_StreamedTable, "_table_arrays", _boom)
    monkeypatch.setattr(_TableScan, "arrays", _boom)
    monkeypatch.setattr(SRIM_Output, "df", property(_boom))
    outputs = Outputs(**{name: cls(directory)})
    export.results_to_csv(outputs, tmp_path / "csv", include=[name], chunk_rows=3)
    export.results_to_parquet(outputs, tmp_path / "pq", include=[name], chunk_rows=3)
    export.results_to_hdf5(outputs, tmp_path / "h.h5", include=[name], chunk_rows=3)
    assert (tmp_path / "csv" / f"{name}.csv").read_bytes() == expected
    pd.testing.assert_frame_equal(
        pd.read_parquet(tmp_path / "pq" / f"{name}.parquet"), whole, check_exact=True
    )
    pd.testing.assert_frame_equal(pd.read_hdf(tmp_path / "h.h5", name), whole, check_exact=True)


@pytest.mark.parametrize("case", CASES[:3], ids=ids)
def test_chunk_rows_reaches_iter_chunks(case, tmp_path, monkeypatch):
    name, cls, directory = case
    seen = []
    original = cls.iter_chunks

    def spy(self, chunk_rows=50_000):
        seen.append(chunk_rows)
        return original(self, chunk_rows)

    monkeypatch.setattr(cls, "iter_chunks", spy)
    export.results_to_csv(Outputs(**{name: cls(directory)}), tmp_path, include=[name], chunk_rows=7)
    assert seen == [7]


def test_profile_tables_keep_their_whole_table_bytes(tmp_path):
    ioniz = Ioniz(D + "/1")
    export.results_to_csv(Outputs(ioniz=ioniz), tmp_path, include=["ioniz"], chunk_rows=2)
    assert (tmp_path / "ioniz.csv").read_bytes() == whole_csv(ioniz.df)


def test_chunk_rows_must_be_positive_for_per_ion_tables(tmp_path):
    obj = Range3D(D + "/1")
    with pytest.raises(ValueError, match="chunk_rows"):
        export.results_to_csv(Outputs(range3d=obj), tmp_path, include=["range3d"], chunk_rows=0)


def merged_results(columns=({"beam_batch": 0}, {"beam_batch": 1})):
    parts = [
        Outputs(range3d=Range3D(D + "/1"), exyz=EXYZ(os.path.join(EV, "exyz"))),
        Outputs(range3d=Range3D(D + "/1"), exyz=EXYZ(os.path.join(EV, "exyz"))),
    ]
    infos = [BatchInfo(i, i + 1, 10, columns=c) for i, c in enumerate(columns)]
    return MergedResults(parts, infos, spec=SPEC, base_seed=1)


@pytest.mark.parametrize("name", ["range3d", "exyz"])
def test_merged_chunks_are_the_merged_frame(name):
    merged = merged_results()
    whole = getattr(merged, name).df
    chunks = list(getattr(merged, name).iter_chunks(3))
    assert all(len(c) <= 3 for c in chunks)
    pd.testing.assert_frame_equal(pd.concat(chunks), whole, check_exact=True)


def test_merged_per_ion_exports_stream_and_match_the_merged_frame(tmp_path, monkeypatch):
    merged = merged_results()
    expected = {name: getattr(merged, name).df for name in ("range3d", "exyz")}
    monkeypatch.setattr(_MergedTable, "_build", _boom)
    monkeypatch.setattr(SRIM_Output, "df", property(_boom))
    names = ["range3d", "exyz"]
    export.results_to_csv(merged, tmp_path / "csv", include=names, chunk_rows=3)
    export.results_to_parquet(merged, tmp_path / "pq", include=names, chunk_rows=3)
    export.results_to_hdf5(merged, tmp_path / "m.h5", include=names, chunk_rows=3)
    for name in names:
        assert (tmp_path / "csv" / f"{name}.csv").read_bytes() == whole_csv(expected[name])
        pd.testing.assert_frame_equal(
            pd.read_parquet(tmp_path / "pq" / f"{name}.parquet"), expected[name], check_exact=True
        )
        pd.testing.assert_frame_equal(
            pd.read_hdf(tmp_path / "m.h5", name), expected[name], check_exact=True
        )


def test_merged_batches_with_different_bin_columns_export_as_the_frame(tmp_path):
    columns = ({"beam_batch": 0}, {})  # the second batch has no bin column
    merged = merged_results(columns)
    expected = merged.range3d.df
    assert merged.range3d._same_batch_columns() is False
    chunks = list(merged.range3d.iter_chunks(3))
    assert all(len(c) <= 3 for c in chunks)
    pd.testing.assert_frame_equal(pd.concat(chunks), expected, check_exact=True)
    export.results_to_csv(merged, tmp_path, include=["range3d"], chunk_rows=3)
    assert (tmp_path / "range3d.csv").read_bytes() == whole_csv(expected)
    with pytest.raises(ValueError, match="chunk_rows"):
        merged.range3d.iter_chunks(0)
