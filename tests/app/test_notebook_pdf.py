"""The notebook PDF export: the commands, the export and its failures (Qt-free), and the window's
Export PDF… entries (Qt; skipped without the app extra and pytest-qt).

The export runs ``fake_pdf_export.py`` in place of marimo or nbconvert, so no notebook tool or
browser is started. The Qt tests use the notebook server and view fakes of test_notebook_tab.
"""

import importlib.util
import sys
import threading
import time
from functools import partial
from pathlib import Path

import pytest

from srim.app import notebooks
from srim.app.notebooks import (
    REINSTALL_TEXT,
    NotebookServerError,
    export_pdf,
    pdf_command,
    pdf_problem,
)

FAKE = Path(__file__).with_name("fake_pdf_export.py")
QT = importlib.util.find_spec("PySide6") is not None and importlib.util.find_spec("pytestqt")
needs_qt = pytest.mark.skipif(not QT, reason="needs the app extra and pytest-qt")


def fake(mode):
    """A ``command_for`` that runs the fake export in ``mode`` and writes to the given file."""

    def command_for(kind, path, out):
        return [sys.executable, str(FAKE), mode, str(out)]

    return command_for


def test_pdf_command_for_both_kinds(tmp_path):
    folder = tmp_path / "run"
    out = tmp_path / "out.pdf"
    marimo = folder / "notebook.py"
    assert pdf_command("marimo", marimo, out) == [
        sys.executable,
        "-m",
        "marimo",
        "export",
        "pdf",
        str(marimo),
        "-o",
        str(out),
    ]
    jupyter = folder / "notebook.ipynb"
    assert pdf_command("jupyter", jupyter, out) == [
        sys.executable,
        "-m",
        "nbconvert",
        "--to",
        "webpdf",
        "--WebPDFExporter.allow_chromium_download=True",
        "--output",
        "out",
        "--output-dir",
        str(tmp_path),
        str(jupyter),
    ]


def test_export_pdf_returns_the_file(tmp_path):
    out = tmp_path / "notebook.pdf"
    written = export_pdf("marimo", tmp_path / "notebook.py", out, command_for=fake("write"))
    assert written == out
    assert out.read_bytes() == b"%PDF-1.4 fake\n"
    # the temporary file is gone: only the PDF is left next to it
    assert sorted(p.name for p in tmp_path.iterdir()) == ["notebook.pdf"]


def test_a_failing_export_raises_with_the_log_tail(tmp_path):
    out = tmp_path / "notebook.pdf"
    with pytest.raises(NotebookServerError) as info:
        export_pdf("marimo", tmp_path / "notebook.py", out, command_for=fake("boom"))
    message = str(info.value)
    assert "exited" not in message
    assert "failed with code 1" in message
    assert "boom: the export broke" in message
    assert not out.exists()
    assert sorted(p.name for p in tmp_path.iterdir()) == []


def test_a_failed_export_keeps_an_existing_pdf(tmp_path):
    out = tmp_path / "notebook.pdf"
    out.write_bytes(b"old")
    with pytest.raises(NotebookServerError):
        export_pdf("marimo", tmp_path / "notebook.py", out, command_for=fake("boom"))
    assert out.read_bytes() == b"old"


def test_a_missing_browser_says_to_reinstall_first(tmp_path):
    out = tmp_path / "notebook.pdf"
    with pytest.raises(NotebookServerError) as info:
        export_pdf("jupyter", tmp_path / "notebook.ipynb", out, command_for=fake("fail"))
    message = str(info.value)
    assert message.startswith(REINSTALL_TEXT)
    assert "Executable doesn't exist" in message  # the log tail follows the hint


def test_a_command_that_writes_no_file_is_a_failure(tmp_path):
    out = tmp_path / "notebook.pdf"
    with pytest.raises(NotebookServerError, match="wrote no file"):
        export_pdf("marimo", tmp_path / "notebook.py", out, command_for=fake("nothing"))
    assert not out.exists()


