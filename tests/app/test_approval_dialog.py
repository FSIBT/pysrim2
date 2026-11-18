"""The app's confirm dialog for the agent's ask-first requests (:mod:`srim.app.approval_dialog`).

The dialog is shown with ``open()`` (the event loop keeps running), so the tests wait with
``qtbot``. The socket test runs a client in a thread; every wait is bounded.
"""

import pytest

pytest.importorskip("PySide6")
pytest.importorskip("pytestqt")

from PySide6.QtCore import Qt  # noqa: E402
from PySide6.QtWidgets import QDialog  # noqa: E402
from test_agent_server import connect, finish, in_thread  # noqa: E402

from srim.app.agent_backend import AgentBackend  # noqa: E402
from srim.app.agent_server import AgentServer  # noqa: E402
from srim.app.approval_dialog import ApprovalQueue  # noqa: E402
from srim.app.settings import AppSettings  # noqa: E402
from srim.app.window import MainWindow  # noqa: E402
from srim.mcp import protocol  # noqa: E402
from srim.mcp.backend import BackendError  # noqa: E402
from srim.mcp.discovery import read_endpoint  # noqa: E402

#: the bound of a wait for the dialog or an answer
WAIT = 5000


@pytest.fixture
def window(qtbot, tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    win = MainWindow(AppSettings(route="wine"), checks_fn=lambda route: [])
    qtbot.addWidget(win)
    return win


@pytest.fixture
def queue(window):
    """A queue with a short timeout, for the timeout test; the window's own queue is not used."""
    return ApprovalQueue(window, timeout_s=60)


def shown(qtbot, queue):
    """Wait until a dialog is on screen, and return it."""
    qtbot.waitUntil(lambda: queue.shown is not None, timeout=WAIT)
    return queue.shown


# --- the answers ---------------------------------------------------------------------------


def test_allow_resolves_allowed(qtbot, queue):
    answer = queue.request("claude", "start a run")
    shown(qtbot, queue).allow_button.click()
    assert answer.outcome == (True, "allowed")
    qtbot.waitUntil(lambda: queue.shown is None, timeout=WAIT)


def test_deny_resolves_denied(qtbot, queue):
    answer = queue.request("claude", "start a run")
    shown(qtbot, queue).deny_button.click()
    assert answer.outcome == (True, "denied")


def test_no_answer_in_time_resolves_timed_out(qtbot, window):
    quick = ApprovalQueue(window, timeout_s=1)
    answer = quick.request("claude", "start a run")
    qtbot.waitUntil(lambda: answer.settled, timeout=3000)
    assert answer.outcome == (True, "timed out")
    qtbot.waitUntil(lambda: quick.shown is None, timeout=WAIT)


def test_the_dialog_shows_the_client_text_title_and_countdown(qtbot, window):
    quick = ApprovalQueue(window, timeout_s=3)
    quick.request("claude 1.2", "start a run of 2 hours")
    dialog = shown(qtbot, quick)
    assert isinstance(dialog, QDialog)
    assert dialog.windowTitle() == "Agent request"
    assert dialog.isVisible()
    assert dialog.text_label.text() == "claude 1.2 wants to:"
    assert dialog.text_area.toPlainText() == "start a run of 2 hours"
    assert "3 s" in dialog.countdown_label.text()


def test_the_request_text_is_plain_text(qtbot, queue):
    """Markup in an agent's text is shown as it is: no rich text, no links."""
    text = "<b>bold</b> <a href='https://example.org'>go</a> <img src='x'>"
    queue.request("claude", text)
    dialog = shown(qtbot, queue)
    assert dialog.text_label.textFormat() == Qt.TextFormat.PlainText
    assert dialog.text_label.openExternalLinks() is False
    assert dialog.text_area.toPlainText() == text
    assert dialog.text_area.isReadOnly()


# --- the queue -----------------------------------------------------------------------------


def test_two_requests_show_one_after_the_other(qtbot, queue):
    first = queue.request("claude", "start run one")
    second = queue.request("claude", "start run two")
    dialog = shown(qtbot, queue)
    assert "run one" in dialog.text_area.toPlainText()
    assert queue.waiting == 1
    dialog.allow_button.click()
    assert first.outcome == (True, "allowed")
    qtbot.waitUntil(lambda: queue.shown is not None and queue.shown is not dialog, timeout=WAIT)
    assert "run two" in queue.shown.text_area.toPlainText()
    queue.shown.deny_button.click()
    assert second.outcome == (True, "denied")
    assert queue.waiting == 0


def test_a_cancel_while_showing_closes_the_dialog_and_shows_the_next(qtbot, queue):
    first = queue.request("claude", "start run one")
    second = queue.request("claude", "start run two")
    dialog = shown(qtbot, queue)
    first._cancel()  # the server's cancel: the peer that asked has gone
    assert first.cancelled and not first.settled
    qtbot.waitUntil(lambda: queue.shown is not None and queue.shown is not dialog, timeout=WAIT)
    assert "run two" in queue.shown.text_area.toPlainText()
    assert queue.waiting == 0
    queue.shown.allow_button.click()
    assert second.outcome == (True, "allowed")


def test_a_cancel_while_queued_removes_the_request(qtbot, queue):
    first = queue.request("claude", "start run one")
    second = queue.request("claude", "start run two")
    third = queue.request("claude", "start run three")
    dialog = shown(qtbot, queue)
    second._cancel()
    assert queue.waiting == 1
    dialog.deny_button.click()
    assert first.outcome == (True, "denied")
    qtbot.waitUntil(lambda: queue.shown is not None, timeout=WAIT)
    assert "run three" in queue.shown.text_area.toPlainText()
    assert not second.settled
    queue.shown.allow_button.click()
    assert third.outcome == (True, "allowed")


def test_the_gui_keeps_answering_while_the_dialog_is_up(qtbot, window):
    """The dialog is not modal in the sense of exec(): the other requests go on meanwhile."""
    backend = AgentBackend(window)
    answer = backend.handle("ask", {"text": "start a run"})
    qtbot.waitUntil(lambda: window.approvals.shown is not None, timeout=WAIT)
    setup = backend.handle("get_setup", {})
    assert "setup" in setup
    assert not answer.settled
    window.approvals.shown.allow_button.click()
    assert answer.outcome == (True, "allowed")


# --- closing -------------------------------------------------------------------------------


def test_escape_means_denied(qtbot, queue):
    answer = queue.request("claude", "start a run")
    qtbot.keyClick(shown(qtbot, queue), Qt.Key.Key_Escape)
    assert answer.outcome == (True, "denied")


def test_the_close_button_means_denied(qtbot, queue):
    answer = queue.request("claude", "start a run")
    shown(qtbot, queue).close()
    assert answer.outcome == (True, "denied")


def test_closing_the_window_denies_every_open_request(qtbot, window):
    backend = AgentBackend(window)
    first = backend.handle("ask", {"text": "start run one"})
    second = backend.handle("ask", {"text": "start run two"})
    qtbot.waitUntil(lambda: window.approvals.shown is not None, timeout=WAIT)
    window.close()
    assert first.outcome == (True, "denied")
    assert second.outcome == (True, "denied")
    assert window.approvals.shown is None
    late = window.approvals.request("claude", "start run three")
    assert late.outcome == (True, "denied")


# --- the default button --------------------------------------------------------------------


def test_deny_is_the_default_button(qtbot, queue):
    queue.request("claude", "start a run")
    dialog = shown(qtbot, queue)
    assert dialog.deny_button.isDefault()
    assert not dialog.allow_button.isDefault()
    assert not dialog.allow_button.autoDefault()


def test_enter_by_accident_means_denied(qtbot, queue):
    answer = queue.request("claude", "start a run")
    qtbot.keyClick(shown(qtbot, queue), Qt.Key.Key_Return)
    assert answer.outcome == (True, "denied")


# --- the socket: the agent's request through the server --------------------------------------


@pytest.fixture
def socket_server(qtbot, window):
    server = AgentServer(AgentBackend(window), parent=window)
    server.start()
    yield server
    server.stop()


def test_an_agent_request_over_the_socket_is_answered_by_the_dialog(qtbot, window, socket_server):
    def talk():
        channel, _ = connect(read_endpoint(), client="claude 1.2")
        channel.send(protocol.request(1, "ask", {"text": "start a run"}))
        reply = channel.receive()
        channel.close()
        return reply

    thread = in_thread(talk)
    qtbot.waitUntil(lambda: window.approvals.shown is not None, timeout=WAIT)
    assert window.approvals.shown.text_label.text() == "claude 1.2 wants to:"
    assert window.approvals.shown.text_area.toPlainText() == "start a run"
    window.approvals.shown.allow_button.click()
    assert finish(qtbot, thread) == {"id": 1, "result": "allowed"}


def test_a_connection_that_goes_away_while_asking_closes_its_dialog(qtbot, window, socket_server):
    channel_holder = {}

    def open_and_ask():
        channel, _ = connect(read_endpoint(), client="claude 1.2")
        channel.send(protocol.request(1, "ask", {"text": "start a run"}))
        channel_holder["channel"] = channel
        return True

    finish(qtbot, in_thread(open_and_ask))
    qtbot.waitUntil(lambda: window.approvals.shown is not None, timeout=WAIT)
    finish(qtbot, in_thread(lambda: channel_holder["channel"].close()))
    qtbot.waitUntil(lambda: window.approvals.shown is None, timeout=WAIT)
    # the dialog is gone: none is on screen, and the answer was not reported to anyone
    qtbot.waitUntil(
        lambda: not [d for d in window.findChildren(QDialog) if d.isVisible()], timeout=WAIT
    )


def test_the_agent_cannot_name_another_client_in_the_arguments(window):
    backend = AgentBackend(window)
    with pytest.raises(BackendError):
        backend.handle("ask", {"client": "someone else", "text": "start a run"})


# --- bounds and the text shown -------------------------------------------------------------


def test_a_long_text_is_cut_and_says_how_much_is_left(qtbot, queue):
    queue.request("claude", "x" * 5000)
    dialog = shown(qtbot, queue)
    assert dialog.text_area.toPlainText() == "x" * 2000 + "… (3000 more characters)"


def test_many_lines_are_one_line_and_the_dialog_stays_short(qtbot, queue):
    queue.request("claude", "\n".join(f"line {i}" for i in range(300)))
    dialog = shown(qtbot, queue)
    text = dialog.text_area.toPlainText()
    assert "\n" not in text
    assert text.startswith("line 0 line 1 line 2")
    dialog.adjustSize()
    assert dialog.height() <= 600
    assert_buttons_inside(dialog)


def test_the_buttons_stay_inside_the_dialog_for_a_long_request(qtbot, queue):
    queue.request("claude", "word " * 1000)
    dialog = shown(qtbot, queue)
    dialog.adjustSize()
    assert_buttons_inside(dialog)


def test_markup_and_bidi_controls_are_dropped_from_the_text(qtbot, queue):
    queue.request("claude", "start\u202e run\x00 now\u2066\tdone")
    text = shown(qtbot, queue).text_area.toPlainText()
    assert text == "start run now done"


def test_the_client_name_is_one_short_line(qtbot, queue):
    queue.request("claude\n\u202eBAD " + "c" * 100, "x")
    label = shown(qtbot, queue).text_label.text()
    assert label.endswith(" wants to:")
    name = label[: -len(" wants to:")]
    assert len(name) == 60 and name.endswith("…")
    assert "\n" not in label and "\u202e" not in label


def assert_buttons_inside(dialog):
    rect = dialog.rect()
    for button in (dialog.allow_button, dialog.deny_button):
        assert button.isVisible()
        assert rect.contains(button.geometry())
