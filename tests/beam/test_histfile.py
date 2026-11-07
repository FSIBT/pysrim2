"""Histogram files: reading measured distributions and the YAML ``histogram`` forms."""

import numpy as np
import pytest

from srim.beam import Histogram, shape_from_dict
from srim.beam.histfile import read_histogram
from srim.spec import SpecError


def write(tmp_path, text, name="h.txt", newline="\n"):
    path = tmp_path / name
    path.write_bytes(text.replace("\n", newline).encode("utf-8"))
    return path


def edges_counts(path):
    edges, counts = read_histogram(path)
    return list(map(float, edges)), list(map(float, counts))


# --- layouts ----------------------------------------------------------------------------------


def test_two_columns_are_centres(tmp_path):
    path = write(tmp_path, "value count\n1 10\n2 20\n3 30\n")
    # inner edges at the midpoints, outer edges half a neighbouring width beyond the ends
    assert edges_counts(path) == ([0.5, 1.5, 2.5, 3.5], [10.0, 20.0, 30.0])


def test_three_columns_are_low_high_count(tmp_path):
    path = write(tmp_path, "low high count\n0 1 1\n1 3 2\n")
    assert edges_counts(path) == ([0.0, 1.0, 3.0], [1.0, 2.0])


def test_single_three_column_bin_is_allowed(tmp_path):
    path = write(tmp_path, "0 2 5\n")
    assert edges_counts(path) == ([0.0, 2.0], [5.0])


def test_gap_between_three_column_bins_becomes_a_zero_bin(tmp_path):
    path = write(tmp_path, "0 1 1\n2 3 4\n")
    assert edges_counts(path) == ([0.0, 1.0, 2.0, 3.0], [1.0, 0.0, 4.0])


# --- separators and number formats --------------------------------------------------------------


@pytest.mark.parametrize(
    "text",
    [
        "1\t10\n2\t20\n",
        "1 10\n2 20\n",
        "1;10\n2;20\n",
        "1,10\n2,20\n",
        "1 , 10\n2 , 20\n",
    ],
)
def test_separators(tmp_path, text):
    path = write(tmp_path, text)
    assert edges_counts(path) == ([0.5, 1.5, 2.5], [10.0, 20.0])


def test_comma_decimals_with_semicolon_separator(tmp_path):
    path = write(tmp_path, "1,5;10\n2,5;20\n")
    assert edges_counts(path) == ([1.0, 2.0, 3.0], [10.0, 20.0])


def test_comma_decimals_with_space_separator(tmp_path):
    path = write(tmp_path, "1,5 10\n2,5 20\n")
    assert edges_counts(path) == ([1.0, 2.0, 3.0], [10.0, 20.0])


def test_comma_decimals_with_tab_separator(tmp_path):
    path = write(tmp_path, "1,5\t10\n2,5\t20\n")
    assert edges_counts(path) == ([1.0, 2.0, 3.0], [10.0, 20.0])


def test_crlf_line_endings(tmp_path):
    path = write(tmp_path, "value count\n1 10\n2 20\n", newline="\r\n")
    assert edges_counts(path) == ([0.5, 1.5, 2.5], [10.0, 20.0])


def test_comments_and_blank_lines(tmp_path):
    path = write(
        tmp_path,
        "# measured beam\n\nvalue count  # counts per bin\n1 10\n\n# middle\n2 20\n\n",
    )
    assert edges_counts(path) == ([0.5, 1.5, 2.5], [10.0, 20.0])


def test_header_row_is_skipped(tmp_path):
    path = write(tmp_path, "energy_keV, counts\n1, 10\n2, 20\n")
    assert edges_counts(path) == ([0.5, 1.5, 2.5], [10.0, 20.0])


def test_trailing_empty_column_is_ignored(tmp_path):
    path = write(tmp_path, "1;10;\n2;20;\n")
    assert edges_counts(path) == ([0.5, 1.5, 2.5], [10.0, 20.0])


