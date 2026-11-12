"""The shape of one beam quantity in an editor: its kind, its parameters and its "Limit to" range.

Nothing here imports Qt. :func:`describe` reads a document value (a number, a ``{kind: params}``
mapping, or None) into what the editor shows, and :func:`build` turns the editor's fields back
into a value. A value the editor does not offer as a kind (``fixed``, an inline histogram with
``edges``) is not editable: it is shown as YAML and kept until the user picks another kind.
"""

import numbers
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

import yaml

#: the kinds an editor offers, in the order of its menu (a fixed value is a plain number)
KINDS = ("value", "gaussian", "uniform", "triangular", "lognormal", "function", "histogram")
#: the menu text of each kind; "unset" is the "Not set" choice of an optional quantity
LABELS = {
    "value": "Fixed value",
    "gaussian": "Gaussian",
    "uniform": "Uniform",
    "triangular": "Triangular",
    "lognormal": "Log-normal",
    "function": "Python function",
    "histogram": "Histogram file",
    "unset": "Not set",
}
UNSET = "unset"
#: the kind of a value the editor keeps as YAML (not one of :data:`KINDS`)
OTHER = "other"
#: the parameters of each distribution kind, in the order the editor shows them (no range)
PARAMETERS = {
    "value": ("value",),
    "gaussian": ("mean", "sigma"),
    "uniform": ("low", "high"),
    "triangular": ("low", "mode", "high"),
    "lognormal": ("mu", "sigma"),
    "function": ("ref",),
    "histogram": ("file",),
}


@dataclass(frozen=True)
class Shown:
    """What an editor shows for a value.

    ``kind`` is a :data:`KINDS` entry, :data:`UNSET`, or :data:`OTHER` (not editable). ``params``
    maps each parameter of the kind to its value (None when empty), and ``limit`` is the
    ``(low, high)`` of "Limit to" (either may be None) or None when there is no range.
    """

    kind: str
    params: dict
    limit: tuple | None
    editable: bool


def is_number(value: Any) -> bool:
    """A plain number, not a bool."""
    return isinstance(value, numbers.Real) and not isinstance(value, bool)


def _limit(raw: Any) -> tuple | None:
    """The ``(low, high)`` of a ``range`` entry; None for no range; ValueError when malformed."""
    if raw is None:
        return None
    if not isinstance(raw, (list, tuple)) or len(raw) != 2:
        raise ValueError(f"range must be [low, high], got {raw!r}")
    bounds = []
    for bound in raw:
        if bound is not None and not is_number(bound):
            raise ValueError(f"range must hold numbers, got {raw!r}")
        bounds.append(None if bound is None else float(bound))
    if bounds == [None, None]:
        return None
    return (bounds[0], bounds[1])


def describe(value: Any, optional: bool) -> Shown:
    """What an editor shows for ``value``. ``optional`` says whether None means "Not set"."""
    if value is None:
        if optional:
            return Shown(UNSET, {}, None, True)
        return Shown("value", {"value": None}, None, True)
    if is_number(value):
        return Shown("value", {"value": float(value)}, None, True)
    if isinstance(value, Mapping) and len(value) == 1:
        ((kind, body),) = value.items()
        if kind in PARAMETERS and kind != "value" and isinstance(body, Mapping):
            allowed = set(PARAMETERS[kind]) | {"range"}
            if set(body) <= allowed:
                try:
                    limit = _limit(body.get("range"))
                except ValueError:
                    return Shown(OTHER, {}, None, False)
                params = {name: body.get(name) for name in PARAMETERS[kind]}
                return Shown(kind, params, limit, True)
    return Shown(OTHER, {}, None, False)


def build(kind: str, params: dict, limit: tuple | None) -> Any:
    """The value an editor emits: a number, a ``{kind: params}`` mapping, or None for "Not set"."""
    if kind == UNSET:
        return None
    if kind == "value":
        return params.get("value")
    body = {name: params.get(name) for name in PARAMETERS[kind]}
    if limit is not None:
        body["range"] = [limit[0], limit[1]]
    return {kind: body}


def text_of(value: Any) -> str:
    """A field's text for ``value``: a number in full, text as it is, None as empty."""
    if value is None:
        return ""
    if is_number(value):
        return repr(float(value))
    return str(value)


def yaml_text(value: Any) -> str:
    """The YAML of a value, as the editor shows a value it keeps."""
    return yaml.safe_dump(value, sort_keys=False, default_flow_style=False).strip()
