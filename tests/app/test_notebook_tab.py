"""The Notebook menu and the Notebook tab: a notebook of the run folder, shown in the window.

The notebook server is the fake of ``fake_notebook_server.py`` (no marimo or Jupyter runs), and
the web view is a plain widget that records the address it is given. The run folders are built
from test_files/2 by ``run_folders`` (no SRIM).
"""

import os
import shutil
import sys
import threading
from copy import deepcopy
from pathlib import Path

import pytest

pytest.importorskip("PySide6")
pytest.importorskip("pytestqt")

from PySide6.QtCore import QUrl  # noqa: E402
from PySide6.QtWidgets import QMessageBox, QWidget  # noqa: E402
from run_folders import DONE, FIXTURE, finished_scan  # noqa: E402

from srim.app import notebook_tab  # noqa: E402
from srim.app import window as window_module  # noqa: E402
from srim.app.notebook_tab import (  # noqa: E402
    JUPYTER_RELOAD_TEXT,
    NEW_RESULTS_TEXT,
    NO_FOLDER_TEXT,
    REPLACE_TEXT,
)
from srim.app.notebooks import REINSTALL_TEXT, NotebookServer  # noqa: E402
from srim.app.settings import AppSettings  # noqa: E402
from srim.app.window import MainWindow  # noqa: E402
from srim.notebook import NOTEBOOK_DIR, RELOAD_FILE, load_run  # noqa: E402
from srim.scan import Scan  # noqa: E402
from srim.scan.executor import MARKER_NAME  # noqa: E402
from srim.spec import RunSpec  # noqa: E402

FAKE = Path(__file__).with_name("fake_notebook_server.py")
RUN_TIMEOUT = 60_000
START_TIMEOUT = 30_000
BORON = {
    "version": 1,
    "ion": {"symbol": "B", "energy_keV": 20.0, "angle_deg": 7.0},
    "number_ions": 200,
    "calculation": 1,
    "seed": 12345,
    "settings": {},
    "target": [
        {
            "name": "gold",
            "thickness_nm": 100.0,
            "density_g_cm3": 19.3,
            "phase": 0,
            "elements": [
                {
                    "symbol": "Au",
                    "stoich": 1.0,
                    "displacement_eV": 25.0,
                    "lattice_eV": 3.0,
                    "surface_eV": 3.0,
                }
            ],
        }
    ],
}
TABS_WITHOUT_NOTEBOOK = 6


class FakeView(QWidget):
    """The web view of the tab: records the addresses it is given."""

    def __init__(self):
        super().__init__()
        self.urls = []

    def setUrl(self, url):  # noqa: N802 (Qt's name)
        self.urls.append(url)


def fake_command(mode):
    """A ``command_for`` that runs the fake server in ``mode`` (serve, exit or silent)."""

    def command_for(kind, path, port, token_file):
        extra = [str(port)] if mode == "serve" else []
        return [sys.executable, str(FAKE), mode, *extra]

    return command_for


def use_server(monkeypatch, mode="serve"):
    """The tab starts the fake server in ``mode`` instead of a notebook."""

    def make(kind, path):
        return NotebookServer(kind, path, command_for=fake_command(mode))

    monkeypatch.setattr(notebook_tab, "NotebookServer", make)


@pytest.fixture
def notebooks(monkeypatch):
    """WebEngine and the notebook tools count as installed; the view is the fake one."""
    monkeypatch.setattr(notebook_tab, "webengine_available", lambda: True)
    monkeypatch.setattr(notebook_tab, "tool_problem", lambda kind: None)
    monkeypatch.setattr(notebook_tab, "_new_webengine_view", FakeView)
    use_server(monkeypatch)


def window_of(qtbot, refresh=5):
    win = MainWindow(AppSettings(route="wine", refresh_seconds=refresh), checks_fn=lambda route: [])
    qtbot.addWidget(win)
    return win


def run_folder(tmp_path, outcomes):
    """A scan of two energies in ``tmp_path/run``; ``outcomes`` says which points are done."""
    spec = RunSpec.from_dict(deepcopy(BORON))
    scan = Scan(spec, grid={"ion.energy_keV": [10.0, 20.0]}, seed=7)
    folder = tmp_path / "run"
    finished_scan(folder, scan, outcomes)
    # A scan folder has its marker (the app opens only a folder that has it).
    (folder / MARKER_NAME).write_text("pysrim2 scan folder\n", encoding="utf-8")
    return folder, scan


