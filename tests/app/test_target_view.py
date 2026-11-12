"""The cross-section drawing beside the Geometry tab: clicks, selection both ways and tooltips.

The widget needs the offscreen platform (conftest) and skips without PySide6 or pytest-qt.
"""

import pytest

pytest.importorskip("PySide6")
pytest.importorskip("pytestqt")

from PySide6.QtCore import QPoint, Qt  # noqa: E402
from PySide6.QtTest import QTest  # noqa: E402

from srim.app.document import DocumentModel  # noqa: E402
from srim.app.drawing import layer_boxes  # noqa: E402
from srim.app.geometry import GeometryTab  # noqa: E402
from srim.app.target_view import TargetView  # noqa: E402


def set_target(document, layers, angle=None):
    def apply(doc):
        doc["target"] = [dict(layer) for layer in layers]
        if angle is not None:
            doc["ion"]["angle_deg"] = angle

    document.edit(apply)


def layer(name, thickness, symbol="Au"):
    return {
        "name": name,
        "thickness_nm": thickness,
        "density_g_cm3": 19.3,
        "phase": 0,
        "elements": [{"symbol": symbol, "stoich": 1.0}],
        "_unit": "nm",
    }


@pytest.fixture
def document(qtbot):
    return DocumentModel()


@pytest.fixture
def view(qtbot, document):
    widget = TargetView(document)
    widget.resize(420, 140)
    qtbot.addWidget(widget)
    return widget


@pytest.fixture
def tab(qtbot, document):
    widget = GeometryTab(document)
    widget.resize(900, 300)
    qtbot.addWidget(widget)
    return widget


def centre_x(view, row, thicknesses):
    """The x (widget pixels) at the middle of a layer's drawn box."""
    x0, width = view.target_span()
    box = layer_boxes(thicknesses, width)[row]
    return x0 + box.left + box.width // 2


def test_layer_at_returns_the_row_under_each_box(view, document):
    set_target(document, [layer("a", 100.0), layer("b", 10.0), layer("c", 50.0)])
    thicknesses = [100.0, 10.0, 50.0]
    for row in range(3):
        assert view.layer_at(centre_x(view, row, thicknesses)) == row


def test_layer_at_is_minus_one_outside_the_target(view, document):
    set_target(document, [layer("a", 100.0)])
    x0, width = view.target_span()
    assert view.layer_at(0) == -1  # the margin where the beam comes in
    assert view.layer_at(x0 + width + 50) == -1
    assert view.layer_at(-5) == -1


def test_a_thin_layer_next_to_a_thick_one_is_clickable(view, document):
    set_target(document, [layer("slab", 1e7), layer("film", 10.0)])
    x0, width = view.target_span()
    last = x0 + width - 1  # the thin layer is the last one, its box is at the right edge
    assert view.layer_at(last) == 1
    assert view.layer_at(x0) == 0


def test_no_layers_paints_and_has_no_layer(view):
    assert view.grab().width() == 420
    assert view.layer_at(200) == -1
    assert view.tooltip_at(200) == ""


def test_the_tooltip_of_a_thin_layer_names_the_drawing_as_wider(view, document):
    set_target(document, [layer("slab", 1e7), layer("film", 10.0)])
    x0, width = view.target_span()
    assert view.tooltip_at(x0 + width - 1).endswith("(drawn wider than to scale)")
    assert not view.tooltip_at(x0).endswith("(drawn wider than to scale)")


def test_a_click_on_a_layer_emits_its_row(view, document, qtbot):
    set_target(document, [layer("a", 100.0), layer("b", 100.0)])
    thicknesses = [100.0, 100.0]
    point = QPoint(centre_x(view, 1, thicknesses), view.height() // 2)
    with qtbot.waitSignal(view.layer_clicked, timeout=1000) as signal:
        QTest.mouseClick(view, Qt.MouseButton.LeftButton, pos=point)
    assert signal.args == [1]


def test_a_click_outside_the_layers_emits_nothing(view, document, qtbot):
    set_target(document, [layer("a", 100.0)])
    with qtbot.assertNotEmitted(view.layer_clicked):
        QTest.mouseClick(view, Qt.MouseButton.LeftButton, pos=QPoint(2, 2))


def test_set_selected_marks_the_row(view, document):
    set_target(document, [layer("a", 100.0), layer("b", 100.0)])
    view.set_selected(1)
    assert view.selected == 1
    view.set_selected(-1)
    assert view.selected == -1


def test_a_table_selection_reaches_the_drawing(tab, document):
    set_target(document, [layer("a", 100.0), layer("b", 100.0)])
    tab.select_layer(1)
    assert tab.target_view.selected == 1


def test_a_click_in_the_drawing_selects_the_table_row(tab, document, qtbot):
    set_target(document, [layer("a", 100.0), layer("b", 100.0)])
    view = tab.target_view
    view.resize(420, 140)
    point = QPoint(centre_x(view, 0, [100.0, 100.0]), view.height() // 2)
    with qtbot.waitSignal(tab.selected_layer, timeout=1000) as signal:
        QTest.mouseClick(view, Qt.MouseButton.LeftButton, pos=point)
    assert signal.args == [0]
    assert tab.table.currentIndex().row() == 0
    assert tab.table.selectionModel().isRowSelected(0, tab.table.rootIndex())


def test_a_drawing_repaints_when_the_document_changes(view, document):
    set_target(document, [layer("a", 100.0)])
    assert view.grab().width() == 420
    set_target(document, [layer("a", 100.0), layer("b", 1.0), layer("c", 2.0)])
    assert view.layer_at(view.target_span()[0] + 1) == 0
    assert view.grab().width() == 420
