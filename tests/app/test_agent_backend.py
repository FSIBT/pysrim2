"""The app's agent backend (:class:`srim.app.agent_backend.AgentBackend`): the agent's edits in the
window's tabs and undo history, its runs in Progress, the undo rule, and the Deferreds.

The runs use the fake runners of ``tests/app/runners.py`` (no SRIM, wine, network or install), and
the doctor's checks are fakes. Every wait is bounded.
"""

from pathlib import Path

import pytest

pytest.importorskip("PySide6")
pytest.importorskip("pytestqt")

from runners import fixture_runner, slow_runner  # noqa: E402

from srim.app.agent_backend import AgentBackend  # noqa: E402
from srim.app.agent_server import Deferred  # noqa: E402
from srim.app.document import LOCKED_TEXT  # noqa: E402
from srim.app.model import read_setup  # noqa: E402
from srim.app.runs import ParallelChoice, plan_run  # noqa: E402
from srim.app.settings import AppSettings  # noqa: E402
from srim.app.window import MainWindow  # noqa: E402
from srim.mcp.backend import BackendError  # noqa: E402
from srim.mcp.headless import HeadlessBackend  # noqa: E402
from srim.scan.executor import mark_scan_folder  # noqa: E402

#: the bound of a run that is expected to finish on its own
RUN_TIMEOUT = 60_000
#: the bound of a stop: the workers are killed at once, so this is generous
STOP_TIMEOUT = 15_000


@pytest.fixture
def make_window(qtbot):
    def make(runner=fixture_runner, checks=None, checks_fn=None):
        fn = checks_fn if checks_fn is not None else (lambda route: checks or [])
        win = MainWindow(AppSettings(route="wine"), checks_fn=fn, run_runner=runner)
        qtbot.addWidget(win)
        return win

    return make


@pytest.fixture
def setup_file(tmp_path, boron_data, write_setup):
    """The boron reference setup, saved as ``boron/run.yaml``."""
    return write_setup(tmp_path / "boron" / "run.yaml", boron_data)


@pytest.fixture
def win(make_window, setup_file):
    window = make_window()
    window.document.open(setup_file)
    return window


@pytest.fixture
def backend(win):
    return AgentBackend(win)


def settle(qtbot, deferred, timeout=RUN_TIMEOUT):
    """Wait until ``deferred`` settles, and return its outcome ``(ok, value_or_error)``."""
    qtbot.waitUntil(lambda: deferred.settled, timeout=timeout)
    return deferred.outcome


def run_to_end(qtbot, win, timeout=RUN_TIMEOUT):
    qtbot.waitUntil(lambda: not win.run.running, timeout=timeout)


def run_once(qtbot, win):
    """Run the window's setup once with the fake runner; returns the run folder."""
    with qtbot.waitSignal(win.run.finished, timeout=RUN_TIMEOUT):
        win.start_run_with(ParallelChoice())
    return win.run.folder


# --- reading and the setup's shape ----------------------------------------------------------


def test_get_setup_has_the_headless_shape(backend, setup_file):
    view = backend.handle("get_setup", {})
    assert set(view) == {"path", "setup", "problems", "modified"}
    assert view["path"] == str(setup_file.absolute())
    assert view["setup"]["ion"]["symbol"] == "B"
    assert "scan" in view["setup"]
    assert view["modified"] is False
    assert all(set(p) == {"text", "blocking"} for p in view["problems"])


def test_get_setup_matches_the_headless_backend(backend, setup_file, tmp_path):
    headless = HeadlessBackend(tmp_path, settings=AppSettings(route="wine"))
    headless.open_setup(str(setup_file))
    assert backend.handle("get_setup", {}) == headless.get_setup()


def test_the_estimate_matches_the_headless_backend(backend, setup_file, tmp_path):
    headless = HeadlessBackend(tmp_path, settings=AppSettings(route="wine"))
    headless.open_setup(str(setup_file))
    app_estimate = backend.handle("estimate", {"workers": 1})
    assert app_estimate == headless.estimate(workers=1)
    assert set(app_estimate) == {"seconds", "workers", "cores", "units", "ions"}