def test_an_export_that_takes_too_long_is_killed(tmp_path, monkeypatch):
    monkeypatch.setattr(notebooks, "PDF_SECONDS", 1)
    started = time.monotonic()
    with pytest.raises(NotebookServerError, match="did not finish within 1 s"):
        export_pdf("marimo", tmp_path / "notebook.py", tmp_path / "n.pdf", command_for=fake("hang"))
    assert time.monotonic() - started < 30


def test_export_is_greyed_with_the_reinstall_hint_when_nbconvert_is_missing(monkeypatch):
    monkeypatch.setattr(notebooks, "chromium_available", lambda: True)
    monkeypatch.setattr(notebooks, "_installed", lambda name: name != "nbconvert")
    assert pdf_problem() == REINSTALL_TEXT


def test_export_needs_the_chromium_download_too(monkeypatch):
    monkeypatch.setattr(notebooks, "_installed", lambda name: True)
    monkeypatch.setattr(notebooks, "chromium_available", lambda: False)
    assert pdf_problem() == REINSTALL_TEXT
    monkeypatch.setattr(notebooks, "chromium_available", lambda: True)
    assert pdf_problem() is None


# --- the window (Qt) -------------------------------------------------------------------


def _tab_helpers():
    """test_notebook_tab's helpers (its module needs Qt, so it is imported only here)."""
    import test_notebook_tab

    return test_notebook_tab


def _ready_notebook(monkeypatch):
    """WebEngine and the tools count as installed, the view and the server are the fakes."""
    from srim.app import notebook_tab

    helpers = _tab_helpers()
    monkeypatch.setattr(notebook_tab, "webengine_available", lambda: True)
    monkeypatch.setattr(notebook_tab, "tool_problem", lambda kind: None)
    monkeypatch.setattr(notebook_tab, "_new_webengine_view", helpers.FakeView)
    helpers.use_server(monkeypatch)


def _export_window(qtbot, tmp_path, monkeypatch, mode="write"):
    """A window with a marimo notebook running in a finished run folder; PDF exports are
    available and run the fake export in ``mode``."""
    from srim.app import window as window_module

    helpers = _tab_helpers()
    _ready_notebook(monkeypatch)
    monkeypatch.setattr(window_module, "pdf_problem", lambda: None)
    monkeypatch.setattr(window_module, "export_pdf", partial(export_pdf, command_for=fake(mode)))
    win = helpers.window_of(qtbot)
    folder, _scan = helpers.run_folder(tmp_path, [helpers.DONE, helpers.DONE])
    helpers.open_folder(qtbot, win, folder)
    helpers.start_and_wait(qtbot, win, win.start_marimo_action)
    return win, folder


class FakeDialog:
    """Stands in for QFileDialog: it answers with ``chosen`` and records the default it was given."""

    chosen = None
    defaults = []

    @staticmethod
    def getSaveFileName(parent, caption, directory, filters):  # noqa: N802 (Qt's name)
        FakeDialog.defaults.append(directory)
        return (str(FakeDialog.chosen) if FakeDialog.chosen else "", "PDF files (*.pdf)")


@pytest.fixture
def dialog(monkeypatch):
    from srim.app import window as window_module

    FakeDialog.chosen = None
    FakeDialog.defaults = []
    monkeypatch.setattr(window_module, "QFileDialog", FakeDialog)
    return FakeDialog


@needs_qt
def test_export_from_the_tab_writes_the_chosen_file(qtbot, tmp_path, monkeypatch, dialog):
    win, folder = _export_window(qtbot, tmp_path, monkeypatch)
    chosen = tmp_path / "chosen.pdf"
    dialog.chosen = chosen
    win.notebook_tab.pdf_button.click()
    qtbot.waitUntil(lambda: chosen.is_file() and not win._pdf_busy, timeout=30_000)
    assert chosen.read_bytes() == b"%PDF-1.4 fake\n"
    assert dialog.defaults == [str(folder / "notebook.pdf")]
    assert str(chosen) in win._notebook_message.text


@needs_qt
def test_a_chosen_name_without_pdf_gets_the_extension(qtbot, tmp_path, monkeypatch, dialog):
    win, _folder = _export_window(qtbot, tmp_path, monkeypatch)
    dialog.chosen = tmp_path / "chosen"
    win.export_pdf_action.trigger()
    qtbot.waitUntil(lambda: (tmp_path / "chosen.pdf").is_file(), timeout=30_000)


