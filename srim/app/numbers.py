"""Reading a number typed into an editor cell. Nothing here imports Qt.

An empty entry is "no value" (None), which the setup reports as missing. A single comma is read
as a decimal point, so "1,5" is 1.5. Anything else that is not a finite number is refused with
ValueError, and the editor keeps the value it had.
"""

import math


def parse_number(text: str) -> float | None:
    """The number in ``text``; None for an empty entry. Raises ValueError when it is refused."""
    stripped = text.strip()
    if not stripped:
        return None
    if stripped.count(",") == 1:
        stripped = stripped.replace(",", ".")
    if "_" in stripped:
        raise ValueError(f"{text!r} is not a number")
    try:
        value = float(stripped)
    except ValueError:
        raise ValueError(f"{text!r} is not a number") from None
    if not math.isfinite(value):
        raise ValueError(f"{text!r} is not a finite number")
    return value