# --- the agent's edits ------------------------------------------------------------------------


def test_an_agent_edit_shows_in_the_tabs_and_in_undo(qtbot, win, backend):
    backend.handle("set_values", {"assignments": ["ion.energy_keV=40"]})
    assert win.document.doc["ion"]["energy_keV"] == 40
    assert win.editors["beam"].editors["energy_keV"].value == 40
    assert win.document.undo_label == "agent: set ion.energy_keV=40"
    assert win.undo_action.text() == "&Undo agent: set ion.energy_keV=40"


def test_an_agent_edit_is_one_undo_step_and_undo_restores_the_state_before(win, backend):
    before = win.document.doc
    backend.handle("set_values", {"assignments": ["ion.energy_keV=40", "number_ions=300"]})
    assert win.document.doc["number_ions"] == 300
    backend.handle("undo", {})
    assert win.document.doc == before
    assert not win.document.can_undo


def test_set_section_changes_the_section(win, backend):
    backend.handle("set_section", {"name": "run", "value": {"number_ions": 150}})
    assert win.document.doc["number_ions"] == 150
    assert win.document.undo_label == "agent: set run"


def test_a_value_the_library_refuses_changes_nothing_and_says_what(win, backend, tmp_path):
    headless = HeadlessBackend(tmp_path, settings=AppSettings(route="wine"))
    headless.open_setup(str(win.document.path))
    with pytest.raises(BackendError) as app_error:
        backend.handle("set_values", {"assignments": ["number_ions=-5"]})
    with pytest.raises(BackendError) as headless_error:
        headless.set_values(["number_ions=-5"])
    assert str(app_error.value) == str(headless_error.value)
    assert win.document.doc["number_ions"] == 200
    assert not win.document.can_undo


def test_an_edit_with_a_bad_argument_is_refused(backend):
    with pytest.raises(BackendError, match="set_values"):
        backend.handle("set_values", {"nope": 1})


def test_an_unknown_method_is_refused(backend):
    with pytest.raises(BackendError, match="unknown method 'summary'"):
        backend.handle("summary", {})
    with pytest.raises(BackendError, match="unknown method 'no_such'"):
        backend.handle("no_such", {})


def test_an_edit_during_a_run_is_refused_with_the_locked_text(qtbot, make_window, setup_file):
    win = make_window(runner=slow_runner)
    win.document.open(setup_file)
    win.start_run_with(ParallelChoice())
    backend = AgentBackend(win)
    try:
        with pytest.raises(BackendError, match=LOCKED_TEXT):
            backend.handle("set_values", {"assignments": ["ion.energy_keV=50"]})
        with pytest.raises(BackendError, match=LOCKED_TEXT):
            backend.handle("undo", {})
        assert win.document.doc["ion"]["energy_keV"] == 20
    finally:
        win.run.stop()
        run_to_end(qtbot, win, STOP_TIMEOUT)


# --- the undo rule: the agent undoes only its own steps --------------------------------------


def test_the_agent_cannot_undo_a_user_edit_made_after_its_own(win, backend):
    backend.handle("set_values", {"assignments": ["ion.energy_keV=40"]})
    win.document.edit(lambda doc: doc.__setitem__("number_ions", 300))  # a user edit: no label
    with pytest.raises(BackendError, match="yours, not the agent's"):
        backend.handle("undo", {})
    assert win.document.doc["number_ions"] == 300
    assert win.document.doc["ion"]["energy_keV"] == 40


def test_after_the_user_undoes_their_edit_the_agent_can_undo_its_own(win, backend):
    backend.handle("set_values", {"assignments": ["ion.energy_keV=40"]})
    win.document.edit(lambda doc: doc.__setitem__("number_ions", 300))
    win.document.undo()  # the user's own step
    backend.handle("undo", {})
    assert win.document.doc["ion"]["energy_keV"] == 20


