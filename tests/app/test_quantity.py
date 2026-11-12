"""The Qt-free rules of a beam quantity: what an editor shows and what it emits.

These run without the app extra: the module does not import Qt.
"""

import pytest

from srim.app.quantity import KINDS, LABELS, OTHER, UNSET, build, describe, text_of, yaml_text


def test_the_kinds_and_their_labels_are_the_ones_the_tab_offers():
    assert KINDS == (
        "value",
        "gaussian",
        "uniform",
        "triangular",
        "lognormal",
        "function",
        "histogram",
    )
    assert LABELS == {
        "value": "Fixed value",
        "gaussian": "Gaussian",
        "uniform": "Uniform",
        "triangular": "Triangular",
        "lognormal": "Log-normal",
        "function": "Python function",
        "histogram": "Histogram file",
        "unset": "Not set",
    }


def test_no_value_is_not_set_for_an_optional_quantity_and_a_missing_value_otherwise():
    assert describe(None, optional=True).kind == UNSET
    shown = describe(None, optional=False)
    assert (shown.kind, shown.params, shown.editable) == ("value", {"value": None}, True)


def test_a_number_is_a_fixed_value():
    shown = describe(20, optional=False)
    assert (shown.kind, shown.params, shown.limit, shown.editable) == (
        "value",
        {"value": 20.0},
        None,
        True,
    )


def test_a_gaussian_with_a_range_is_read_into_its_fields_and_limit():
    shown = describe(
        {"gaussian": {"mean": 20.0, "sigma": 1.0, "range": [18.0, 22.0]}}, optional=False
    )
    assert shown.kind == "gaussian"
    assert shown.params == {"mean": 20.0, "sigma": 1.0}
    assert shown.limit == (18.0, 22.0)
    assert shown.editable


def test_a_gaussian_without_a_range_has_no_limit():
    assert describe({"gaussian": {"mean": 20.0, "sigma": 1.0}}, False).limit is None


def test_a_histogram_file_is_editable_but_an_inline_histogram_is_not():
    assert describe({"histogram": {"file": "/x/beam.txt"}}, False).editable
    inline = {"histogram": {"edges": [0.0, 1.0, 2.0], "counts": [1.0, 2.0]}}
    shown = describe(inline, False)
    assert (shown.kind, shown.editable) == (OTHER, False)


@pytest.mark.parametrize(
    "value",
    [
        {"fixed": {"value": 20.0}},
        {"gaussian": {"mean": 20.0, "sigma": 1.0, "range": [18.0]}},
        {"gaussian": {"mean": 20.0, "sigma": 1.0, "source": "x"}},
        {"gaussian": None},
        {"gaussian": 3},
        {"gaussian": {"mean": 20.0}, "uniform": {"low": 1.0}},
        "twenty",
        True,
    ],
)
def test_anything_else_is_not_editable(value):
    assert describe(value, optional=False).editable is False


def test_build_writes_the_forms_the_library_reads():
    assert build("value", {"value": 20.0}, None) == 20.0
    assert build("value", {"value": None}, None) is None
    assert build(UNSET, {}, None) is None
    assert build("gaussian", {"mean": 20.0, "sigma": None}, None) == {
        "gaussian": {"mean": 20.0, "sigma": None}
    }
    assert build("gaussian", {"mean": 20.0, "sigma": 1.0}, (18.0, 22.0)) == {
        "gaussian": {"mean": 20.0, "sigma": 1.0, "range": [18.0, 22.0]}
    }
    assert build("function", {"ref": None}, (0.0, None)) == {
        "function": {"ref": None, "range": [0.0, None]}
    }
    assert build("histogram", {"file": None}, None) == {"histogram": {"file": None}}


@pytest.mark.parametrize(
    "value",
    [
        20.0,
        None,
        {"gaussian": {"mean": 20.0, "sigma": 1.0, "range": [18.0, 22.0]}},
        {"uniform": {"low": 19.0, "high": 21.0}},
        {"triangular": {"low": 1.0, "mode": 2.0, "high": 4.0}},
        {"lognormal": {"mu": 0.5, "sigma": 0.1}},
        {"function": {"ref": "python:mod:fn", "range": [0.0, 1.0]}},
        {"histogram": {"file": "/x/beam.txt", "range": [1.0, 2.0]}},
    ],
)
def test_a_described_value_builds_back_to_itself(value):
    shown = describe(value, optional=value is None)
    assert build(shown.kind, shown.params, shown.limit) == value


def test_yaml_text_shows_a_value_the_editor_keeps():
    assert yaml_text({"fixed": {"value": 20.0}}) == "fixed:\n  value: 20.0"


def test_text_of_shows_numbers_in_full_and_none_as_empty():
    assert text_of(None) == ""
    assert text_of(20) == "20.0"
    assert text_of(0.123456789012) == "0.123456789012"
    assert text_of("python:mod:fn") == "python:mod:fn"
