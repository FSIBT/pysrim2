"""``max_rows`` on the per-ion parsers: read at most that many rows, and say whether more existed.

The default (``None``) reads everything, as before. A capped parser has the first rows of the
file, the same values as the full parse, and ``truncated`` is True only when a row was left out.
"""

import os

import pandas as pd
import pytest

from srim.output import EXYZ, Backscat, Range3D, Sputter, Transmit
from srim.output._common import SRIMOutputParseError

D = "test_files"
RANGE_ROWS = 996  # complete rows of test_files/1/RANGE_3D.txt
EVENT_FILES = [
    (Backscat, os.path.join(D, "outputs-0.8", "thin"), 9),
    (Transmit, os.path.join(D, "outputs-0.8", "thin"), 11),
    (Sputter, os.path.join(D, "outputs-0.8", "sputter"), 148),
]


def test_range3d_default_reads_every_row_and_is_not_truncated():
    r = Range3D(os.path.join(D, "1"))
    assert r.num_ions == RANGE_ROWS
    assert r.truncated is False


def test_range3d_capped_has_the_first_rows_and_says_so():
    full = Range3D(os.path.join(D, "1")).df
    capped = Range3D(os.path.join(D, "1"), max_rows=10)
    assert capped.num_ions == 10
    assert capped.truncated is True
    pd.testing.assert_frame_equal(capped.df, full.iloc[:10].reset_index(drop=True))


def test_range3d_cap_equal_to_the_rows_is_not_truncated():
    r = Range3D(os.path.join(D, "1"), max_rows=RANGE_ROWS)
    assert r.num_ions == RANGE_ROWS
    assert r.truncated is False


def test_range3d_cap_of_zero_gives_no_rows_and_is_truncated():
    r = Range3D(os.path.join(D, "1"), max_rows=0)
    assert r.num_ions == 0
    assert r.truncated is True
    assert list(r.df.columns) == ["ion", "depth_A", "y_A", "z_A"]
    assert r.metadata["ion"] == "Ni"  # the header is still read


def test_range3d_header_only_file_with_cap_zero_is_empty_not_an_error(tmp_path):
    source = open(os.path.join(D, "1", "RANGE_3D.txt"), "rb").read()
    head = source[: source.index(b"0000001")]  # the header, no ion row
    (tmp_path / "RANGE_3D.txt").write_bytes(head)
    r = Range3D(str(tmp_path), max_rows=0)
    assert r.num_ions == 0
    assert r.truncated is False


@pytest.mark.parametrize(("cls", "folder", "rows"), EVENT_FILES)
def test_event_files_default_reads_every_row(cls, folder, rows):
    parser = cls(folder)
    assert len(parser.df) == rows
    assert parser.truncated is False


@pytest.mark.parametrize(("cls", "folder", "rows"), EVENT_FILES)
def test_event_files_capped_have_the_first_rows(cls, folder, rows):
    full = cls(folder).df
    capped = cls(folder, max_rows=3)
    assert capped.truncated is True
    assert len(capped.df) == 3
    pd.testing.assert_frame_equal(capped.df, full.iloc[:3].reset_index(drop=True))


@pytest.mark.parametrize(("cls", "folder", "rows"), EVENT_FILES)
def test_event_files_cap_equal_to_the_rows_is_not_truncated(cls, folder, rows):
    parser = cls(folder, max_rows=rows)
    assert len(parser.df) == rows
    assert parser.truncated is False


@pytest.mark.parametrize(("cls", "folder", "rows"), EVENT_FILES)
def test_event_files_cap_of_zero_is_empty(cls, folder, rows):
    parser = cls(folder, max_rows=0)
    assert len(parser.df) == 0
    assert parser.truncated is True
    assert parser.metadata["ion"] is not None  # the header is still read


def test_exyz_caps_its_rows():
    folder = os.path.join(D, "outputs-0.8", "exyz")
    full = EXYZ(folder).df
    assert len(full) == 137
    capped = EXYZ(folder, max_rows=5)
    assert capped.truncated is True
    pd.testing.assert_frame_equal(capped.df, full.iloc[:5].reset_index(drop=True))
    assert EXYZ(folder).truncated is False


def test_a_capped_parse_still_checks_the_header(tmp_path):
    (tmp_path / "RANGE_3D.txt").write_text("not a SRIM file\n", encoding="latin-1")
    with pytest.raises(SRIMOutputParseError):
        Range3D(str(tmp_path), max_rows=5)