def open_folder(qtbot, win, folder):
    """Open a run folder in the window (its ``finished`` is the end of the load)."""
    with qtbot.waitSignal(win.run.finished, timeout=RUN_TIMEOUT):
        win.run.load(folder)


def start_and_wait(qtbot, win, action):
    """Trigger a start action and wait until the notebook's server answers."""
    action.trigger()
    tab = win.notebook_tab
    assert tab is not None
    qtbot.waitUntil(lambda: bool(tab.view.urls), timeout=START_TIMEOUT)
    return tab


def test_the_notebook_menu_is_between_run_and_view(qtbot):
    win = window_of(qtbot)
    titles = [action.text() for action in win.menuBar().actions()]
    assert titles.index("&Notebook") == titles.index("&Run") + 1
    assert titles.index("&View") == titles.index("&Notebook") + 1


def test_start_marimo_is_greyed_without_a_run_folder_and_names_the_reason(qtbot):
    win = window_of(qtbot)
    assert not win.start_marimo_action.isEnabled()
    assert win.start_marimo_action.toolTip() == NO_FOLDER_TEXT
    assert not win.start_jupyter_action.isEnabled()


def test_start_marimo_is_greyed_with_the_reinstall_hint_when_marimo_is_missing(
    qtbot, tmp_path, monkeypatch
):
    monkeypatch.setattr(notebook_tab, "webengine_available", lambda: True)
    monkeypatch.setattr(
        notebook_tab,
        "tool_problem",
        lambda kind: REINSTALL_TEXT if kind == "marimo" else None,
    )
    folder, _scan = run_folder(tmp_path, [DONE, DONE])
    win = window_of(qtbot)
    open_folder(qtbot, win, folder)
    assert not win.start_marimo_action.isEnabled()
    assert win.start_marimo_action.toolTip() == REINSTALL_TEXT
    assert win.start_jupyter_action.isEnabled()


def test_without_webengine_the_start_entries_say_reinstall(qtbot, tmp_path, monkeypatch):
    monkeypatch.setattr(notebook_tab, "webengine_available", lambda: False)
    monkeypatch.setattr(notebook_tab, "tool_problem", lambda kind: None)
    folder, _scan = run_folder(tmp_path, [DONE, DONE])
    win = window_of(qtbot)
    open_folder(qtbot, win, folder)
    assert not win.start_jupyter_action.isEnabled()
    assert win.start_jupyter_action.toolTip() == REINSTALL_TEXT


def test_start_marimo_creates_the_notebook_and_shows_its_url_in_a_notebook_tab(
    qtbot, tmp_path, notebooks
):
    folder, _scan = run_folder(tmp_path, [DONE, DONE])
    win = window_of(qtbot)
    win.show()
    open_folder(qtbot, win, folder)
    assert win.start_marimo_action.isEnabled()
    tab = start_and_wait(qtbot, win, win.start_marimo_action)
    assert (folder / "notebook.py").is_file()
    assert win.tabs.indexOf(tab) >= 0
    assert win.tabs.tabText(win.tabs.indexOf(tab)) == "Notebook"
    assert win.stop_notebook_action.isEnabled()
    shown = tab.view.urls[0]
    assert isinstance(shown, QUrl)
    assert shown.toString() == tab.server.url
    assert "access_token=" in tab.server.url


def test_a_failed_start_shows_the_log_tail_and_removes_the_tab(qtbot, tmp_path, monkeypatch):
    monkeypatch.setattr(notebook_tab, "webengine_available", lambda: True)
    monkeypatch.setattr(notebook_tab, "tool_problem", lambda kind: None)
    monkeypatch.setattr(notebook_tab, "_new_webengine_view", FakeView)
    use_server(monkeypatch, mode="exit")
    folder, _scan = run_folder(tmp_path, [DONE, DONE])
    win = window_of(qtbot)
    open_folder(qtbot, win, folder)
    win.start_marimo_action.trigger()
    qtbot.waitUntil(lambda: win.notebook_tab is None, timeout=START_TIMEOUT)
    assert win.tabs.count() == TABS_WITHOUT_NOTEBOOK
    assert not win.stop_notebook_action.isEnabled()
    shown = win.status.shown_text()
    assert "did not start" in shown
    assert "line 30" in shown
    assert "line 5" not in shown
    # A setup refresh replaces the setup messages, not the notebook's message.
    with qtbot.waitSignal(win.checked, timeout=5000):
        win.refresh_checks()
    assert "did not start" in win.status.shown_text()