def test_the_agent_cannot_redo_a_user_step(win, backend):
    backend.handle("set_values", {"assignments": ["ion.energy_keV=40"]})
    win.document.edit(lambda doc: doc.__setitem__("number_ions", 300))
    win.document.undo()  # the user's step is now the one redo would re-apply
    with pytest.raises(BackendError, match="yours, not the agent's"):
        backend.handle("redo", {})
    assert win.document.doc["number_ions"] == 200


def test_the_agent_redoes_its_own_undone_step(win, backend):
    backend.handle("set_values", {"assignments": ["ion.energy_keV=40"]})
    backend.handle("undo", {})
    backend.handle("redo", {})
    assert win.document.doc["ion"]["energy_keV"] == 40


def test_undo_with_nothing_to_undo_says_so(backend):
    with pytest.raises(BackendError, match="nothing to undo"):
        backend.handle("undo", {})


def test_redo_with_nothing_to_redo_says_so(backend):
    with pytest.raises(BackendError, match="nothing to redo"):
        backend.handle("redo", {})


# --- files ------------------------------------------------------------------------------------


def test_would_replace_is_false_for_the_own_path_and_for_saved_paths(backend, setup_file, tmp_path):
    assert backend.handle("would_replace", {"path": str(setup_file)}) is False
    fresh = tmp_path / "fresh.yaml"
    assert backend.handle("would_replace", {"path": str(fresh)}) is False
    fresh.write_text("made by someone else\n")
    assert backend.handle("would_replace", {"path": str(fresh)}) is True
    backend.handle("save_setup", {"path": str(fresh)})
    assert backend.handle("would_replace", {"path": str(fresh)}) is False


def test_relative_paths_are_resolved_from_the_working_directory(
    monkeypatch, tmp_path, make_window, write_setup, boron_data
):
    write_setup(tmp_path / "here" / "run.yaml", boron_data)
    monkeypatch.chdir(tmp_path)
    win = make_window()
    backend = AgentBackend(win)
    view = backend.handle("open_setup", {"path": "here/run.yaml"})
    assert view["path"] == str((tmp_path / "here" / "run.yaml").absolute())


def test_save_and_open_round_trip(make_window, backend, tmp_path):
    saved = tmp_path / "out" / "saved.yaml"
    view = backend.handle("save_setup", {"path": str(saved)})
    assert view["path"] == str(saved.absolute())
    assert saved.is_file()
    other = make_window()
    reopened = AgentBackend(other).handle("open_setup", {"path": str(saved)})
    assert reopened["setup"]["ion"]["symbol"] == "B"


def test_save_without_a_path_for_an_unsaved_setup_says_so(make_window):
    backend = AgentBackend(make_window())
    with pytest.raises(BackendError, match="no file yet"):
        backend.handle("save_setup", {"path": None})


def test_open_refuses_a_file_that_is_not_a_setup(backend, tmp_path):
    bad = tmp_path / "bad.yaml"
    bad.write_text("::: not a setup\n")
    with pytest.raises(BackendError):
        backend.handle("open_setup", {"path": str(bad)})


def test_a_new_setup_has_no_path(backend):
    view = backend.handle("new_setup", {})
    assert view["path"] is None


# --- runs -------------------------------------------------------------------------------------


def test_an_agent_run_shows_in_progress_and_finishes(qtbot, win, backend, setup_file):
    result = backend.handle("start_run", {"workers": 1})
    assert win.tabs.currentWidget() is win.progress
    assert set(result["estimate"]) == {"seconds", "workers", "cores", "units", "ions"}
    run_to_end(qtbot, win)
    folder = Path(result["folder"])
    assert folder.parent == setup_file.parent / "run-runs"
    assert win.run.folder == folder
    assert backend.handle("run_status", {})["status"] == "finished"


def test_start_run_saves_an_unsaved_change_first(qtbot, win, backend, setup_file):
    backend.handle("set_values", {"assignments": ["number_ions=150"]})
    assert backend.handle("get_setup", {})["modified"] is True
    backend.handle("start_run", {"workers": 1})
    assert backend.handle("get_setup", {})["modified"] is False
    assert read_setup(setup_file)["number_ions"] == 150
    run_to_end(qtbot, win)


