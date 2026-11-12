import dataclasses

import pytest

from srim.core.isotopes import Isotope, find_isotope, isotope_label, stable_isotopes


def test_silicon_has_three_stable_isotopes_most_abundant_first():
    isotopes = stable_isotopes("Si")
    assert [i.mass_number for i in isotopes] == [28, 29, 30]
    assert isotopes[0].mass_amu == pytest.approx(27.9769265, abs=1e-6)
    assert isotope_label(isotopes[0]) == "Si-28"


def test_abundances_add_up_to_100_percent():
    for symbol in ("H", "C", "Si", "Fe", "Au", "U"):
        total = sum(i.abundance_percent for i in stable_isotopes(symbol))
        assert total == pytest.approx(100.0, abs=0.1)


def test_an_element_without_natural_isotopes_has_none():
    assert stable_isotopes("Tc") == ()


def test_unknown_symbol_is_refused():
    with pytest.raises(ValueError):
        stable_isotopes("Xx")


def test_a_name_is_not_a_symbol():
    with pytest.raises(ValueError):
        stable_isotopes("Silicon")


def test_find_isotope_matches_the_mass():
    assert find_isotope("Si", 28.9764947).mass_number == 29
    assert find_isotope("Si", 28.0855) is None


def test_isotope_is_a_frozen_dataclass():
    isotope = stable_isotopes("Si")[0]
    assert isinstance(isotope, Isotope)
    with pytest.raises(dataclasses.FrozenInstanceError):
        isotope.mass_number = 1


def test_isotope_symbols_match_the_element():
    assert all(i.symbol == "Cu" for i in stable_isotopes("Cu"))
