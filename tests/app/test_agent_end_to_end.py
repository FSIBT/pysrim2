"""The agent and the open app, end to end: the MCP server (the SDK's in-memory client, run in a
thread) drives the real window through its agent socket. The window's tabs, undo history, Progress,
Results and Agent activity panel change as the agent's calls go through; a long run asks in the
window's dialog; closing the window falls back to headless.

The runs use the fake runner of ``tests/app/runners.py`` (no SRIM, network or agent). Each test has
its own data directory (``tests/app/conftest.py``) and works in its own folder.
"""

import asyncio
import json
import threading

import pytest

pytest.importorskip("PySide6")
pytest.importorskip("pytestqt")
pytest.importorskip("mcp")

from mcp import Client  # noqa: E402
from runners import fixture_runner  # noqa: E402

from srim.app.approval_dialog import ApprovalQueue  # noqa: E402
from srim.app.runs import ParallelChoice  # noqa: E402
from srim.app.settings import AppSettings  # noqa: E402
from srim.app.window import MainWindow  # noqa: E402
from srim.mcp import server  # noqa: E402
from srim.mcp.app_client import AppLink  # noqa: E402
from srim.mcp.headless import HeadlessBackend  # noqa: E402

#: the bound of any wait on the window or a run (milliseconds)
TIMEOUT = 60_000
READY = [{"name": "trim-exe", "ok": True, "detail": "ok", "fix": ""}]
#: a run of about two hours by the library's stand-in rate: over the 30 minute limit
LONG = "number_ions=100000000"
#: the N15 request of the spec: 40 keV N into 1 µm of Si
N15 = {"symbol": "N", "mass_amu": 15.0001, "energy_keV": 40}
SI_LAYER = [
    {"thickness_nm": 1000, "density_g_cm3": 2.33, "elements": [{"symbol": "Si", "stoich": 1}]}
]


class Outcome:
    """A call made in a thread: ``value`` or ``error``, and ``done`` once it ended."""

    def __init__(self):
        self.done = False
        self.value = None
        self.error: BaseException | None = None


def in_thread(fn) -> Outcome:
    outcome = Outcome()

    def run():
        try:
            outcome.value = fn()
        except BaseException as error:  # reported to the test, not lost in the thread
            outcome.error = error
        finally:
            outcome.done = True

    threading.Thread(target=run, daemon=True).start()
    return outcome


def call_tool(srv, name: str, args: dict | None = None):
    """One tool call through the SDK's in-memory client, as a thread's job."""

    async def go():
        async with Client(srv) as client:
            return await client.call_tool(name, args or {})

    return asyncio.run(go())


@pytest.fixture
def window(qtbot, monkeypatch, tmp_path):
    """The real window, its agent socket on, in the test's own folder. Its ask-first dialog
    shows Allow at once (the delay is the window's, shortened here)."""
    monkeypatch.setenv("PYSRIM2_SCAN_RUNNER", "tests.app.runners:fixture_runner")
    monkeypatch.chdir(tmp_path)
    win = MainWindow(
        AppSettings(route="wine"), checks_fn=lambda route: READY, run_runner=fixture_runner
    )
    qtbot.addWidget(win)
    win.approvals = ApprovalQueue(win, allow_delay_s=0.0, gap_s=0.0)
    win.start_agent_socket()
    assert win.agent.listening
    return win


@pytest.fixture
def srv(tmp_path):
    """The MCP server: the app's connection, or headless when there is no app."""
    headless = HeadlessBackend(tmp_path, checks=lambda: READY)
    return server.build_server(
        tmp_path, headless=headless, link=AppLink(cwd=tmp_path, retry_seconds=0.0)
    )


def tool(qtbot, srv, name: str, args: dict | None = None):
    """Call a tool and wait for its result (the window's event loop runs meanwhile)."""
    outcome = in_thread(lambda: call_tool(srv, name, args))
    qtbot.waitUntil(lambda: outcome.done, timeout=TIMEOUT)
    if outcome.error is not None:
        raise outcome.error
    return outcome.value


def payload(result) -> dict:
    """The dict a tool returned: its JSON text (the first text content)."""
    assert not result.is_error, result.content
    texts = [part.text for part in result.content if part.type == "text"]
    return json.loads(texts[0])


def error_of(result) -> str:
    assert result.is_error, result
    return result.content[0].text


