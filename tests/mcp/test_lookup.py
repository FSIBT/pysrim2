"""Looking up elements, isotopes and materials for the agent (Qt-free)."""

import pytest

from srim.app.materials import UserMaterial, save_user_material
from srim.app.model import new_element
from srim.mcp.backend import BackendError
from srim.mcp.lookup import find_material, lookup_element


def test_n15_is_found_in_every_spelling():
    for text in ("N15", "15N", "N-15"):
        found = lookup_element(text)
        assert found["symbol"] == "N" and round(found["mass_amu"]) == 15


def test_an_element_without_an_isotope_number_has_its_natural_mass():
    found = lookup_element("N")
    assert found["symbol"] == "N" and found["z"] == 7
    assert found["mass_amu"] == pytest.approx(14.0067, abs=1e-3)
    labels = [i["label"] for i in found["isotopes"]]
    assert labels == ["N-14", "N-15"]  # the most abundant first
    assert found["isotopes"][0]["abundance"] == pytest.approx(99.63, abs=0.01)


def test_an_isotope_that_is_not_natural_says_so():
    with pytest.raises(BackendError, match="N-13"):
        lookup_element("N13")


def test_sio2_has_a_density():
    found = find_material("SiO2")
    assert found and found[0]["density_g_cm3"] > 2
    assert {e["symbol"] for e in found[0]["elements"]} == {"Si", "O"}
    assert found[0]["source"] == "predefined"


def test_a_material_is_found_by_name_in_any_case():
    found = find_material("silicon dioxide")
    assert found and found[0]["formula"] == "SiO2"
    assert find_material("KAPTON")[0]["formula"] == "C22H10N2O5"


def test_an_unknown_formula_asks_for_a_density():
    found = find_material("Ga2O3Xx")  # not a formula: nothing
    assert found == []
    guess = find_material("Hf3N4")  # a formula without a known compound
    assert guess == [
        {
            "formula": "Hf3N4",
            "elements": [{"symbol": "Hf", "stoich": 3.0}, {"symbol": "N", "stoich": 4.0}],
            "density_g_cm3": None,
            "name": None,
            "source": "formula",
        }
    ]


def test_text_that_is_not_a_formula_or_a_name_finds_nothing():
    assert find_material("a shiny metal") == []
    assert find_material("") == []


def test_a_user_material_is_found_by_name(tmp_path):
    path = tmp_path / "materials.json"
    save_user_material(
        UserMaterial("My foil", 8.5, 0, [new_element("Cu")]),
        path,
    )
    found = find_material("my foil", materials_path=path)
    assert found == [
        {
            "name": "My foil",
            "formula": None,
            "density_g_cm3": 8.5,
            "elements": [{"symbol": "Cu", "stoich": 1.0}],
            "source": "user",
        }
    ]


def test_an_unknown_element_says_so():
    with pytest.raises(BackendError, match="Xx"):
        lookup_element("Xx")