# --- errors -----------------------------------------------------------------------------------


def test_negative_count_names_file_and_line(tmp_path):
    path = write(tmp_path, "1 10\n2 -5\n3 30\n")
    with pytest.raises(SpecError, match=r"line 2") as info:
        read_histogram(path)
    assert str(path) in str(info.value)
    assert "negative" in str(info.value)


def test_non_increasing_centres_name_the_line(tmp_path):
    path = write(tmp_path, "1 10\n3 20\n2 30\n")
    with pytest.raises(SpecError, match=r"line 3"):
        read_histogram(path)


def test_overlapping_three_column_bins_name_the_line(tmp_path):
    path = write(tmp_path, "0 2 1\n1 3 1\n")
    with pytest.raises(SpecError, match=r"line 2") as info:
        read_histogram(path)
    assert "overlap" in str(info.value)


def test_empty_three_column_bin_is_refused(tmp_path):
    path = write(tmp_path, "0 1 1\n2 2 1\n")
    with pytest.raises(SpecError, match=r"line 2"):
        read_histogram(path)


def test_all_counts_zero_names_the_file(tmp_path):
    path = write(tmp_path, "1 0\n2 0\n")
    with pytest.raises(SpecError) as info:
        read_histogram(path)
    assert str(path) in str(info.value)


def test_two_column_form_needs_two_rows(tmp_path):
    path = write(tmp_path, "1 10\n")
    with pytest.raises(SpecError) as info:
        read_histogram(path)
    assert str(path) in str(info.value)
    assert "two" in str(info.value) or "2" in str(info.value)


def test_mixed_column_counts_name_the_line(tmp_path):
    path = write(tmp_path, "0 1 1\n2 3 4\n5 6\n")
    with pytest.raises(SpecError, match=r"line 3"):
        read_histogram(path)


def test_non_numeric_row_names_the_line(tmp_path):
    path = write(tmp_path, "1 10\n2 twenty\n3 30\n")
    with pytest.raises(SpecError, match=r"line 2"):
        read_histogram(path)


def test_second_header_like_row_is_an_error(tmp_path):
    path = write(tmp_path, "value count\nvalue count\n1 10\n2 20\n")
    with pytest.raises(SpecError, match=r"line 2"):
        read_histogram(path)


def test_nan_and_inf_are_refused(tmp_path):
    path = write(tmp_path, "1 10\nnan 20\n")
    with pytest.raises(SpecError, match=r"line 2"):
        read_histogram(path)


def test_wrong_column_count_is_refused(tmp_path):
    path = write(tmp_path, "1 2 3 4\n5 6 7 8\n")
    with pytest.raises(SpecError) as info:
        read_histogram(path)
    assert str(path) in str(info.value)


def test_no_data_rows_is_refused(tmp_path):
    path = write(tmp_path, "# nothing here\n\n")
    with pytest.raises(SpecError) as info:
        read_histogram(path)
    assert str(path) in str(info.value)


def test_missing_file_is_a_spec_error(tmp_path):
    with pytest.raises(SpecError, match="missing.txt"):
        Histogram.from_file("missing.txt", base_dir=tmp_path)


# --- Histogram.from_file and the YAML forms -----------------------------------------------------


def test_file_relative_to_base_dir(tmp_path):
    write(tmp_path, "0 1 1\n1 2 3\n")
    h = Histogram.from_file("h.txt", base_dir=tmp_path)
    assert h.file == str((tmp_path / "h.txt").resolve())
    assert h.edges == [0.0, 1.0, 2.0]
    assert h.counts == [1.0, 3.0]


def test_file_form_in_shape_from_dict(tmp_path):
    write(tmp_path, "value count\n1 10\n2 20\n")
    shape = shape_from_dict({"histogram": {"file": "h.txt"}}, "energy", base_dir=tmp_path)
    assert isinstance(shape, Histogram)
    assert shape.to_dict() == {"histogram": {"file": str((tmp_path / "h.txt").resolve())}}