def test_start_without_a_file_asks_for_a_save_first(backend, make_window):
    win = make_window()
    other = AgentBackend(win)
    with pytest.raises(BackendError, match="save the setup first"):
        other.handle("start_run", {})
    assert not win.run.running


def test_a_run_started_in_the_window_is_the_latest_run_for_the_agent(qtbot, win, backend):
    run_once(qtbot, win)
    assert backend.handle("run_status", {})["status"] == "finished"
    listed = backend.handle("list_runs", {})
    assert listed[0]["folder"] == str(win.run.folder)
    assert listed[0]["status"] == "finished"


def test_run_status_without_a_run_says_so(backend):
    with pytest.raises(BackendError, match="no run"):
        backend.handle("run_status", {})


def test_pause_and_stop_act_on_the_run_in_the_window(qtbot, make_window, setup_file):
    win = make_window(runner=slow_runner)
    win.document.open(setup_file)
    backend = AgentBackend(win)
    backend.handle("start_run", {"workers": 1})
    backend.handle("control_run", {"action": "pause"})
    assert win.pause_action.isChecked()
    backend.handle("control_run", {"action": "resume"})
    assert not win.pause_action.isChecked()
    backend.handle("control_run", {"action": "stop"})
    run_to_end(qtbot, win, STOP_TIMEOUT)
    assert backend.handle("run_status", {})["status"] == "stopped"
    with pytest.raises(BackendError, match="nothing to pause"):
        backend.handle("control_run", {"action": "pause"})


# --- opening a run folder (its Deferred) --------------------------------------------------------


def test_open_run_resolves_with_its_points(qtbot, win, backend, make_window):
    folder = run_once(qtbot, win)
    fresh = make_window()
    deferred = AgentBackend(fresh).handle("open_run", {"folder": str(folder)})
    assert isinstance(deferred, Deferred)
    ok, value = settle(qtbot, deferred)
    assert ok is True
    assert value["folder"] == str(folder)
    assert value["finished"] is True
    assert [point["status"] for point in value["points"]] == ["done"]
    assert fresh.run.folder == folder


def test_open_run_of_a_folder_without_a_scan_is_refused_at_once(backend, tmp_path):
    with pytest.raises(BackendError, match="no scan.yaml"):
        backend.handle("open_run", {"folder": str(tmp_path / "nowhere")})


def test_a_deferred_settles_with_an_error_when_its_load_fails(qtbot, backend, tmp_path):
    folder = tmp_path / "broken-runs" / "20261112T000000Z"
    folder.mkdir(parents=True)
    mark_scan_folder(folder)  # the marker first: a scan folder must be empty when it is marked
    (folder / "scan.yaml").write_text("garbage: [\n")
    deferred = backend.handle("open_run", {"folder": str(folder)})
    ok, error = settle(qtbot, deferred)
    assert ok is False
    assert isinstance(error, BackendError)


def test_an_open_that_is_replaced_by_another_settles(qtbot, make_window, win):
    first = run_once(qtbot, win)
    second = run_once(qtbot, win)
    other = AgentBackend(make_window())
    waiting = other.handle("open_run", {"folder": str(first)})
    replacing = other.handle("open_run", {"folder": str(second)})
    assert waiting.settled
    ok, error = waiting.outcome
    assert ok is False
    assert "replaced" in str(error)
    ok, value = settle(qtbot, replacing)
    assert ok is True and value["folder"] == str(second)


def test_a_run_started_in_the_window_settles_a_pending_open(qtbot, make_window, win, setup_file):
    folder = run_once(qtbot, win)
    fresh = make_window()
    fresh.document.open(setup_file)  # a run needs a setup with a file of its own
    pending = AgentBackend(fresh).handle("open_run", {"folder": str(folder)})
    fresh.start_run_with(ParallelChoice())
    assert pending.settled
    ok, error = pending.outcome
    assert ok is False
    run_to_end(qtbot, fresh)


