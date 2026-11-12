import pytest

from srim.core.compounds import Compound, formula_elements, predefined_compounds


def test_the_list_starts_with_silicon_and_holds_kapton():
    compounds = predefined_compounds()
    assert compounds[0] == Compound("Silicon", "Si", 2.3212, 0)
    kapton = next(c for c in compounds if c.name == "Kapton")
    assert formula_elements(kapton.formula) == [("C", 22.0), ("H", 10.0), ("N", 2.0), ("O", 5.0)]


def test_air_is_a_gas():
    assert next(c for c in predefined_compounds() if c.name == "Air (dry)").phase == 1


def test_every_compound_formula_parses():
    for compound in predefined_compounds():
        assert formula_elements(compound.formula)


def test_bad_formulas_are_refused():
    for formula in ("si", "SiSi", "Xx2", ""):
        with pytest.raises(ValueError):
            formula_elements(formula)


def test_ratios_are_floats_and_default_to_one():
    assert formula_elements("SiO2") == [("Si", 1.0), ("O", 2.0)]
    assert formula_elements("Fe0.1Al.9") == [("Fe", 0.1), ("Al", 0.9)]
    assert all(isinstance(ratio, float) for _, ratio in formula_elements("Ni99Fe1"))


def test_a_repeated_element_is_refused():
    with pytest.raises(ValueError):
        formula_elements("Fe2O3Fe")


def test_compound_is_a_frozen_dataclass_with_phase_default():
    compound = Compound("Gold", "Au", 19.32)
    assert compound.phase == 0
    with pytest.raises(AttributeError):
        compound.name = "Silver"