def test_the_banner_appears_when_more_points_have_outputs(qtbot, tmp_path, notebooks):
    folder, scan = run_folder(tmp_path, [DONE, "later"])
    load_run(folder).mark_loaded()  # one point loaded
    win = window_of(qtbot, refresh=1)
    win.show()
    open_folder(qtbot, win, folder)
    tab = start_and_wait(qtbot, win, win.start_marimo_action)
    qtbot.waitUntil(lambda: tab._pending is False, timeout=START_TIMEOUT)
    assert tab.banner.isHidden()
    # The second point gets its outputs after the notebook loaded the first one.
    point = scan.points()[1]
    point_dir = folder / "points" / point.id[:16]
    shutil.copytree(FIXTURE, point_dir)
    point.spec.to_yaml(point_dir / "run.yaml", provenance={"test": "banner"})
    qtbot.waitUntil(lambda: not tab.banner.isHidden(), timeout=10_000)
    assert tab.banner.text() == NEW_RESULTS_TEXT
    tab.reload_button.click()
    assert tab.banner.isHidden()  # hidden at once; the notebook has not loaded the results yet
    qtbot.waitUntil(lambda: not tab.banner.isHidden(), timeout=10_000)


def test_the_reload_touches_the_reload_file_for_marimo(qtbot, tmp_path, notebooks):
    folder, _scan = run_folder(tmp_path, [DONE, DONE])
    win = window_of(qtbot)
    open_folder(qtbot, win, folder)
    tab = start_and_wait(qtbot, win, win.start_marimo_action)
    reload_file = folder / NOTEBOOK_DIR / RELOAD_FILE
    os.utime(reload_file, (1_000_000, 1_000_000))
    tab.reload_button.click()
    assert reload_file.stat().st_mtime > 1_000_000


def test_reload_for_jupyter_only_says_to_run_the_first_cell(qtbot, tmp_path, notebooks):
    folder, _scan = run_folder(tmp_path, [DONE, DONE])
    win = window_of(qtbot)
    open_folder(qtbot, win, folder)
    tab = start_and_wait(qtbot, win, win.start_jupyter_action)
    assert (folder / "notebook.ipynb").is_file()
    tab.reload_button.click()
    assert not tab.banner.isHidden()
    assert tab.banner.text() == JUPYTER_RELOAD_TEXT


def test_the_browser_button_opens_the_address_with_its_token(
    qtbot, tmp_path, notebooks, monkeypatch
):
    opened = []
    monkeypatch.setattr(
        notebook_tab.QDesktopServices, "openUrl", staticmethod(lambda url: opened.append(url))
    )
    folder, _scan = run_folder(tmp_path, [DONE, DONE])
    win = window_of(qtbot)
    open_folder(qtbot, win, folder)
    tab = start_and_wait(qtbot, win, win.start_marimo_action)
    tab.browser_button.click()
    assert [url.toString() for url in opened] == [tab.server.url]


def test_starting_a_second_notebook_asks_first_and_no_keeps_the_first(
    qtbot, tmp_path, notebooks, monkeypatch
):
    asked = []

    def answer(answer_value):
        def question(*args, **kwargs):
            asked.append(args[2])
            return answer_value

        return staticmethod(question)

    folder, _scan = run_folder(tmp_path, [DONE, DONE])
    win = window_of(qtbot)
    win.show()
    open_folder(qtbot, win, folder)
    first = start_and_wait(qtbot, win, win.start_marimo_action)

    monkeypatch.setattr(
        window_module.QMessageBox, "question", answer(QMessageBox.StandardButton.No)
    )
    win.start_jupyter_action.trigger()
    assert asked == [REPLACE_TEXT]
    assert win.notebook_tab is first
    assert first.server.running

    monkeypatch.setattr(
        window_module.QMessageBox, "question", answer(QMessageBox.StandardButton.Yes)
    )
    win.start_jupyter_action.trigger()
    second = win.notebook_tab
    assert second is not first
    assert second.kind == "jupyter"
    assert not first.server.running
    qtbot.waitUntil(lambda: bool(second.view.urls), timeout=START_TIMEOUT)
    assert second.server.running


