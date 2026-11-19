"""The Agent activity panel, its settings dialog and the Agent menu (Qt).

The entries come from the agent over the socket, so they are untrusted: a bad shape is refused by
the backend, and a text is shown as plain text, elided. The panel keeps at most ROW_CAP rows.
"""

import json
import time

import pytest

pytest.importorskip("PySide6")
pytest.importorskip("pytestqt")

from PySide6.QtWidgets import QDialogButtonBox  # noqa: E402

from srim.app.agent_backend import AgentBackend  # noqa: E402
from srim.app.agent_panel import (  # noqa: E402
    CELL_CAP,
    COLUMNS,
    ROW_CAP,
    AgentPanel,
    error_colour,
)
from srim.app.agent_settings_dialog import AgentSettingsDialog  # noqa: E402
from srim.app.settings import AppSettings, load_settings  # noqa: E402
from srim.app.window import MainWindow  # noqa: E402
from srim.mcp.activity import ActivityEntry, ActivityLog, activity_path  # noqa: E402
from srim.mcp.backend import BackendError  # noqa: E402
from srim.mcp.discovery import discovery_path  # noqa: E402

#: the bound of a wait for the GUI
WAIT = 5000
#: the columns of the panel, by index
TIME, CLIENT, ACTION, ANSWER = 0, 1, 2, 3


