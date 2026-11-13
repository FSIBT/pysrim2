"""The dialog that offers a long run in parallel: its text, its defaults and its choice."""

import math

import pytest

pytest.importorskip("PySide6")
pytest.importorskip("pytestqt")

from PySide6.QtWidgets import QDialog  # noqa: E402

from srim.app.parallel_dialog import ParallelDialog  # noqa: E402
from srim.app.runs import Advice, ParallelChoice  # noqa: E402

LONG = Advice(
    seconds=2 * 3600 + 5,
    units=3,
    can_split=True,
    offer_container=True,
    default_workers=3,
)


@pytest.fixture
def make_dialog(qtbot):
    def make(advice=LONG, cores=4, ions=1000):
        dialog = ParallelDialog(advice, cores=cores, ions=ions)
        qtbot.addWidget(dialog)
        return dialog

    return make


def test_the_text_gives_the_time_and_the_units(make_dialog):
    dialog = make_dialog()
    text = dialog.message.text()
    assert text == (
        "This run may take about 2:00:05 (3 units of work). Run it in parallel?"
    )


def test_the_workers_start_at_the_default_and_stay_between_one_and_the_cores(make_dialog):
    dialog = make_dialog(cores=4)
    assert dialog.workers_box.value() == 3
    assert dialog.workers_box.minimum() == 1
    assert dialog.workers_box.maximum() == 4


def test_the_default_choice_is_the_default_workers_with_nothing_else(make_dialog):
    dialog = make_dialog()
    assert dialog.choice() == ParallelChoice(workers=3, batch_ions=None, container=False)


@pytest.mark.parametrize("split", [False, True])
@pytest.mark.parametrize("container", [False, True])
def test_each_check_box_combination_is_the_choice(make_dialog, split, container):
    dialog = make_dialog(ions=1000)
    dialog.split_box.setChecked(split)
    dialog.container_box.setChecked(container)
    choice = dialog.choice()
    assert choice.workers == 3
    assert choice.batch_ions == (math.ceil(1000 / 3) if split else None)
    assert choice.container is container


def test_the_batch_size_defaults_to_the_ions_over_the_workers(make_dialog):
    dialog = make_dialog(ions=1000)
    assert dialog.batch_box.value() == 334
    dialog.split_box.setChecked(True)
    assert dialog.batch_box.isEnabled()
    assert "different random numbers" in dialog.note.text()


def test_a_run_that_cannot_split_has_no_split_box(make_dialog):
    dialog = make_dialog(advice=Advice(3600.0, 1, False, False, 2))
    assert dialog.split_box.isHidden()
    assert dialog.container_box.isHidden()
    assert dialog.choice().batch_ions is None
    assert dialog.choice().container is False


def test_the_container_box_appears_when_it_is_offered(make_dialog):
    dialog = make_dialog()
    assert not dialog.container_box.isHidden()
    assert dialog.container_box.text() == "Run in containers"


def test_run_as_one_chooses_one_worker_and_no_split(make_dialog):
    dialog = make_dialog()
    dialog.split_box.setChecked(True)
    dialog.as_one_button.click()
    assert dialog.result() == QDialog.DialogCode.Accepted
    assert dialog.choice() == ParallelChoice(workers=1, batch_ions=None, container=False)


def test_run_in_parallel_keeps_the_boxes(make_dialog):
    dialog = make_dialog()
    dialog.split_box.setChecked(True)
    dialog.run_button.click()
    assert dialog.result() == QDialog.DialogCode.Accepted
    assert dialog.choice().batch_ions == 334


def test_cancel_rejects_the_dialog(make_dialog):
    dialog = make_dialog()
    dialog.cancel_button.click()
    assert dialog.result() == QDialog.DialogCode.Rejected
