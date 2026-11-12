"""A value typed but not yet committed (no Enter, no focus change) reaches Save, Undo and friends."""

import pytest

pytest.importorskip("PySide6")
pytest.importorskip("pytestqt")

from PySide6.QtWidgets import QApplication, QLineEdit, QSpinBox  # noqa: E402

from srim.app.settings import AppSettings  # noqa: E402
from srim.app.window import MainWindow  # noqa: E402
from srim.spec import RunSpec  # noqa: E402


def shown_window(qtbot, document=None):
    win = MainWindow(AppSettings(route="wine"), checks_fn=lambda route: [], document=document)
    qtbot.addWidget(win)
    win.resize(1000, 800)
    win.show()
    qtbot.waitExposed(win)
    # Offscreen, a window gets keyboard focus only once it is the active window.
    win.activateWindow()
    qtbot.waitUntil(win.isActiveWindow, timeout=2000)
    return win


def focus(qtbot, widget):
    widget.setFocus()
    qtbot.waitUntil(lambda: QApplication.focusWidget() is widget, timeout=2000)


def type_without_enter(qtbot, widget, text):
    """Select the field's text and type ``text`` into it, without pressing Enter."""
    focus(qtbot, widget)
    widget.selectAll() if isinstance(widget, QLineEdit) else widget.lineEdit().selectAll()
    qtbot.keyClicks(QApplication.focusWidget(), text)


def test_typing_into_a_spin_box_then_saving_keeps_the_typed_value(
    qtbot, boron_data, write_setup, tmp_path
):
    path = write_setup(tmp_path / "run.yaml", boron_data)
    win = shown_window(qtbot)
    win.document.open(path)
    spin = win.editors["beam"].number_ions
    type_without_enter(qtbot, spin, "350")
    assert spin.value() == 200  # not committed yet: the spin box has not seen the value
    win.save_action.trigger()
    assert RunSpec.from_yaml(path).to_dict()["number_ions"] == 350


def test_typing_into_a_geometry_cell_then_saving_keeps_the_typed_value(
    qtbot, boron_data, write_setup, tmp_path
):
    path = write_setup(tmp_path / "run.yaml", boron_data)
    win = shown_window(qtbot)
    win.document.open(path)
    geometry = win.editors["geometry"]
    thickness = geometry.model.index(0, geometry.model.COLUMNS.index("thickness"))
    geometry.table.setCurrentIndex(thickness)
    geometry.table.edit(thickness)
    qtbot.waitUntil(lambda: isinstance(QApplication.focusWidget(), QLineEdit), timeout=2000)
    editor = QApplication.focusWidget()
    editor.selectAll()
    qtbot.keyClicks(editor, "250")
    win.save_action.trigger()
    assert RunSpec.from_yaml(path).to_dict()["target"][0]["thickness_nm"] == pytest.approx(250)


def test_undo_commits_the_typed_value_first_and_then_reverts_it(
    qtbot, boron_data, write_setup, tmp_path
):
    path = write_setup(tmp_path / "run.yaml", boron_data)
    win = shown_window(qtbot)
    win.document.open(path)
    win.document.edit(lambda d: d.__setitem__("seed", 5))  # the edit undo should return to
    spin = win.editors["beam"].number_ions
    type_without_enter(qtbot, spin, "350")
    win.undo_action.trigger()
    # The typed 350 was committed as an edit of its own, and undo reverted that one: the seed
    # edit is still there, and the number of ions is back to the value before the typing.
    assert win.document.doc["number_ions"] == 200
    assert win.document.doc["seed"] == 5
    assert spin.value() == 200


def test_new_counts_a_typed_value_as_an_unsaved_edit(
    qtbot, boron_data, write_setup, tmp_path, monkeypatch
):
    from PySide6.QtWidgets import QMessageBox

    path = write_setup(tmp_path / "run.yaml", boron_data)
    win = shown_window(qtbot)
    win.document.open(path)
    asked = []

    def question(parent, title, text, *args, **kwargs):
        asked.append(text)
        return QMessageBox.StandardButton.No

    monkeypatch.setattr(QMessageBox, "question", question)
    type_without_enter(qtbot, win.editors["beam"].number_ions, "350")
    win.new_action.trigger()
    assert asked, "the typed value is an unsaved edit, so New asks"
    assert win.document.doc["number_ions"] == 350