def set_the_n15_edits(qtbot, srv, ions: str = "number_ions=200") -> None:
    """The N15 setup as the agent makes it, not saved yet."""
    tool(qtbot, srv, "set_values", {"assignments": [ions]})
    tool(qtbot, srv, "set_section", {"name": "ion", "value": N15})
    tool(qtbot, srv, "set_section", {"name": "target", "value": SI_LAYER})


def set_the_n15_setup(qtbot, srv, path, ions: str = "number_ions=200") -> None:
    """The N15 setup as the agent makes it, saved to ``path``."""
    set_the_n15_edits(qtbot, srv, ions)
    tool(qtbot, srv, "save_setup", {"path": str(path)})


def answer_the_dialog(qtbot, window, allow: bool) -> None:
    """Wait for the window's ask-first dialog, and click Allow or Deny."""
    qtbot.waitUntil(lambda: window.approvals.shown is not None, timeout=TIMEOUT)
    dialog = window.approvals.shown
    button = dialog.allow_button if allow else dialog.deny_button
    qtbot.waitUntil(button.isEnabled, timeout=TIMEOUT)
    button.click()


def test_the_n15_request_runs_and_shows_the_profile_in_the_app(qtbot, window, srv, tmp_path):
    assert payload(tool(qtbot, srv, "status"))["mode"] == "app"
    set_the_n15_setup(qtbot, srv, tmp_path / "n15.yaml")
    # the edits are in the window's tabs and its undo history, as the agent's own steps
    assert window.document.doc["ion"]["energy_keV"] == 40
    assert window.document.undo_label.startswith("agent: ")
    assert "agent" in window.undo_action.text()

    started = payload(tool(qtbot, srv, "start_run", {}))
    assert started["mode"] == "app"
    qtbot.waitUntil(lambda: not window.run.running, timeout=TIMEOUT)
    assert payload(tool(qtbot, srv, "run_status", {}))["status"] == "finished"

    shown = payload_of_plot(tool(qtbot, srv, "plot", {"key": "range_profile"}))
    assert shown["shown"] is True and shown["mode"] == "app"
    assert window.tabs.currentWidget() is window.results
    assert window.agent_panel.table.rowCount() >= 5


def payload_of_plot(result) -> dict:
    """A plot's details: the second content part (the first is the picture)."""
    assert not result.is_error, result.content
    assert result.content[0].type == "image"
    return json.loads(result.content[1].text)


def test_a_long_run_asks_in_the_window_and_allow_starts_it(qtbot, window, srv, tmp_path):
    set_the_n15_setup(qtbot, srv, tmp_path / "n15.yaml", ions=LONG)
    outcome = in_thread(lambda: call_tool(srv, "start_run", {}))
    qtbot.waitUntil(lambda: window.approvals.shown is not None, timeout=TIMEOUT)
    dialog = window.approvals.shown
    qtbot.waitUntil(lambda: dialog.allow_button.isEnabled(), timeout=TIMEOUT)
    dialog.allow_button.click()
    qtbot.waitUntil(lambda: outcome.done, timeout=TIMEOUT)
    assert outcome.error is None
    started = payload(outcome.value)
    assert started["folder"] and started["mode"] == "app"
    qtbot.waitUntil(lambda: not window.run.running, timeout=TIMEOUT)


def test_a_long_run_the_user_denies_is_not_started(qtbot, window, srv, tmp_path):
    set_the_n15_setup(qtbot, srv, tmp_path / "n15.yaml", ions=LONG)
    outcome = in_thread(lambda: call_tool(srv, "start_run", {}))
    qtbot.waitUntil(lambda: window.approvals.shown is not None, timeout=TIMEOUT)
    dialog = window.approvals.shown
    qtbot.waitUntil(lambda: dialog.deny_button.isEnabled(), timeout=TIMEOUT)
    dialog.deny_button.click()
    qtbot.waitUntil(lambda: outcome.done, timeout=TIMEOUT)
    assert "did not allow" in error_of(outcome.value)
    assert window.run.folder is None  # nothing was started
    assert not window.run.running


def test_an_edit_while_the_question_is_open_refuses_allow(qtbot, window, srv, tmp_path):
    """The window stays usable: the user edits the setup while the dialog is up. Allow then
    starts nothing, because the setup is no longer the one the user was asked about."""
    set_the_n15_setup(qtbot, srv, tmp_path / "n15.yaml", ions=LONG)
    outcome = in_thread(lambda: call_tool(srv, "start_run", {}))
    qtbot.waitUntil(lambda: window.approvals.shown is not None, timeout=TIMEOUT)
    window.document.edit(lambda doc: doc.__setitem__("number_ions", 150))  # the user's edit
    answer_the_dialog(qtbot, window, allow=True)
    qtbot.waitUntil(lambda: outcome.done, timeout=TIMEOUT)
    assert "changed while the user was asked" in error_of(outcome.value)
    assert window.run.folder is None
    assert not window.run.running


