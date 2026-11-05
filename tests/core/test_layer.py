from srim.core.element import Element
from srim.core.layer import Layer


def test_layer_init_simple():
    layer = Layer.from_formula("Ni99Fe1", density=1.0, width=1.0, phase=0, name="layer 1")
    assert layer.name == "layer 1"
    assert layer.density == 1.0
    assert layer.width == 1.0
    assert layer.phase == 0
    assert len(layer.elements) == 2
    assert Element("Ni") in layer.elements
    assert Element("Fe") in layer.elements


def test_unnamed_layer_is_named_after_its_elements():
    from srim import Layer

    elements = {
        "Si": {"stoich": 0.5, "E_d": 35.0, "lattice": 0.0, "surface": 3.0},
        "C": {"stoich": 0.5, "E_d": 20.0, "lattice": 0.0, "surface": 3.0},
    }
    assert Layer(elements, 3.21, 1000.0).name == "Si+C"
    assert Layer(elements, 3.21, 1000.0, name=None).name == "Si+C"
    assert Layer.from_formula("Au", 19.3, 10.0).name == "Au"
    assert Layer.from_formula("SiC", 3.21, 10.0).name == "SiC"
    assert Layer.from_formula("SiC", 3.21, 10.0, name="film").name == "film"
