"""The Qt-free part of the cross-section drawing: widths, beam angle and tooltips.

This file must not import PySide6: it runs without the app extra.
"""

import pytest

from srim.app.drawing import (
    MIN_LAYER_PX,
    LayerBox,
    beam_angle,
    layer_boxes,
    layer_color,
    layer_tooltip,
)


def test_a_thin_layer_on_a_thick_one_stays_visible():
    boxes = layer_boxes([1e7, 10.0], 400)
    assert [b.width for b in boxes] == [394, 6]
    assert [b.to_scale for b in boxes] == [True, False]


def test_widths_add_up_and_are_proportional():
    boxes = layer_boxes([100.0, 200.0, 100.0], 401)
    assert sum(b.width for b in boxes) == 401
    assert boxes[1].width in (200, 201)
    assert [b.left for b in boxes] == [0, boxes[0].width, boxes[0].width + boxes[1].width]


def test_more_layers_than_fit():
    boxes = layer_boxes([1.0] * 100, 300)
    assert all(b.width == 3 and not b.to_scale for b in boxes)


def test_missing_thickness_and_no_layers():
    assert layer_boxes([], 300) == []
    assert layer_boxes([None, 0.0], 300)[0].to_scale is False


def test_a_missing_layer_gets_the_minimum_next_to_a_to_scale_one():
    boxes = layer_boxes([None, 100.0], 300)
    assert boxes[0] == LayerBox(0, MIN_LAYER_PX, False)
    assert boxes[1].width == 300 - MIN_LAYER_PX and boxes[1].to_scale
    assert sum(b.width for b in boxes) == 300


def test_all_layers_missing_share_the_width_evenly():
    boxes = layer_boxes([None, None, None], 300)
    assert [b.width for b in boxes] == [100, 100, 100]
    assert not any(b.to_scale for b in boxes)


def test_layers_just_above_the_minimum_are_to_scale():
    boxes = layer_boxes([1.0, 1.0, 98.0], 100)  # the first two are 1 px each: below 6
    assert [b.to_scale for b in boxes] == [False, False, True]
    assert [b.width for b in boxes] == [6, 6, 88]


def test_a_huge_thickness_does_not_overflow_the_ratio():
    boxes = layer_boxes([1e308, 1e308], 200)
    assert [b.width for b in boxes] == [100, 100]


def test_a_thickness_that_is_not_finite_counts_as_missing():
    boxes = layer_boxes([float("inf"), float("nan"), 50.0], 200)
    assert [b.to_scale for b in boxes] == [False, False, True]
    assert sum(b.width for b in boxes) == 200


def test_the_minimum_can_be_given():
    boxes = layer_boxes([1e6, 1.0], 100, min_px=10)
    assert boxes[1] == LayerBox(90, 10, False)


def test_boxes_are_contiguous_with_no_gaps():
    thicknesses = [3.0, None, 1e5, 0.5, 40.0]
    boxes = layer_boxes(thicknesses, 517)
    assert boxes[0].left == 0
    for before, after in zip(boxes, boxes[1:], strict=False):
        assert after.left == before.left + before.width
    assert boxes[-1].left + boxes[-1].width == 517


@pytest.mark.parametrize("count", [1, 2, 7, 50, 200])
def test_every_layer_has_at_least_one_pixel(count):
    boxes = layer_boxes([1.0] * count, 40)
    assert all(b.width >= 1 for b in boxes)


def test_beam_angle_of_a_number_and_of_a_gaussian():
    assert beam_angle(30) == 30.0
    assert beam_angle({"gaussian": {"mean": 30.0, "sigma": 0.5}}) == pytest.approx(30.0)


def test_beam_angle_is_zero_when_the_value_is_not_usable():
    assert beam_angle(None) == 0.0
    assert beam_angle({"gaussian": {"mean": "abc"}}) == 0.0
    assert beam_angle({"function": {"ref": "python:no_such_module:f"}}) == 0.0


def test_beam_angle_reads_a_histogram_file_from_its_folder(tmp_path):
    (tmp_path / "angles.txt").write_text("10 1\n30 1\n")
    value = {"histogram": {"file": "angles.txt"}}
    assert beam_angle(value, base_dir=tmp_path) == pytest.approx(20.0)
    assert beam_angle(value) == 0.0  # no folder: the relative path is not found


def test_layer_color_is_the_first_element_colour_and_grey_otherwise():
    assert layer_color({"elements": [{"symbol": "Au"}, {"symbol": "Si"}]}) == "#FFD123"
    assert layer_color({"elements": []}) == "#D0D0D0"
    assert layer_color({"elements": [{"symbol": "Xx"}]}) == "#D0D0D0"
    assert layer_color({}) == "#D0D0D0"


def test_the_tooltip_of_a_thin_layer_says_it_is_drawn_wider():
    layer = {"name": "thin", "thickness_nm": 10.0, "_unit": "nm"}
    assert layer_tooltip(layer, to_scale=False) == "thin: 10 nm (drawn wider than to scale)"


def test_the_tooltip_of_a_to_scale_layer_has_no_note():
    layer = {"name": "gold", "thickness_nm": 100.0, "_unit": "Å"}
    assert layer_tooltip(layer, to_scale=True) == "gold: 1000 Å"


def test_the_tooltip_falls_back_to_the_best_unit_and_the_label_rule():
    layer = {"elements": [{"symbol": "Si"}, {"symbol": "O"}], "thickness_nm": 2e6}
    assert layer_tooltip(layer, to_scale=True) == "Si+O: 2 mm"
    assert layer_tooltip({"thickness_nm": None}, to_scale=False) == (
        "new layer: no thickness (drawn wider than to scale)"
    )


def test_the_tooltip_of_a_layer_with_an_unknown_unit_uses_the_best_one():
    layer = {"name": "x", "thickness_nm": 1500.0, "_unit": "parsec"}
    assert layer_tooltip(layer, to_scale=True) == "x: 1.5 µm"


@pytest.mark.parametrize("thickness", ["abc", [1.0], {"nm": 1}, float("nan"), "1e400", True])
def test_the_tooltip_never_raises_for_a_thickness_that_is_not_a_number(thickness):
    layer = {"name": "x", "thickness_nm": thickness}
    assert layer_tooltip(layer, to_scale=True) == "x: no thickness"


def test_a_zero_thickness_is_shown_not_hidden():
    layer = {"name": "x", "thickness_nm": 0.0, "_unit": "nm"}
    assert layer_tooltip(layer, to_scale=False) == "x: 0 nm (drawn wider than to scale)"