def test_a_run_the_user_starts_while_the_question_is_open_withdraws_it(
    qtbot, window, srv, tmp_path
):
    """The user starts a run while the agent's question is open. The question is withdrawn (its
    dialog closes, denied): the agent's start is refused, and no second run is started."""
    set_the_n15_setup(qtbot, srv, tmp_path / "n15.yaml", ions=LONG)
    outcome = in_thread(lambda: call_tool(srv, "start_run", {}))
    qtbot.waitUntil(lambda: window.approvals.shown is not None, timeout=TIMEOUT)
    window.start_run_with(ParallelChoice())  # the user's own run
    qtbot.waitUntil(lambda: not window.run.running, timeout=TIMEOUT)
    qtbot.waitUntil(lambda: outcome.done, timeout=TIMEOUT)
    assert window.approvals.shown is None  # the dialog is gone: Allow cannot be pressed
    assert "did not allow" in error_of(outcome.value)
    runs = tmp_path / "n15-runs"
    folders = [p for p in runs.iterdir() if (p / "scan.yaml").is_file()]
    assert folders == [window.run.folder]  # the user's run only


def test_closing_the_app_falls_back_to_headless(qtbot, window, srv, tmp_path):
    saved = tmp_path / "n15.yaml"
    set_the_n15_setup(qtbot, srv, saved)
    assert payload(tool(qtbot, srv, "get_setup"))["mode"] == "app"

    window.close()  # the agent socket stops and its discovery file goes with it

    result = payload(tool(qtbot, srv, "get_setup"))
    assert result["mode"] == "headless"
    assert str(saved) in result["note"]  # the setup now comes from its last saved file
    assert result["path"] == str(saved)


def test_saving_over_a_file_the_session_did_not_write_asks_in_the_window(
    qtbot, window, srv, tmp_path
):
    taken = tmp_path / "taken.yaml"
    taken.write_text("not a setup of this session\n", encoding="utf-8")
    set_the_n15_edits(qtbot, srv)
    outcome = in_thread(lambda: call_tool(srv, "save_setup", {"path": str(taken)}))
    answer_the_dialog(qtbot, window, allow=False)
    qtbot.waitUntil(lambda: outcome.done, timeout=TIMEOUT)
    assert "did not allow" in error_of(outcome.value)
    assert taken.read_text(encoding="utf-8") == "not a setup of this session\n"  # kept

    outcome = in_thread(lambda: call_tool(srv, "save_setup", {"path": str(taken)}))
    answer_the_dialog(qtbot, window, allow=True)
    qtbot.waitUntil(lambda: outcome.done, timeout=TIMEOUT)
    assert payload(outcome.value)["path"] == str(taken)
    assert "number_ions: 200" in taken.read_text(encoding="utf-8")


def test_opening_a_run_over_unsaved_edits_asks_in_the_window(qtbot, window, srv, tmp_path):
    set_the_n15_setup(qtbot, srv, tmp_path / "n15.yaml")
    tool(qtbot, srv, "start_run", {})
    qtbot.waitUntil(lambda: not window.run.running, timeout=TIMEOUT)
    folder = window.run.folder
    tool(qtbot, srv, "set_values", {"assignments": ["number_ions=5"]})  # unsaved now

    outcome = in_thread(lambda: call_tool(srv, "open_run", {"folder": str(folder)}))
    answer_the_dialog(qtbot, window, allow=False)
    qtbot.waitUntil(lambda: outcome.done, timeout=TIMEOUT)
    assert "did not allow" in error_of(outcome.value)
    assert window.document.doc["number_ions"] == 5  # the unsaved edit is kept

    outcome = in_thread(lambda: call_tool(srv, "open_run", {"folder": str(folder)}))
    answer_the_dialog(qtbot, window, allow=True)
    qtbot.waitUntil(lambda: outcome.done, timeout=TIMEOUT)
    assert payload(outcome.value)["folder"] == str(folder)
    assert window.document.doc["number_ions"] == 200  # the run's own setup replaced the edit
