"""``iter_chunks`` of the per-ion parsers: the same rows as ``.df``, in chunks, and no whole
table read on the way (construction, metadata and chunks keep no rows).
"""

import os

import pandas as pd
import pytest

from srim.output import EXYZ, Backscat, Range3D, Sputter, Transmit, _common
from srim.output._common import SRIM_Output

D = "test_files"
EVENTS = os.path.join(D, "outputs-0.8")
# (parser, fixture directory, rows in the file)
FIXTURES = [
    (Range3D, os.path.join(D, "1"), 996),
    (Range3D, os.path.join(D, "comma"), 996),
    (EXYZ, os.path.join(EVENTS, "exyz"), 137),
    (EXYZ, os.path.join(EVENTS, "exyz", "comma"), 137),
    (Backscat, os.path.join(EVENTS, "thin"), 9),
    (Backscat, os.path.join(EVENTS, "thin", "comma"), 9),
    (Transmit, os.path.join(EVENTS, "thin"), 11),
    (Transmit, os.path.join(EVENTS, "thin", "comma"), 11),
    (Sputter, os.path.join(EVENTS, "sputter"), 148),
    (Sputter, os.path.join(EVENTS, "sputter", "comma"), 148),
]


def ids(case):
    cls, directory, _ = case
    return f"{cls.__name__}-{os.path.basename(directory)}"


def _raise(*_args, **_kwargs):
    raise AssertionError("the whole table was read")


@pytest.mark.parametrize("case", FIXTURES, ids=ids)
@pytest.mark.parametrize("chunk_rows", [1, 7, 100_000])
def test_chunks_are_the_rows_of_df(case, chunk_rows):
    cls, directory, rows = case
    obj = cls(directory)
    whole = obj.df
    assert len(whole) == rows
    chunks = list(obj.iter_chunks(chunk_rows))
    assert all(1 <= len(c) <= chunk_rows for c in chunks)
    assert len(chunks) == -(-rows // chunk_rows)
    for chunk in chunks:
        assert list(chunk.columns) == list(whole.columns)
        assert list(chunk.dtypes) == list(whole.dtypes)
    pd.testing.assert_frame_equal(pd.concat(chunks), whole, check_exact=True)


@pytest.mark.parametrize("case", FIXTURES, ids=ids)
def test_chunk_index_is_the_row_position(case):
    cls, directory, _ = case
    obj = cls(directory)
    start = 0
    for chunk in obj.iter_chunks(5):
        assert list(chunk.index) == list(range(start, start + len(chunk)))
        start += len(chunk)


def test_chunks_respect_max_rows():
    capped = Range3D(os.path.join(D, "1"), max_rows=10)
    assert capped.truncated is True
    pd.testing.assert_frame_equal(pd.concat(capped.iter_chunks(4)), capped.df, check_exact=True)
    assert sum(len(c) for c in capped.iter_chunks(4)) == 10


@pytest.mark.parametrize(("cls", "name"), [
    (Range3D, "RANGE_3D.txt"),
    (EXYZ, "EXYZ.txt"),
    (Backscat, "BACKSCAT.txt"),
    (Transmit, "TRANSMIT.txt"),
    (Sputter, "SPUTTER.txt"),
])  # fmt: skip
def test_empty_file_gives_one_empty_typed_chunk(cls, name, tmp_path):
    (tmp_path / name).write_bytes(b"")
    with pytest.warns(UserWarning, match="is empty"):
        obj = cls(str(tmp_path))
    chunks = list(obj.iter_chunks(3))
    assert len(chunks) == 1 and len(chunks[0]) == 0
    assert list(chunks[0].dtypes) == list(obj.df.dtypes)
    assert list(chunks[0].columns) == list(obj.df.columns)


def test_chunk_rows_must_be_positive():
    obj = Range3D(os.path.join(D, "1"))
    with pytest.raises(ValueError, match="chunk_rows"):
        obj.iter_chunks(0)


@pytest.mark.parametrize("case", FIXTURES, ids=ids)
def test_construction_metadata_and_chunks_never_read_the_whole_table(case, monkeypatch):
    cls, directory, rows = case
    obj = cls(directory)  # the reference values are read before the patch
    reference = obj.df
    monkeypatch.setattr(_common._TableScan, "arrays", _raise)
    monkeypatch.setattr(SRIM_Output, "df", property(_raise))
    fresh = cls(directory)
    assert fresh.metadata == obj.metadata
    assert sum(len(c) for c in fresh.iter_chunks(7)) == rows
    monkeypatch.undo()
    assert len(reference) == rows


def test_num_ions_counts_distinct_ions_of_event_files():
    exyz = EXYZ(os.path.join(EVENTS, "exyz"))
    assert exyz.num_ions == 10
    assert exyz.metadata["num_ions"] == len(set(exyz.df["ion"]))
    sputter = Sputter(os.path.join(EVENTS, "sputter"))
    assert sputter.num_ions == len(set(sputter.df["ion"]))
