import numpy as np

from srim.output._chunks import concat_chunks, iter_lines, iter_row_chunks


def test_iter_lines_decodes_and_keeps_terminators_on_request(tmp_path):
    path = tmp_path / "f.txt"
    path.write_bytes(b"a\xb3b\r\nlast")
    assert list(iter_lines(path, "cp437")) == ["a│b", "last"]
    assert list(iter_lines(path, None, keepends=True)) == [b"a\xb3b\r\n", b"last"]


def test_row_chunks_and_concat():
    cols, dtypes = ["a", "b", "c"], [int, float, object]
    rows = ([i, None if i == 3 else i / 2, "x"] for i in range(7))
    chunks = list(iter_row_chunks(rows, cols, dtypes, chunk_rows=3))
    assert [len(c) for c in chunks] == [3, 3, 1]
    df = concat_chunks(chunks, cols, dtypes)
    assert list(df.index) == list(range(7))
    assert df["a"].dtype == np.dtype(int)
    assert np.isnan(df["b"][3]) and df["b"][6] == 3.0


def test_concat_of_nothing_is_an_empty_typed_frame():
    df = concat_chunks(iter_row_chunks([], ["a", "b"], [int, float]), ["a", "b"], [int, float])
    assert len(df) == 0 and list(df.columns) == ["a", "b"]
    assert df["a"].dtype == np.dtype(int)