def test_a_cancelled_open_is_forgotten_and_the_next_one_works(qtbot, make_window, win, backend):
    folder = run_once(qtbot, win)
    fresh = make_window()
    other = AgentBackend(fresh)
    cancelled = other.handle("open_run", {"folder": str(folder)})
    cancelled._cancel()
    assert cancelled.cancelled and not cancelled.settled
    again = other.handle("open_run", {"folder": str(folder)})
    ok, value = settle(qtbot, again)
    assert ok is True


# --- the checks (a Deferred from a thread) ------------------------------------------------------


def test_status_settles_with_the_doctor_checks(qtbot, make_window):
    checks = [{"name": "trim-exe", "ok": True, "detail": "found", "fix": None}]
    win = make_window(checks=checks)
    deferred = AgentBackend(win).handle("status", {})
    ok, value = settle(qtbot, deferred)
    assert ok is True
    assert value["mode"] == "app"
    assert value["checks"] == checks
    assert value["srim_ready"] is True
    assert value["running"] is None


def test_status_settles_with_an_error_when_the_checks_fail(qtbot, make_window):
    def broken(route):
        raise RuntimeError("the probe broke")

    win = make_window(checks_fn=broken)
    deferred = AgentBackend(win).handle("status", {})
    ok, error = settle(qtbot, deferred)
    assert ok is False
    assert isinstance(error, BackendError)
    assert "probe broke" in str(error)


def test_a_cancelled_status_is_not_answered_and_the_late_result_is_ignored(qtbot, make_window):
    win = make_window(checks=[])
    deferred = AgentBackend(win).handle("status", {})
    deferred._cancel()  # the server does this when the window closes or the peer has gone
    qtbot.wait(300)  # the check thread finishes meanwhile
    assert deferred.cancelled
    assert not deferred.settled


# --- show_plot ---------------------------------------------------------------------------------


def test_show_plot_switches_to_the_results_tab_and_shows_the_plot(qtbot, win, backend):
    folder = run_once(qtbot, win)
    # a real SRIM point writes its run.yaml next to its outputs; the fake runner does not
    for point in plan_run(win.document.doc, win.document.base_dir, ParallelChoice()).points():
        point.spec.to_yaml(folder / "points" / point.id[:16] / "run.yaml", provenance={"t": "x"})
    with qtbot.waitSignal(win.results.canvas.drawn, timeout=10000):
        answer = backend.handle(
            "show_plot", {"folder": str(folder), "point": None, "key": "ionization"}
        )
    assert answer == {"shown": True}
    assert win.tabs.currentWidget() is win.results
    assert win.results.plot_list.currentItem() is win.results.item_for("ionization")


def test_show_plot_of_a_run_that_is_not_open_is_refused(qtbot, win, backend, tmp_path):
    run_once(qtbot, win)
    other = str(tmp_path / "other")
    with pytest.raises(BackendError, match="not open in the app"):
        backend.handle("show_plot", {"folder": other, "point": None, "key": "ionization"})


def test_show_plot_of_a_plot_the_run_cannot_give_is_refused(qtbot, win, backend):
    folder = run_once(qtbot, win)
    with pytest.raises(BackendError, match="no plot"):
        backend.handle("show_plot", {"folder": str(folder), "point": None, "key": "no-such"})


# --- ask and log: stubs until the confirm dialog and the activity panel --------------------------


def test_ask_is_not_available_yet(backend):
    deferred = backend.handle("ask", {"client": "claude", "text": "start a run"})
    assert deferred.settled
    ok, error = deferred.outcome
    assert ok is False
    assert "not available yet" in str(error)


def test_log_does_nothing_yet(backend):
    assert backend.handle("log", {"entry": {"tool": "get_setup"}}) is None


# --- the window: the app backend answers the agent's socket -------------------------------------


def test_the_window_answers_agents_with_the_app_backend(make_window):
    win = make_window()
    assert isinstance(win.agent._handler, AgentBackend)


