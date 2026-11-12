"""The periodic-table layout, isotope labels and the text colour for an element button.

Nothing here imports PySide6, so these tests run without the app extra.
"""

import dataclasses

import pytest

from srim.app.elements import (
    MAX_Z,
    ElementChoice,
    element_label,
    grid_position,
    text_color,
)


def test_the_table_offers_hydrogen_to_uranium():
    assert MAX_Z == 92


@pytest.mark.parametrize(
    "symbol, z, position",
    [
        ("H", 1, (0, 0)),
        ("He", 2, (0, 17)),
        ("Li", 3, (1, 0)),
        ("B", 5, (1, 12)),
        ("Ne", 10, (1, 17)),
        ("Na", 11, (2, 0)),
        ("Ar", 18, (2, 17)),
        ("K", 19, (3, 0)),
        ("Sc", 21, (3, 2)),
        ("Zn", 30, (3, 11)),
        ("Kr", 36, (3, 17)),
        ("Rb", 37, (4, 0)),
        ("Xe", 54, (4, 17)),
        ("Cs", 55, (5, 0)),
        ("Ba", 56, (5, 1)),
        ("La", 57, (8, 2)),
        ("Lu", 71, (8, 16)),
        ("Hf", 72, (5, 3)),
        ("Rn", 86, (5, 17)),
        ("Fr", 87, (6, 0)),
        ("Ra", 88, (6, 1)),
        ("Ac", 89, (9, 2)),
        ("U", 92, (9, 5)),
    ],
)
def test_elements_sit_in_their_place_in_the_table(symbol, z, position):
    assert grid_position(z) == position


def test_every_element_has_its_own_cell_and_row_seven_is_empty():
    positions = [grid_position(z) for z in range(1, MAX_Z + 1)]
    assert len(set(positions)) == MAX_Z
    assert all(row != 7 for row, _ in positions)


@pytest.mark.parametrize("z", [0, -1, 93, 118])
def test_an_atomic_number_outside_the_table_is_refused(z):
    with pytest.raises(ValueError):
        grid_position(z)


def test_the_natural_element_is_labelled_natural():
    assert element_label("Si", None) == "Si (natural)"


def test_an_isotope_mass_gets_the_isotope_label():
    assert element_label("Si", 27.9769265) == "Si-28"


def test_a_mass_that_is_no_natural_isotope_shows_the_mass():
    assert element_label("Si", 27.5) == "Si (27.5 amu)"


def test_an_element_choice_is_frozen_and_natural_by_default_is_none():
    choice = ElementChoice("Si", None)
    assert choice.mass_amu is None
    with pytest.raises(dataclasses.FrozenInstanceError):
        choice.symbol = "Au"


def test_a_light_background_gets_black_text():
    assert text_color("#FFFFFF") == "#000000"
    assert text_color("#D9FFFF") == "#000000"


def test_a_dark_background_gets_white_text():
    assert text_color("#000000") == "#FFFFFF"
    assert text_color("#545CF2") == "#FFFFFF"