def test_file_form_with_range(tmp_path):
    write(tmp_path, "0 4 1\n4 8 1\n")
    shape = shape_from_dict(
        {"histogram": {"file": "h.txt", "range": [2, 6]}}, "energy", base_dir=tmp_path
    )
    assert shape.support() == (2.0, 6.0)


def test_inline_dict(tmp_path):
    write(tmp_path, "0 1 1\n1 3 2\n")
    h = Histogram.from_file("h.txt", base_dir=tmp_path)
    assert h.to_dict(inline=True) == {"histogram": {"edges": [0.0, 1.0, 3.0], "counts": [1.0, 2.0]}}
    assert "file" not in h.to_dict(inline=True)["histogram"]


def test_edges_and_counts_forms_in_shape_from_dict():
    shape = shape_from_dict({"histogram": {"edges": [0, 1, 3], "counts": [1, 1]}}, "energy")
    assert shape.edges == [0.0, 1.0, 3.0]
    assert shape.to_dict() == {"histogram": {"edges": [0.0, 1.0, 3.0], "counts": [1.0, 1.0]}}


def test_centres_form_in_shape_from_dict():
    shape = shape_from_dict({"histogram": {"centres": [1, 2, 4], "counts": [1, 1, 2]}}, "energy")
    assert shape.edges == [0.5, 1.5, 3.0, 5.0]
    assert shape.counts == [1.0, 1.0, 2.0]


@pytest.mark.parametrize(
    "params",
    [
        {},
        {"file": "h.txt", "edges": [0, 1, 2], "counts": [1, 1]},
        {"file": "h.txt", "centres": [1, 2], "counts": [1, 1]},
        {"edges": [0, 1, 2]},
        {"counts": [1, 1]},
        {"centres": [1, 2]},
        {"edges": [0, 1, 2], "centres": [1, 2], "counts": [1, 1]},
    ],
)
def test_exactly_one_form_is_accepted(params):
    with pytest.raises(SpecError, match="energy") as info:
        shape_from_dict({"histogram": params}, "energy")
    assert "histogram" in str(info.value)


def test_shape_from_dict_errors_name_the_file_line(tmp_path):
    write(tmp_path, "1 10\n2 -1\n")
    with pytest.raises(SpecError, match="energy") as info:
        shape_from_dict({"histogram": {"file": "h.txt"}}, "energy", base_dir=tmp_path)
    assert "line 2" in str(info.value)


def test_edges_and_counts_are_not_shared_with_sampling():
    h = Histogram([0, 1, 3], [1, 1])
    h.edges.append(99.0)
    h.counts.append(99.0)
    assert h.edges == [0.0, 1.0, 3.0]
    assert h.counts == [1.0, 1.0]
    xs, F = h.nodes()
    assert np.allclose(xs, [0.0, 1.0, 3.0])


# --- review fixes: header and empty rows ---------------------------------------------------------


def test_spaced_header_does_not_change_comma_separator(tmp_path):
    # the header has a space, so it must not be read when choosing the separator
    path = write(tmp_path, "Energy (keV),Count\r\n1,10\r\n2,20\r\n")
    assert edges_counts(path) == ([0.5, 1.5, 2.5], [10.0, 20.0])


@pytest.mark.parametrize("empty_row", [";", ",,", " ; ", " , "])
def test_all_empty_rows_are_skipped(tmp_path, empty_row):
    if ";" in empty_row:  # decimal commas need a non-comma separator
        sep, first, second = ";", "1,5", "2,5"
        expected = [1.0, 2.0, 3.0]
    else:
        sep, first, second = ",", "1", "2"
        expected = [0.5, 1.5, 2.5]
    path = write(
        tmp_path,
        f"energy_keV{sep}count\r\n{first}{sep}10\r\n{empty_row}\r\n{second}{sep}20\r\n",
    )
    assert edges_counts(path) == (expected, [10.0, 20.0])