def test_closing_the_window_stops_the_server(qtbot, tmp_path, notebooks):
    folder, _scan = run_folder(tmp_path, [DONE, DONE])
    win = window_of(qtbot)
    open_folder(qtbot, win, folder)
    tab = start_and_wait(qtbot, win, win.start_marimo_action)
    server = tab.server
    assert server.running
    win.close()
    assert not server.running
    assert server.process.poll() is not None


def test_quitting_the_app_stops_the_server_and_the_exports(qtbot, tmp_path, notebooks):
    from PySide6.QtWidgets import QApplication

    from srim.app import notebooks as notebooks_module
    from srim.app.main import stop_on_quit

    folder, _scan = run_folder(tmp_path, [DONE, DONE])
    win = window_of(qtbot)
    open_folder(qtbot, win, folder)
    tab = start_and_wait(qtbot, win, win.start_marimo_action)
    server = tab.server
    app = QApplication.instance()
    stop_on_quit(app, win)
    app.aboutToQuit.emit()  # the quit runs with the window still open
    assert not server.running
    assert win.notebook_tab is None
    assert notebooks_module._exports_stopped  # an export started now is refused
    win.stop_background()  # idempotent: a second stop (the window's close) changes nothing
    assert win.notebook_tab is None


def test_stop_notebook_removes_the_tab(qtbot, tmp_path, notebooks):
    folder, _scan = run_folder(tmp_path, [DONE, DONE])
    win = window_of(qtbot)
    open_folder(qtbot, win, folder)
    tab = start_and_wait(qtbot, win, win.start_marimo_action)
    server = tab.server
    win.stop_notebook_action.trigger()
    assert win.notebook_tab is None
    assert win.tabs.count() == TABS_WITHOUT_NOTEBOOK
    assert not server.running
    assert not win.stop_notebook_action.isEnabled()


def test_the_stop_button_of_the_tab_removes_the_tab(qtbot, tmp_path, notebooks):
    folder, _scan = run_folder(tmp_path, [DONE, DONE])
    win = window_of(qtbot)
    open_folder(qtbot, win, folder)
    tab = start_and_wait(qtbot, win, win.start_marimo_action)
    tab.stop_button.click()
    assert win.notebook_tab is None
    assert win.tabs.count() == TABS_WITHOUT_NOTEBOOK


def test_a_late_count_after_the_tab_stopped_is_ignored(qtbot, tmp_path, notebooks):
    folder, _scan = run_folder(tmp_path, [DONE, DONE])
    win = window_of(qtbot)
    win.show()
    open_folder(qtbot, win, folder)
    tab = start_and_wait(qtbot, win, win.start_marimo_action)
    qtbot.waitUntil(lambda: tab._pending is False, timeout=START_TIMEOUT)
    generation = tab._generation
    tab.stop()
    # A count that would show the banner (5 results, none loaded) if it were accepted.
    tab._counted(generation, (5, 0))
    assert tab.banner.isHidden()
    assert tab._pending is False


@pytest.mark.parametrize("phase", ["start", "wait_ready"])
def test_a_stop_during_the_start_leaves_no_server_running(
    qtbot, tmp_path, notebooks, monkeypatch, phase
):
    """The stop lands while the start thread is blocked in the server's start or readiness
    wait; whichever way the start goes on, no server process is left running."""
    blocked = threading.Event()
    release = threading.Event()
    finished = threading.Event()

    class Blocking(NotebookServer):
        def _block(self):
            blocked.set()
            release.wait(10)

        def start(self):
            try:
                if phase == "start":
                    self._block()
                super().start()
            finally:
                finished.set()

        def wait_ready(self, *args, **kwargs):
            try:
                if phase == "wait_ready":
                    self._block()
                super().wait_ready(*args, **kwargs)
            finally:
                finished.set()

    monkeypatch.setattr(
        notebook_tab,
        "NotebookServer",
        lambda kind, path: Blocking(kind, path, command_for=fake_command("serve")),
    )
    folder, _scan = run_folder(tmp_path, [DONE, DONE])
    win = window_of(qtbot)
    open_folder(qtbot, win, folder)
    win.start_marimo_action.trigger()
    tab = win.notebook_tab
    server = tab.server
    assert blocked.wait(10)
    win.stop_notebook_action.trigger()
    release.set()
    assert finished.wait(10)
    assert not server.running
    assert server.process is None or server.process.poll() is not None
    assert win.notebook_tab is None
