"""The pure parts of the to-scale cross-section of the target. Nothing here imports Qt.

:func:`layer_boxes` gives the width of each layer in pixels. The layers share the width in
proportion to their thickness, but no layer is narrower than ``min_px`` (a thin layer is drawn
wider than to scale, and marked as such), so every layer stays visible and clickable. With more
layers than fit at that width, each gets an equal share of the width and none is to scale.

:func:`beam_angle` is the angle the beam arrow is drawn at (the mean of a distribution), and
:func:`layer_color` and :func:`layer_tooltip` are what the drawing shows for a layer.
"""

import math
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from srim.app.model import UNITS, best_unit, layer_label
from srim.beam.shapes import Shape
from srim.beam.spec import quantity_from_value
from srim.core.elementdb import ElementDB

#: the narrowest a layer is drawn, in pixels
MIN_LAYER_PX = 6
#: the fill of a layer with no element (or an element the database does not know)
NO_COLOUR = "#D0D0D0"
#: the note at the end of the tooltip of a layer drawn wider than to scale
WIDER_NOTE = "(drawn wider than to scale)"


@dataclass(frozen=True)
class LayerBox:
    """A layer's horizontal place in the drawing: ``left`` and ``width`` in pixels."""

    left: int
    width: int
    to_scale: bool


def _usable_thickness(value: Any) -> float | None:
    """A thickness that can be drawn to scale, or None: missing, not a number, or not positive."""
    if value is None or isinstance(value, bool):
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) and number > 0 else None


def _largest_remainder(exact: dict[int, float], total: int) -> dict[int, int]:
    """Whole numbers near ``exact`` that add up to ``total`` (the largest fractions get the rest)."""
    whole = {i: math.floor(value) for i, value in exact.items()}
    spare = total - sum(whole.values())
    order = sorted(exact, key=lambda i: (-(exact[i] - whole[i]), i))
    for i in order[:spare]:
        whole[i] += 1
    return whole


def layer_boxes(
    thicknesses_nm: list[float | None], width_px: int, min_px: int = MIN_LAYER_PX
) -> list[LayerBox]:
    """The place of each layer across ``width_px`` pixels, left to right.

    A layer whose share would be below ``min_px`` gets ``min_px`` and is not to scale. A missing
    or non-positive thickness is such a layer too. The other layers share what is left in
    proportion to their thickness. The widths add up to ``width_px``; when no layer is to scale
    they share it evenly. When the layers do not fit at ``min_px`` each gets ``width_px // n``
    pixels (at least one) and none is to scale.
    """
    count = len(thicknesses_nm)
    if count == 0:
        return []
    width_px = max(0, int(width_px))
    if count * min_px > width_px:
        each = max(1, width_px // count)
        return _place([each] * count, [False] * count)

    values = [_usable_thickness(t) for t in thicknesses_nm]
    fixed = [value is None for value in values]
    while True:
        free = [i for i in range(count) if not fixed[i]]
        if not free:
            break
        scale = max(values[i] for i in free)
        weight = {i: values[i] / scale for i in free}
        total = sum(weight.values())
        remaining = width_px - min_px * sum(fixed)
        narrow = [i for i in free if remaining * weight[i] / total < min_px]
        if not narrow:
            break
        for i in narrow:
            fixed[i] = True

    if free:
        exact = {i: remaining * weight[i] / total for i in free}
        shares = _largest_remainder(exact, remaining)
        widths = [shares[i] if i in shares else min_px for i in range(count)]
    else:
        shares = _largest_remainder({i: width_px / count for i in range(count)}, width_px)
        widths = [shares[i] for i in range(count)]
    return _place(widths, [not is_fixed for is_fixed in fixed])


def _place(widths: list[int], to_scale: list[bool]) -> list[LayerBox]:
    boxes, left = [], 0
    for width, scaled in zip(widths, to_scale):
        boxes.append(LayerBox(left, width, scaled))
        left += width
    return boxes


def beam_angle(value: Any, base_dir: str | Path | None = None) -> float:
    """The beam's angle in degrees: a number, or the mean of a distribution.

    0.0 when the value is not usable (missing, unreadable or not finite). A histogram file is
    read from ``base_dir``, the folder of the setup.
    """
    try:
        quantity = quantity_from_value(value, "angle_deg", base_dir)
        angle = float(quantity.mean() if isinstance(quantity, Shape) else quantity)
    except Exception:  # any value the library cannot read draws the beam straight on
        return 0.0
    return angle if math.isfinite(angle) else 0.0


def layer_color(layer: dict) -> str:
    """The fill of a layer: the colour of its first element, or grey."""
    elements = layer.get("elements") or []
    if not elements:
        return NO_COLOUR
    try:
        return str(ElementDB.lookup(elements[0].get("symbol"))["color"])
    except (KeyError, ValueError, TypeError, AttributeError):
        return NO_COLOUR


def display_unit(layer: dict) -> str:
    """The layer's own display unit, else the best one for its thickness."""
    unit = layer.get("_unit")
    if unit in UNITS:
        return unit
    thickness = layer.get("thickness_nm")
    return best_unit(thickness) if _usable_thickness(thickness) is not None else "nm"


def layer_tooltip(layer: dict, to_scale: bool) -> str:
    """``"<label>: <thickness> <unit>"``, with the wider-than-scale note when it is not to scale."""
    label = layer_label(layer)
    thickness = layer.get("thickness_nm")
    if thickness is None or isinstance(thickness, bool):
        text = f"{label}: no thickness"
    else:
        unit = display_unit(layer)
        text = f"{label}: {float(thickness) / UNITS[unit]:g} {unit}"
    return text if to_scale else f"{text} {WIDER_NOTE}"
