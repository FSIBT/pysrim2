"""The Save results dialog and the File menu item (needs PySide6 and pytest-qt)."""

import pytest

pytest.importorskip("PySide6")
pytest.importorskip("pytestqt")

from PySide6.QtWidgets import QMessageBox  # noqa: E402
from test_export_results import single_run  # noqa: E402

from srim.app import export_results  # noqa: E402
from srim.app.export_results import ExportRequest  # noqa: E402
from srim.app.save_dialog import SaveResultsDialog  # noqa: E402
from srim.app.settings import AppSettings  # noqa: E402
from srim.app.window import MainWindow  # noqa: E402


def make_run(folder):
    single_run(folder)
    return folder


def test_a_format_without_its_extra_is_greyed_with_the_reason(qtbot, tmp_path, monkeypatch):
    run = make_run(tmp_path / "run")
    monkeypatch.setattr(
        export_results,
        "format_available",
        lambda fmt: "needs the hdf5 extra (tables)" if fmt == "hdf5" else None,
    )
    dialog = SaveResultsDialog(run)
    qtbot.addWidget(dialog)
    hdf5 = dialog.format_boxes["hdf5"]
    assert not hdf5.isEnabled()
    assert hdf5.toolTip() == "needs the hdf5 extra (tables)"
    assert not hdf5.isChecked()
    assert dialog.format_boxes["csv"].isEnabled()


def test_save_asks_for_the_request_the_boxes_say(qtbot, tmp_path, monkeypatch):
    run = make_run(tmp_path / "run")
    monkeypatch.setattr(export_results, "format_available", lambda fmt: None)
    dialog = SaveResultsDialog(run)
    qtbot.addWidget(dialog)
    dialog.folder_edit.setText(str(tmp_path / "out"))
    dialog.name_edit.setText("boron")
    dialog.format_boxes["parquet"].setChecked(False)
    dialog.format_boxes["csv"].setChecked(True)
    dialog.format_boxes["raw"].setChecked(False)
    dialog.collision_box.setChecked(True)
    request = dialog.request()
    assert request == ExportRequest(
        tmp_path / "out", "boron", ("hdf5", "csv"), collision=True, points=None
    )


def test_save_writes_the_files_and_reports_them(qtbot, tmp_path, monkeypatch):
    run = make_run(tmp_path / "run")
    monkeypatch.setattr(export_results, "format_available", lambda fmt: None)
    dialog = SaveResultsDialog(run)
    qtbot.addWidget(dialog)
    out = tmp_path / "out"
    dialog.folder_edit.setText(str(out))
    dialog.name_edit.setText("boron")
    for name, box in dialog.format_boxes.items():
        box.setChecked(name == "csv")
    dialog.save()
    qtbot.waitUntil(lambda: dialog.message is not None or dialog.failure is not None, timeout=20000)
    assert dialog.failure is None
    assert (out / "boron-csv" / "ioniz.csv").is_file()
    assert dialog.message.startswith("Saved ")
    assert str(out) in dialog.message


def test_an_existing_name_asks_and_keeps_the_files_on_no(qtbot, tmp_path, monkeypatch):
    run = make_run(tmp_path / "run")
    monkeypatch.setattr(export_results, "format_available", lambda fmt: None)
    out = tmp_path / "out"
    export_results.export_run(run, ExportRequest(out, "boron", ("csv",)))
    asked = []

    def answer_no(*args, **kwargs):
        asked.append(True)
        return QMessageBox.StandardButton.No

    monkeypatch.setattr(QMessageBox, "question", staticmethod(answer_no))
    dialog = SaveResultsDialog(run)
    qtbot.addWidget(dialog)
    dialog.folder_edit.setText(str(out))
    dialog.name_edit.setText("boron")
    for name, box in dialog.format_boxes.items():
        box.setChecked(name == "csv")
    dialog.save()
    assert asked
    assert dialog.message is None
    assert (out / "boron-csv" / "ioniz.csv").is_file()


def test_the_window_has_save_results_enabled_only_with_a_result(qtbot, tmp_path):
    window = MainWindow(AppSettings(route="wine"), checks_fn=lambda route: [])
    qtbot.addWidget(window)
    assert not window.save_results_action.isEnabled()
    window.run.result = object()
    window.run.folder = tmp_path
    window._update_run_actions()
    assert window.save_results_action.isEnabled()


