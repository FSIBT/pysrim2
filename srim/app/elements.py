"""The periodic table layout, isotope labels and button text colours. Nothing here imports Qt.

Z = 1 to 92 (SRIM's range) on an 18-column table. Row 7 is left empty; the lanthanides sit in
row 8 and the actinides in row 9, so the table fits without a gap for the f-block.
"""

from dataclasses import dataclass

from srim.core.isotopes import find_isotope, isotope_label

MAX_Z = 92

# First atomic number of periods 1 to 7 (the row of each period is its index).
_PERIOD_STARTS = (1, 3, 11, 19, 37, 55, 87)


@dataclass(frozen=True)
class ElementChoice:
    """An element and the mass chosen for it; ``mass_amu`` None means SRIM's natural mass."""

    symbol: str
    mass_amu: float | None = None


def grid_position(z):
    """Row and column of an element in the table, both counted from 0

    Parameters
    ----------
    z : :obj:`int`
        Atomic number, 1 to 92.

    Raises
    ------
    ValueError
        If ``z`` is outside 1 to 92.
    """
    if not isinstance(z, int) or isinstance(z, bool) or not 1 <= z <= MAX_Z:
        raise ValueError(f"atomic number {z!r} is outside 1 to {MAX_Z}")
    if 57 <= z <= 71:  # lanthanides, La at column 2
        return 8, z - 55
    if 89 <= z <= 103:  # actinides, Ac at column 2
        return 9, z - 87
    row = max(i for i, start in enumerate(_PERIOD_STARTS) if start <= z)
    offset = z - _PERIOD_STARTS[row]
    if row == 0:  # H and He
        return 0, 0 if offset == 0 else 17
    if row in (1, 2):  # the short periods: two s-block columns, then the p-block at 12 to 17
        return row, offset if offset < 2 else offset + 10
    if row in (3, 4):  # the long periods without the f-block: column = offset
        return row, offset
    # periods 6 and 7: the f-block takes 15 places after the two s-block columns
    return row, offset if offset < 2 else offset - 14


def element_label(symbol, mass_amu):
    """Label of an element with a mass: ``"Si (natural)"``, ``"Si-28"`` or ``"Si (27.5 amu)"``

    A mass within 1e-4 amu of a natural isotope is labelled with that isotope; any other mass
    is shown as it is.
    """
    if mass_amu is None:
        return f"{symbol} (natural)"
    isotope = find_isotope(symbol, mass_amu)
    if isotope is not None:
        return isotope_label(isotope)
    return f"{symbol} ({mass_amu:g} amu)"


def _channel(value):
    """sRGB channel 0 to 255 as linear light (WCAG 2)"""
    c = value / 255
    return c / 12.92 if c <= 0.03928 else ((c + 0.055) / 1.055) ** 2.4


def relative_luminance(color):
    """WCAG relative luminance of a colour ``"#RRGGBB"``, from 0 (black) to 1 (white)"""
    red, green, blue = (int(color[i : i + 2], 16) for i in (1, 3, 5))
    return 0.2126 * _channel(red) + 0.7152 * _channel(green) + 0.0722 * _channel(blue)


def text_color(background):
    """``"#000000"`` or ``"#FFFFFF"``, whichever gives the higher contrast on ``background``"""
    luminance = relative_luminance(background)
    contrast_black = (luminance + 0.05) / 0.05
    contrast_white = 1.05 / (luminance + 0.05)
    return "#000000" if contrast_black >= contrast_white else "#FFFFFF"