@pytest.fixture
def window(qtbot, tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    win = MainWindow(AppSettings(route="wine"), checks_fn=lambda route: [])
    qtbot.addWidget(win)
    return win


@pytest.fixture
def panel(qtbot):
    widget = AgentPanel()
    qtbot.addWidget(widget)
    return widget


def entry(**changes) -> ActivityEntry:
    data = {
        "time": 1700000000.0,
        "mode": "headless",
        "client": "claude 1",
        "tool": "set_values",
        "summary": "set ion.energy_keV=40",
    }
    data.update(changes)
    return ActivityEntry(**data)


def wire(**changes) -> dict:
    """The entry as the agent sends it (a plain dict, all fields)."""
    data = {
        "time": 1700000000.0,
        "mode": "headless",
        "client": "claude 1",
        "tool": "set_values",
        "summary": "set ion.energy_keV=40",
        "ask": None,
        "answer": None,
        "error": None,
    }
    data.update(changes)
    return data


def cell(panel, row, column) -> str:
    return panel.table.item(row, column).text()


# --- the panel -----------------------------------------------------------------------------------


def test_the_panel_is_hidden_until_a_client_connects(qtbot, window):
    window.show()
    assert window.agent_panel.isHidden()
    window.agent.client_connected.emit("claude 1")
    assert not window.agent_panel.isHidden()


def test_three_entries_are_three_rows_newest_at_the_bottom(panel):
    for summary in ("first", "second", "third"):
        panel.add(entry(summary=summary))
    assert panel.table.rowCount() == 3
    assert [cell(panel, row, ACTION) for row in range(3)] == ["first", "second", "third"]


def test_the_time_and_client_are_shown(panel):
    stamp = 1700000000.0
    panel.add(entry(time=stamp, client="codex 0.9"))
    assert cell(panel, 0, TIME) == time.strftime("%H:%M:%S", time.localtime(stamp))
    assert cell(panel, 0, CLIENT) == "codex 0.9"


def test_an_ask_shows_its_answer(panel):
    panel.add(entry(ask="uses 4 of 8 cores", answer="denied"))
    assert "uses 4 of 8 cores" in cell(panel, 0, ACTION)
    assert cell(panel, 0, ANSWER) == "denied"


def test_an_error_is_shown_in_the_error_colour(panel):
    panel.add(entry(error="disk full"))
    item = panel.table.item(0, ACTION)
    assert "disk full" in item.text()
    assert item.foreground().color() == error_colour()


def test_markup_is_shown_as_plain_text(panel):
    text = "<b>bold</b> <a href='https://example.com'>link</a>"
    panel.add(entry(summary=text))
    assert cell(panel, 0, ACTION) == text


def test_a_huge_field_is_elided_in_its_cell(panel):
    panel.add(entry(summary="x" * 500_000))
    shown = cell(panel, 0, ACTION)
    assert len(shown) <= CELL_CAP + 1
    assert shown.endswith("…")


def test_line_breaks_in_a_field_become_spaces(panel):
    panel.add(entry(summary="first line\nsecond\tline"))
    assert cell(panel, 0, ACTION) == "first line second line"


def test_the_panel_keeps_only_the_newest_rows(panel):
    for i in range(ROW_CAP + 5):
        panel.add(entry(summary=f"step {i}"))
    assert panel.table.rowCount() == ROW_CAP
    assert cell(panel, 0, ACTION) == "step 5"
    assert cell(panel, ROW_CAP - 1, ACTION) == f"step {ROW_CAP + 4}"


def test_load_recent_shows_the_newest_entries_and_skips_broken_lines(panel):
    log = ActivityLog(activity_path())
    for i in range(3):
        log.append(entry(summary=f"step {i}"))
    with open(activity_path(), "a", encoding="utf-8") as handle:
        handle.write("not json\n")
        handle.write(json.dumps({**wire(), "time": "soon"}) + "\n")
        handle.write(json.dumps({**wire(), "summary": "a line of its own"}) + "\n")
    panel.load_recent(log, n=50)
    assert [cell(panel, row, ACTION) for row in range(4)] == [
        "step 0",
        "step 1",
        "step 2",
        "a line of its own",
    ]


def test_load_recent_reads_only_the_newest_entries_of_a_large_log(panel, tmp_path):
    log = ActivityLog(tmp_path / "big.jsonl", max_bytes=10**9)
    for i in range(3000):
        log.append(entry(summary=f"step {i} " + "y" * 300))
    panel.load_recent(log, n=5)
    assert panel.table.rowCount() == 5
    assert cell(panel, 4, ACTION).startswith("step 2999 ")


def test_load_recent_of_a_bad_file_shows_nothing_and_does_not_raise(panel, tmp_path):
    path = tmp_path / "bad.jsonl"
    path.write_bytes(b"\xff\xfe\x00 not an entry\n")
    panel.load_recent(ActivityLog(path), n=50)
    assert panel.table.rowCount() == 0


# --- the backend's log -------------------------------------------------------------------------


def test_log_adds_the_entry_as_a_row(window):
    backend = AgentBackend(window)
    assert backend.handle("log", {"entry": wire(summary="set ion.energy_keV=40")}) is None
    assert cell(window.agent_panel, 0, ACTION) == "set ion.energy_keV=40"


@pytest.mark.parametrize(
    "bad",
    [
        {"time": 1.0, "mode": "headless", "client": "c", "tool": "t"},  # no summary
        wire(time="soon"),
        wire(time=True),
        wire(time=float("nan")),
        wire(mode="remote"),
        wire(summary=["a", "list"]),
        wire(unknown_field="x"),
        "a string, not an entry",
        None,
    ],
)
def test_a_bad_entry_is_refused_and_adds_no_row(window, bad):
    backend = AgentBackend(window)
    with pytest.raises(BackendError):
        backend.handle("log", {"entry": bad})
    assert window.agent_panel.table.rowCount() == 0


def test_log_without_an_entry_is_refused(window):
    with pytest.raises(BackendError):
        AgentBackend(window).handle("log", {})


def test_a_huge_entry_from_the_agent_is_capped_in_the_row(window):
    AgentBackend(window).handle("log", {"entry": wire(summary="z" * 300_000)})
    shown = cell(window.agent_panel, 0, ACTION)
    assert len(shown) <= CELL_CAP + 1


# --- the settings dialog -------------------------------------------------------------------------


def test_the_settings_dialog_saves_the_two_limits(window):
    dialog = AgentSettingsDialog(window.settings, window)
    dialog.minutes.setValue(45)
    dialog.cores.setValue(75)
    dialog.accept()
    saved = load_settings()
    assert saved.agent_max_minutes == 45
    assert saved.agent_max_core_fraction == 0.75
    assert window.settings.agent_max_minutes == 45


def test_cancelling_the_settings_dialog_saves_nothing(window):
    dialog = AgentSettingsDialog(window.settings, window)
    dialog.minutes.setValue(45)
    dialog.reject()
    assert load_settings().agent_max_minutes == 30
    assert window.settings.agent_max_minutes == 30


def test_the_settings_dialog_shows_the_saved_limits(window):
    window.settings.agent_max_minutes = 90
    window.settings.agent_max_core_fraction = 0.25
    dialog = AgentSettingsDialog(window.settings, window)
    assert dialog.minutes.value() == 90
    assert dialog.cores.value() == 25


def test_the_settings_dialog_has_ok_and_cancel(window):
    dialog = AgentSettingsDialog(window.settings, window)
    buttons = dialog.buttons
    assert buttons.button(QDialogButtonBox.StandardButton.Ok) is not None
    assert buttons.button(QDialogButtonBox.StandardButton.Cancel) is not None


# --- the window: the menu and the socket toggle -------------------------------------------------


def test_the_agent_menu_sits_between_notebook_and_view(window):
    titles = [action.text().replace("&", "") for action in window.menuBar().actions()]
    assert titles == ["File", "Edit", "Setup", "Run", "Notebook", "Agent", "View", "Help"]


def test_the_agent_activity_action_shows_and_hides_the_panel(window):
    window.show()
    action = window.agent_activity_action
    assert action.isCheckable() and not action.isChecked()
    action.trigger()  # as a click: the dock's view action reacts to triggered
    assert not window.agent_panel.isHidden()
    assert action.isChecked()
    action.trigger()
    assert window.agent_panel.isHidden()
    assert not action.isChecked()


def test_unticking_allow_agents_removes_the_discovery_file(window):
    window.start_agent_socket()
    assert discovery_path().is_file()
    window.agent_socket_action.setChecked(False)
    assert not discovery_path().exists()
    assert not window.agent.listening
    assert load_settings().agent_socket is False


def test_ticking_allow_agents_starts_the_socket_again(window):
    window.agent_socket_action.setChecked(False)
    window.agent_socket_action.setChecked(True)
    assert window.agent.listening
    assert discovery_path().is_file()
    assert load_settings().agent_socket is True


def test_the_allow_agents_action_follows_the_setting(qtbot, tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    win = MainWindow(AppSettings(route="wine", agent_socket=False), checks_fn=lambda route: [])
    qtbot.addWidget(win)
    assert not win.agent_socket_action.isChecked()


def test_a_socket_that_did_not_start_is_shown_on_the_action(window, monkeypatch):
    from srim.app.agent_server import AgentSocketError

    def refuse():
        raise AgentSocketError("the socket name is taken")

    action = window.agent_socket_action
    action.setChecked(False)  # stop: nothing listens yet
    monkeypatch.setattr(window.agent, "start", refuse)
    action.setChecked(True)  # the choice is kept, the start fails
    assert load_settings().agent_socket is True
    assert "not listening" in action.text()
    assert action.toolTip() == "Agents cannot connect: the socket name is taken"


def test_a_socket_that_starts_clears_the_note(window, monkeypatch):
    from srim.app.agent_server import AgentSocketError

    def refuse():
        raise AgentSocketError("x")

    action = window.agent_socket_action
    action.setChecked(False)
    monkeypatch.setattr(window.agent, "start", refuse)
    action.setChecked(True)
    assert "not listening" in action.text()
    monkeypatch.undo()
    action.setChecked(False)
    action.setChecked(True)
    assert window.agent.listening
    assert "not listening" not in action.text()
    assert "Agents cannot connect" not in action.toolTip()  # Qt shows the text when no tip is set


def test_no_cell_has_a_tooltip_made_of_agent_text(panel):
    # a tooltip would be rich text when it looks like markup: the panel sets none from agent text
    markup = "<a href='https://example.com'>click</a>"
    panel.add(entry(summary=markup, client=markup, answer=markup, ask=markup, error=markup))
    for column in range(len(COLUMNS)):
        assert panel.table.item(0, column).toolTip() == ""