@needs_qt
def test_a_cancelled_dialog_exports_nothing(qtbot, tmp_path, monkeypatch, dialog):
    win, _folder = _export_window(qtbot, tmp_path, monkeypatch)
    dialog.chosen = None
    win.export_pdf_action.trigger()
    assert not win._pdf_busy
    assert win.export_pdf_action.isEnabled()
    assert list(tmp_path.glob("*.pdf")) == []


@needs_qt
def test_the_export_entries_are_greyed_while_one_runs(qtbot, tmp_path, monkeypatch, dialog):
    from srim.app import window as window_module

    win, _folder = _export_window(qtbot, tmp_path, monkeypatch)
    gate = threading.Event()

    def slow(kind, path, out):
        gate.wait(30)
        return export_pdf(kind, path, out, command_for=fake("write"))

    monkeypatch.setattr(window_module, "export_pdf", slow)
    dialog.chosen = tmp_path / "slow.pdf"
    win.export_pdf_action.trigger()
    assert not win.export_pdf_action.isEnabled()
    assert not win.notebook_tab.pdf_button.isEnabled()
    assert win.export_pdf_action.toolTip() == "Exporting the notebook to PDF…"
    gate.set()
    qtbot.waitUntil(lambda: not win._pdf_busy, timeout=30_000)
    assert win.export_pdf_action.isEnabled()
    assert win.notebook_tab.pdf_button.isEnabled()


@needs_qt
def test_a_failed_export_is_reported_in_the_status_bar(qtbot, tmp_path, monkeypatch, dialog):
    from srim.app.notebook_tab import EXPORT_FAILED_TEXT

    win, _folder = _export_window(qtbot, tmp_path, monkeypatch, mode="boom")
    dialog.chosen = tmp_path / "bad.pdf"
    win.notebook_tab.pdf_button.click()
    qtbot.waitUntil(lambda: not win._pdf_busy, timeout=30_000)
    text = win._notebook_message.text
    assert text.startswith(EXPORT_FAILED_TEXT)
    assert "boom: the export broke" in text
    assert not (tmp_path / "bad.pdf").exists()
    assert win.export_pdf_action.isEnabled()


@needs_qt
def test_a_result_after_the_window_closed_is_ignored(qtbot, tmp_path, monkeypatch, dialog):
    from srim.app import window as window_module
    from srim.app.notebook_tab import EXPORTING_TEXT

    win, _folder = _export_window(qtbot, tmp_path, monkeypatch)
    gate = threading.Event()

    def slow(kind, path, out):
        gate.wait(30)
        return export_pdf(kind, path, out, command_for=fake("boom"))

    monkeypatch.setattr(window_module, "export_pdf", slow)
    dialog.chosen = tmp_path / "late.pdf"
    win.export_pdf_action.trigger()
    win.close()
    gate.set()
    qtbot.waitUntil(lambda: not win._pdf_busy, timeout=30_000)
    assert win._notebook_message.text == EXPORTING_TEXT


@needs_qt
def test_the_export_entries_say_reinstall_when_nbconvert_is_missing(qtbot, monkeypatch):
    from srim.app import window as window_module

    helpers = _tab_helpers()
    monkeypatch.setattr(notebooks, "_installed", lambda name: name != "nbconvert")
    win = helpers.window_of(qtbot)
    assert not win.export_pdf_action.isEnabled()
    assert win.export_pdf_action.toolTip() == REINSTALL_TEXT
    assert window_module.pdf_problem() == REINSTALL_TEXT


@needs_qt
def test_the_export_entry_says_to_start_a_notebook_first(qtbot, monkeypatch):
    from srim.app import window as window_module
    from srim.app.notebook_tab import EXPORT_NO_NOTEBOOK_TEXT

    monkeypatch.setattr(window_module, "pdf_problem", lambda: None)
    win = _tab_helpers().window_of(qtbot)
    assert not win.export_pdf_action.isEnabled()
    assert win.export_pdf_action.toolTip() == EXPORT_NO_NOTEBOOK_TEXT
