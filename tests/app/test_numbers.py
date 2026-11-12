"""The number parser for the editors' cells. Qt-free: runs without the app extra."""

import pytest

from srim.app.numbers import parse_number


@pytest.mark.parametrize("text", ["", "   ", "\t"])
def test_an_empty_entry_is_none(text):
    assert parse_number(text) is None


@pytest.mark.parametrize(
    ("text", "value"),
    [
        ("10", 10.0),
        ("  2.5 ", 2.5),
        ("1,5", 1.5),
        ("-1", -1.0),
        ("1e-3", 0.001),
        ("0", 0.0),
    ],
)
def test_a_number_is_read_with_a_single_comma_as_a_point(text, value):
    assert parse_number(text) == value


@pytest.mark.parametrize(
    "text", ["abc", "1e400", "-1e400", "nan", "inf", "1,5,6", "1,000.5", "1_000", "1 000", "1..5"]
)
def test_an_unparsable_or_non_finite_entry_is_refused(text):
    with pytest.raises(ValueError):
        parse_number(text)
