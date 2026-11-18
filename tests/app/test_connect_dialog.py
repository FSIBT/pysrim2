"""The Connect an agent dialog (:mod:`srim.app.connect_dialog`) and its Agent menu entry (Qt).

No real install and no network: the skill's steps are replaced by a fake step (``skill_steps``),
and ``run_steps`` runs them the way the app does (``yes=False``, no confirmation).
"""

import pytest

pytest.importorskip("PySide6")
pytest.importorskip("pytestqt")

from PySide6.QtGui import QFontDatabase, QGuiApplication  # noqa: E402

from srim.app import connect_dialog  # noqa: E402
from srim.app.connect_dialog import ConnectDialog  # noqa: E402
from srim.app.settings import AppSettings  # noqa: E402
from srim.app.window import MainWindow  # noqa: E402
from srim.installer.steps import Step  # noqa: E402
from srim.mcp import INSTALL_HINT  # noqa: E402
from srim.mcp.connect import snippet  # noqa: E402

#: the bound of a wait for the GUI or a thread
WAIT = 5000
#: the command the snippets start in these tests (a fixed one, not this machine's)
COMMAND = ["/opt/pysrim2/bin/pysrim2", "mcp"]
INSTALLED_TEXT = "The pysrim2 skill is installed"


def fake_skill(state: dict, calls: list) -> Step:
    """A skill step that is not installed until it runs (no files are written here)."""

    def run() -> None:
        calls.append("run")
        state["installed"] = True

    return Step(
        name="skill",
        summary="install the pysrim2 agent skill (fake)",
        check=lambda: state["installed"],
        commands=lambda: ["copy the fake skill"],
        run=run,
    )


@pytest.fixture(autouse=True)
def fake_steps(monkeypatch):
    """Every test uses a fake skill step and a fixed command; the real skill is never touched."""
    state = {"installed": False}
    calls: list[str] = []
    monkeypatch.setattr(connect_dialog, "skill_steps", lambda: [fake_skill(state, calls)])
    monkeypatch.setattr(connect_dialog, "pysrim2_command", lambda: list(COMMAND))
    return state, calls


@pytest.fixture
def dialog(qtbot):
    widget = ConnectDialog()
    qtbot.addWidget(widget)
    widget.show()
    qtbot.waitUntil(lambda: dialog_checked(widget), timeout=WAIT)
    return widget


def dialog_checked(widget) -> bool:
    return widget.skill_label.text() != "Checking the skill…"


def choose(dialog, client: str) -> None:
    dialog.client_box.setCurrentIndex(dialog.client_box.findData(client))


# --- the snippet ---------------------------------------------------------------------------------


def test_choosing_codex_shows_its_config(dialog):
    choose(dialog, "codex")
    assert "[mcp_servers.pysrim2]" in dialog.snippet_box.toPlainText()
    assert dialog.snippet_box.toPlainText() == snippet("codex", COMMAND)


def test_the_location_of_the_snippet_is_shown(dialog):
    choose(dialog, "codex")
    assert "~/.codex/config.toml" in dialog.location.text()


def test_copy_puts_the_snippet_on_the_clipboard(dialog):
    choose(dialog, "gemini")
    dialog.copy_button.click()
    assert QGuiApplication.clipboard().text() == snippet("gemini", COMMAND)


def test_the_snippet_box_is_read_only_and_uses_the_system_fixed_font(dialog):
    fixed = QFontDatabase.systemFont(QFontDatabase.SystemFont.FixedFont)
    assert dialog.snippet_box.isReadOnly()
    assert dialog.snippet_box.font().family() == fixed.family()


def test_the_snippet_starts_the_installed_command(dialog):
    choose(dialog, "claude")
    assert "/opt/pysrim2/bin/pysrim2" in dialog.snippet_box.toPlainText()


# --- the mcp extra -------------------------------------------------------------------------------


def test_a_missing_mcp_extra_is_said(qtbot, monkeypatch):
    monkeypatch.setattr(connect_dialog, "mcp_available", lambda: False)
    widget = ConnectDialog()
    qtbot.addWidget(widget)
    assert INSTALL_HINT in widget.note.text()
    assert not widget.note.isHidden()


def test_the_note_is_hidden_when_the_extra_is_there(qtbot, monkeypatch):
    monkeypatch.setattr(connect_dialog, "mcp_available", lambda: True)
    widget = ConnectDialog()
    qtbot.addWidget(widget)
    assert widget.note.isHidden()


# --- the skill -----------------------------------------------------------------------------------


def test_a_missing_skill_offers_the_install(dialog):
    assert dialog.skill_button.isEnabled()
    assert "not installed" in dialog.skill_label.text()


def test_an_installed_skill_needs_no_install(qtbot, fake_steps):
    state, _calls = fake_steps
    state["installed"] = True
    widget = ConnectDialog()
    qtbot.addWidget(widget)
    widget.show()
    qtbot.waitUntil(lambda: dialog_checked(widget), timeout=WAIT)
    assert not widget.skill_button.isEnabled()
    assert INSTALLED_TEXT in widget.skill_label.text()


def test_the_install_button_runs_the_skill_step(qtbot, dialog, fake_steps):
    state, calls = fake_steps
    dialog.skill_button.click()
    qtbot.waitUntil(lambda: calls == ["run"], timeout=WAIT)
    qtbot.waitUntil(lambda: INSTALLED_TEXT in dialog.skill_label.text(), timeout=WAIT)
    assert not dialog.skill_button.isEnabled()
    assert state["installed"]


def test_the_skill_runs_through_run_steps_without_admin_or_asking(qtbot, monkeypatch, dialog):
    seen = []
    real = connect_dialog.run_steps

    def spy(steps, **kwargs):
        seen.append(kwargs)
        return real(steps, **kwargs)

    monkeypatch.setattr(connect_dialog, "run_steps", spy)
    dialog.skill_button.click()
    qtbot.waitUntil(lambda: bool(seen), timeout=WAIT)
    assert seen[0]["yes"] is False
    assert seen[0]["confirm"] is None


def test_a_closed_dialog_ignores_the_end_of_its_install(qtbot, dialog, fake_steps):
    _state, calls = fake_steps
    dialog.skill_button.click()
    dialog.close()
    qtbot.waitUntil(lambda: calls == ["run"], timeout=WAIT)
    # no error: the late result is dropped, and the dialog can be shown again
    dialog.show()
    qtbot.waitUntil(lambda: INSTALLED_TEXT in dialog.skill_label.text(), timeout=WAIT)


# --- the Agent menu ------------------------------------------------------------------------------


@pytest.fixture
def window(qtbot, tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    win = MainWindow(AppSettings(route="wine"), checks_fn=lambda route: [])
    qtbot.addWidget(win)
    return win


def test_connect_an_agent_is_the_first_item_of_the_agent_menu(window):
    [menu] = [a.menu() for a in window.menuBar().actions() if a.text().replace("&", "") == "Agent"]
    assert menu.actions()[0] is window.connect_agent_action


def test_connect_an_agent_opens_the_dialog(qtbot, window):
    window.connect_agent_action.trigger()
    assert window.connect_dialog is not None
    assert not window.connect_dialog.isHidden()
    qtbot.waitUntil(lambda: dialog_checked(window.connect_dialog), timeout=WAIT)