def test_file_save_results_saves_and_reports_in_the_status_bar(qtbot, tmp_path, monkeypatch):
    from PySide6.QtWidgets import QDialog

    result = single_run(tmp_path / "run")
    monkeypatch.setattr(export_results, "format_available", lambda fmt: None)
    window = MainWindow(AppSettings(route="wine"), checks_fn=lambda route: [])
    qtbot.addWidget(window)
    window.run.result = result
    window.run.folder = tmp_path / "run"
    window._update_run_actions()
    out = tmp_path / "out"

    def fill_and_save(dialog):
        dialog.folder_edit.setText(str(out))
        dialog.name_edit.setText("boron")
        for name, box in dialog.format_boxes.items():
            box.setChecked(name == "csv")
        dialog.save()
        qtbot.waitUntil(lambda: dialog.message is not None, timeout=20000)
        return QDialog.DialogCode.Accepted

    monkeypatch.setattr(SaveResultsDialog, "exec", fill_and_save)
    window.save_results()
    assert (out / "boron-csv" / "ioniz.csv").is_file()
    assert window._setup_messages[0].text.startswith("Saved ")
    assert str(out) in window._setup_messages[0].text


def test_the_default_folder_is_next_to_the_setup_not_in_the_runs_folder(qtbot, tmp_path):
    run = make_run(tmp_path / "setup-runs" / "20260101T000000Z")
    dialog = SaveResultsDialog(run)
    qtbot.addWidget(dialog)
    assert dialog.folder_edit.text() == str(tmp_path)


def test_replace_removes_exactly_the_targets(qtbot, tmp_path, monkeypatch):
    run = make_run(tmp_path / "run")
    monkeypatch.setattr(export_results, "format_available", lambda fmt: None)
    out = tmp_path / "out"
    export_results.export_run(run, ExportRequest(out, "boron", ("csv",)))
    export_results.export_run(run, ExportRequest(out, "other", ("csv",)))
    (out / "boron-csv" / "stale.txt").write_text("old")
    (out / "boron-other.txt").write_text("unrelated")
    monkeypatch.setattr(
        QMessageBox, "question", staticmethod(lambda *a, **k: QMessageBox.StandardButton.Yes)
    )
    dialog = SaveResultsDialog(run)
    qtbot.addWidget(dialog)
    dialog.folder_edit.setText(str(out))
    dialog.name_edit.setText("boron")
    for name, box in dialog.format_boxes.items():
        box.setChecked(name == "csv")
    dialog.save()
    qtbot.waitUntil(lambda: dialog.message is not None or dialog.failure is not None, timeout=20000)
    assert dialog.failure is None
    assert not (out / "boron-csv" / "stale.txt").exists()
    assert (out / "boron-csv" / "ioniz.csv").is_file()
    assert (out / "boron-other.txt").read_text() == "unrelated"
    assert (out / "other-csv" / "ioniz.csv").is_file()


def test_a_failed_save_shows_the_failure_and_stays_open(qtbot, tmp_path, monkeypatch):
    from srim import export

    run = make_run(tmp_path / "run")
    monkeypatch.setattr(export_results, "format_available", lambda fmt: None)
    real = export.results_to_csv

    def failing(results, directory, **kwargs):
        raise OSError("disk full")

    monkeypatch.setattr(export, "results_to_csv", failing)
    out = tmp_path / "out"
    dialog = SaveResultsDialog(run)
    qtbot.addWidget(dialog)
    dialog.folder_edit.setText(str(out))
    dialog.name_edit.setText("boron")
    for name, box in dialog.format_boxes.items():
        box.setChecked(name == "csv")
    dialog.save()
    qtbot.waitUntil(lambda: dialog.failure is not None, timeout=20000)
    assert "disk full" in dialog.failure
    assert dialog.message is None
    assert dialog.result() == 0  # not accepted: the dialog stays open
    assert dialog.save_button.isEnabled()
    assert "Not saved" in dialog.status_label.text()
    assert not out.exists() or not any(out.iterdir())
    monkeypatch.setattr(export, "results_to_csv", real)


def test_a_folder_inside_the_run_folder_is_refused_before_anything_is_removed(
    qtbot, tmp_path, monkeypatch
):
    run = make_run(tmp_path / "run")
    monkeypatch.setattr(export_results, "format_available", lambda fmt: None)
    (run / "boron-run.yaml").write_text("keep me")
    dialog = SaveResultsDialog(run)
    qtbot.addWidget(dialog)
    dialog.folder_edit.setText(str(run))
    dialog.name_edit.setText("boron")
    for name, box in dialog.format_boxes.items():
        box.setChecked(name == "csv")
    dialog.save()
    assert "outside the run folder" in dialog.status_label.text()
    assert (run / "boron-run.yaml").read_text() == "keep me"
    assert dialog.message is None