def test_a_window_can_be_given_another_handler(qtbot):
    class Fake:
        def handle(self, method, params):
            return {"method": method}

    fake = Fake()
    win = MainWindow(AppSettings(route="wine"), checks_fn=lambda route: [], agent_handler=fake)
    qtbot.addWidget(win)
    assert win.agent._handler is fake


def test_an_agent_reaches_the_window_over_the_socket(qtbot, win):
    import threading

    from srim.mcp.discovery import read_endpoint
    from srim.mcp.protocol import Channel, hello_request, request, socket_connect

    win.start_agent_socket()
    assert win.agent.listening
    endpoint = read_endpoint()
    outcome = {}

    def talk():
        channel = Channel(socket_connect(endpoint.socket, timeout=3.0), timeout=3.0)
        channel.send(hello_request(endpoint.token, "test"))
        channel.receive()
        channel.send(request(1, "get_setup", {}))
        outcome["reply"] = channel.receive()
        channel.close()

    thread = threading.Thread(target=talk, daemon=True)
    thread.start()
    qtbot.waitUntil(lambda: "reply" in outcome, timeout=5000)
    thread.join(5)
    assert outcome["reply"]["result"]["path"] == str(win.document.path)
    win.stop_background()


# --- the tools over the app's backend: asking before a run folder replaces the setup --------


class Awaited:
    """The backend as the socket client sees it: a Deferred is waited for (the event loop runs),
    and its error is raised. The tools call the methods directly, so this stands in for the
    client here."""

    def __init__(self, backend, qtbot):
        self._backend, self._qtbot = backend, qtbot
        self.mode = backend.mode
        self.open_run_replaces_setup = backend.open_run_replaces_setup

    def __getattr__(self, name):
        method = getattr(self._backend, name)

        def call(*args, **kwargs):
            result = method(*args, **kwargs)
            if isinstance(result, Deferred):
                self._qtbot.waitUntil(lambda: result.settled, timeout=RUN_TIMEOUT)
                ok, value = result.outcome
                if not ok:
                    raise value
                return value
            return result

        return call


class Answers:
    """The client's question: records each text and answers ``answer``."""

    def __init__(self, answer):
        self.answer = answer
        self.asked: list[str] = []

    def __call__(self, text):
        self.asked.append(text)
        return self.answer


def tools_for(qtbot, win, tmp_path, answer):
    from srim.mcp.activity import ActivityLog
    from srim.mcp.approval import ApprovalCodes
    from srim.mcp.tools import Tools

    asks = Answers(answer)
    tools = Tools(
        Awaited(AgentBackend(win), qtbot),
        ActivityLog(tmp_path / "activity.jsonl"),
        ApprovalCodes(),
        AppSettings(),
        "test client 1.0",
        ask=asks,
    )
    return tools, asks


def test_opening_a_run_over_unsaved_edits_asks_and_keeps_them_when_refused(qtbot, win, tmp_path):
    folder = run_once(qtbot, win)
    win.document.edit(lambda doc: doc.__setitem__("number_ions", 300))  # the user's edit
    tools, asks = tools_for(qtbot, win, tmp_path, "denied")
    with pytest.raises(BackendError, match="did not allow"):
        tools.open_run(str(folder))
    assert asks.asked == [f"discard the unsaved changes of {win.document.path}"]
    assert win.document.doc["number_ions"] == 300  # the edit survives
    assert win.run.folder == folder


def test_opening_a_run_over_unsaved_edits_replaces_the_setup_when_allowed(qtbot, win, tmp_path):
    folder = run_once(qtbot, win)
    win.document.edit(lambda doc: doc.__setitem__("number_ions", 300))
    tools, asks = tools_for(qtbot, win, tmp_path, "allowed")
    tools.open_run(str(folder))
    assert len(asks.asked) == 1
    assert win.document.doc["number_ions"] == 200  # the run's setup, as saved
    assert not win.document.is_modified


def test_the_app_backend_declares_that_opening_a_run_replaces_the_setup(win):
    backend = AgentBackend(win)
    assert backend.open_run_replaces_setup is True
    assert backend.mode == "app"
