"""The tools layer: every call says which mode answered, adds one activity entry (errors too), and
asks first where the rules say so. Runs are never started here: the fake backend records the
starts, and the real headless backend only refuses or lists them. Its checks and installer steps
are fakes, so nothing probes the machine, installs, runs wine or SRIM.
"""

import os

import pytest
from test_headless_status import fake_checks, fake_steps, step
from test_results import run_folder

from srim.app.settings import AppSettings
from srim.mcp.activity import ActivityLog
from srim.mcp.approval import ApprovalCodes
from srim.mcp.backend import BackendError, NeedsApp
from srim.mcp.headless import HeadlessBackend
from srim.mcp.tools import ApprovalNeeded, Tools

READY = fake_checks({"name": "trim-exe", "ok": True, "detail": "ok", "fix": ""})


class FakeBackend:
    """The backend methods the ask-first tests call. A run of ``seconds`` length, two workers by
    default on eight cores."""

    mode = "headless"

    def __init__(self, seconds=60.0, srim_ready=True, unsaved=False, path="n15.yaml"):
        self.seconds = seconds
        self.srim_ready = srim_ready
        self.unsaved = unsaved
        self.path = path
        self.setup = {"ion": {"symbol": "N", "energy_keV": 40}, "scan": None}
        self.started = []
        self.deleted = []

    def status(self):
        problem = None
        if not self.srim_ready:
            problem = "trim-exe: C:/srim/TRIM.exe. Fix: run: pysrim2 install srim"
        return {"mode": self.mode, "srim_ready": self.srim_ready, "srim_problem": problem}

    def get_setup(self):
        return {"path": self.path, "setup": self.setup, "problems": [], "modified": self.unsaved}

    def set_values(self, assignments):
        self.setup = {**self.setup, "values": list(assignments)}
        return self.get_setup()

    def estimate(self, workers=None, batch_ions=None):
        resolved = workers if workers is not None else 2
        return {"seconds": self.seconds, "workers": resolved, "cores": 8, "units": 1, "ions": 10}

    def start_run(self, workers=None, batch_ions=None):
        self.started.append((workers, batch_ions))
        return {"folder": f"run-{len(self.started)}", "estimate": self.estimate(workers)}

    def delete_run(self, folder):
        self.deleted.append(folder)
        return {"folder": folder, "deleted": True}

    def has_unsaved_changes(self):
        return self.unsaved

    def would_replace(self, path):
        return False


class Asker:
    """The client's elicitation: records what was asked and answers ``answer``."""

    def __init__(self, answer="allowed"):
        self.answer = answer
        self.asked: list[str] = []

    def __call__(self, text):
        self.asked.append(text)
        return self.answer


def no_asks(text):
    raise AssertionError(f"the tool asked, and it must not: {text}")


def make_tools(tmp_path, backend=None, ask=None, approvals=None, cores=None):
    return Tools(
        backend if backend is not None else FakeBackend(),
        ActivityLog(tmp_path / "activity.jsonl"),
        approvals if approvals is not None else ApprovalCodes(),
        AppSettings(),
        "test client 1.0",
        ask=ask,
        cores=cores,
    )


def real_backend(tmp_path, table=None):
    """A headless backend with a passing doctor, and the fake installer steps of ``table``."""
    plan = fake_steps(table or {})
    return HeadlessBackend(tmp_path, checks=READY, plan_steps=plan, default_components=lambda: [])


def complete(backend):
    """A small complete setup: boron on silicon, ten ions."""
    backend.set_section("ion", {"symbol": "B", "energy_keV": 20.0})
    backend.set_section(
        "target",
        [
            {
                "name": "Si",
                "thickness_nm": 100.0,
                "density_g_cm3": 2.33,
                "elements": [{"symbol": "Si", "stoich": 1.0}],
            }
        ],
    )
    backend.set_values(["number_ions=10", "seed=7"])
    return backend


LONG = 3 * 3600  # seconds: more than the 30 minute limit


# --- the run rules ------------------------------------------------------------------------------


def test_a_long_run_asks_and_a_denial_starts_nothing(tmp_path):
    asker = Asker("denied")
    tools = make_tools(tmp_path, FakeBackend(seconds=LONG), ask=asker)
    with pytest.raises(BackendError, match="did not allow"):
        tools.start_run()
    assert tools.backend.started == []
    assert asker.asked == ["start the run: it is estimated to take about 3.0 h (limit 30 min)"]
    assert tools.log.last(1)[0].answer == "denied"


def test_a_timed_out_answer_is_a_refusal_and_is_logged_as_such(tmp_path):
    tools = make_tools(tmp_path, FakeBackend(seconds=LONG), ask=Asker("timed out"))
    with pytest.raises(BackendError, match="did not allow"):
        tools.start_run()
    entry = tools.log.last(1)[0]
    assert entry.answer == "timed out" and entry.error is not None


def test_an_allowed_long_run_starts_and_the_answer_is_logged(tmp_path):
    tools = make_tools(tmp_path, FakeBackend(seconds=LONG), ask=Asker("allowed"))
    tools.start_run()
    assert tools.backend.started == [(2, None)]
    entry = tools.log.last(1)[0]
    assert entry.answer == "allowed" and entry.ask.startswith("start the run")


def test_without_elicitation_a_code_is_issued_and_works_once(tmp_path):
    tools = make_tools(tmp_path, FakeBackend(seconds=LONG))
    with pytest.raises(ApprovalNeeded) as info:
        tools.start_run(workers=2)
    code = info.value.code
    tools.start_run(workers=2, confirm_code=code)
    assert tools.backend.started == [(2, None)]
    assert tools.log.last(1)[0].answer == "allowed"
    with pytest.raises(ApprovalNeeded):
        tools.start_run(workers=2, confirm_code=code)
    assert tools.backend.started == [(2, None)]


def test_the_message_asks_the_user_and_names_the_code_and_its_validity(tmp_path):
    tools = make_tools(tmp_path, FakeBackend(seconds=LONG))
    with pytest.raises(ApprovalNeeded) as info:
        tools.start_run()
    assert isinstance(info.value, BackendError)
    assert str(info.value) == (
        "This needs the user's OK: start the run: it is estimated to take about 3.0 h "
        "(limit 30 min). Ask the user. If they agree, call start_run again with the same "
        f'arguments and confirm_code="{info.value.code}" (valid for 5 minutes).'
    )
    assert tools.log.last(1)[0].answer == "code issued"


def test_a_code_is_not_good_for_other_arguments(tmp_path):
    tools = make_tools(tmp_path, FakeBackend(seconds=LONG))
    with pytest.raises(ApprovalNeeded) as info:
        tools.start_run(workers=2)
    code = info.value.code
    with pytest.raises(ApprovalNeeded):
        tools.start_run(workers=3, confirm_code=code)
    assert tools.backend.started == []
    tools.start_run(workers=2, confirm_code=code)  # the refused use did not spend it
    assert tools.backend.started == [(2, None)]


def test_a_code_is_not_good_after_the_setup_changed(tmp_path):
    tools = make_tools(tmp_path, FakeBackend(seconds=LONG))
    with pytest.raises(ApprovalNeeded) as info:
        tools.start_run()
    tools.set_values(["ion.energy_keV=50"])
    with pytest.raises(ApprovalNeeded):
        tools.start_run(confirm_code=info.value.code)
    assert tools.backend.started == []


def test_an_expired_code_is_refused(tmp_path):
    now = [0.0]
    approvals = ApprovalCodes(clock=lambda: now[0])
    tools = make_tools(tmp_path, FakeBackend(seconds=LONG), approvals=approvals)
    with pytest.raises(ApprovalNeeded) as info:
        tools.start_run()
    now[0] = 301  # the code lives for 300 seconds
    with pytest.raises(ApprovalNeeded):
        tools.start_run(confirm_code=info.value.code)
    assert tools.backend.started == []


def test_a_short_run_starts_without_asking_and_is_logged(tmp_path):
    tools = make_tools(tmp_path, FakeBackend(seconds=60), ask=no_asks)
    tools.start_run()
    assert tools.backend.started == [(2, None)]
    entry = tools.log.last(1)[0]
    assert entry.tool == "start_run" and entry.ask is None and entry.answer is None


def test_the_cores_limit_applies_as_well_as_the_time(tmp_path):
    # two workers on two cores is more than half the cores: the user is asked
    tools = make_tools(tmp_path, FakeBackend(seconds=60), ask=Asker("denied"), cores=2)
    with pytest.raises(BackendError, match="did not allow"):
        tools.start_run()
    assert tools.log.last(1)[0].ask == "start the run: it uses 2 of 2 cores (limit 1)"


def test_srim_not_ready_gives_the_guidance_and_asks_nothing(tmp_path):
    tools = make_tools(tmp_path, FakeBackend(seconds=LONG, srim_ready=False), ask=no_asks)
    with pytest.raises(BackendError) as info:
        tools.start_run()
    assert not isinstance(info.value, ApprovalNeeded)
    assert str(info.value) == (
        "SRIM is not ready: trim-exe: C:/srim/TRIM.exe. Fix: run: pysrim2 install srim. "
        "See install_plan()."
    )
    entry = tools.log.last(1)[0]
    assert entry.error == str(info.value) and entry.ask is None
    assert tools.backend.started == []


def test_a_run_needs_a_saved_setup_before_anything_is_asked(tmp_path):
    tools = make_tools(tmp_path, FakeBackend(seconds=LONG, path=None), ask=no_asks)
    with pytest.raises(BackendError, match="save_setup"):
        tools.start_run()


def test_delete_always_asks(tmp_path):
    asker = Asker("allowed")
    tools = make_tools(tmp_path, FakeBackend(), ask=asker)
    tools.delete_run("n15-runs/20261115T000000Z")
    assert asker.asked == ["delete the run folder n15-runs/20261115T000000Z"]
    assert tools.backend.deleted == ["n15-runs/20261115T000000Z"]


def test_delete_without_elicitation_needs_a_code(tmp_path):
    tools = make_tools(tmp_path, FakeBackend())
    with pytest.raises(ApprovalNeeded) as info:
        tools.delete_run("n15-runs/x")
    tools.delete_run("n15-runs/x", confirm_code=info.value.code)
    assert tools.backend.deleted == ["n15-runs/x"]


# --- the install rules ------------------------------------------------------------------------


def test_an_install_step_always_asks_and_runs_its_plain_steps(tmp_path):
    calls = []
    table = {"srim": [step("fetch", calls=calls)]}
    asker = Asker("allowed")
    tools = make_tools(tmp_path, real_backend(tmp_path, table), ask=asker)
    out = tools.install_step("srim")
    assert len(asker.asked) == 1 and "srim" in asker.asked[0]
    assert out["ran"] == ["fetch"] and calls == ["fetch"]
    assert out["mode"] == "headless"


def test_an_admin_step_is_never_run_even_when_allowed(tmp_path):
    calls = []
    table = {
        "wine": [
            step("wine-packages", admin=True, commands=("sudo apt install wine",), calls=calls)
        ]
    }
    tools = make_tools(tmp_path, real_backend(tmp_path, table), ask=Asker("allowed"))
    out = tools.install_step("wine")
    assert out["ran"] == [] and out["commands"] == ["sudo apt install wine"]
    assert calls == []


def test_an_install_step_without_elicitation_needs_a_code(tmp_path):
    calls = []
    table = {"srim": [step("fetch", calls=calls)]}
    tools = make_tools(tmp_path, real_backend(tmp_path, table))
    with pytest.raises(ApprovalNeeded) as info:
        tools.install_step("srim")
    assert calls == []
    out = tools.install_step("srim", confirm_code=info.value.code)
    assert out["ran"] == ["fetch"] and calls == ["fetch"]


def test_an_install_step_denied_runs_nothing(tmp_path):
    calls = []
    table = {"srim": [step("fetch", calls=calls)]}
    tools = make_tools(tmp_path, real_backend(tmp_path, table), ask=Asker("denied"))
    with pytest.raises(BackendError, match="did not allow"):
        tools.install_step("srim")
    assert calls == []


# --- files: exports, saves, opening and new setups ----------------------------------------------


def test_an_export_into_a_folder_with_a_foreign_file_asks(tmp_path):
    folder = run_folder(tmp_path)
    (tmp_path / "out").mkdir()
    (tmp_path / "out" / "ioniz.csv").write_text("theirs", encoding="utf-8")
    asker = Asker("denied")
    tools = make_tools(tmp_path, real_backend(tmp_path), ask=asker)
    with pytest.raises(BackendError, match="did not allow"):
        tools.export(str(folder), "csv", "out")
    assert (tmp_path / "out" / "ioniz.csv").read_text(encoding="utf-8") == "theirs"
    assert len(asker.asked) == 1 and "ioniz.csv" in asker.asked[0]


def test_an_export_into_a_new_folder_does_not_ask(tmp_path):
    folder = run_folder(tmp_path)
    tools = make_tools(tmp_path, real_backend(tmp_path), ask=no_asks)
    tools.export(str(folder), "csv", "out")
    assert (tmp_path / "out" / "ioniz.csv").is_file()


def test_an_export_may_rewrite_the_files_this_session_wrote(tmp_path):
    folder = run_folder(tmp_path)
    tools = make_tools(tmp_path, real_backend(tmp_path), ask=no_asks)
    tools.export(str(folder), "csv", "out")
    out = tools.export(str(folder), "csv", "out")
    assert out["mode"] == "headless" and out["written"]


def test_an_export_passes_its_point_on(tmp_path):
    folder = run_folder(tmp_path)
    tools = make_tools(tmp_path, real_backend(tmp_path), ask=no_asks)
    tools.export(str(folder), "csv", "out", point=0)
    assert (tmp_path / "out" / "ioniz.csv").is_file()


def test_saving_over_a_file_this_session_did_not_create_asks(tmp_path):
    (tmp_path / "theirs.yaml").write_text("keep me", encoding="utf-8")
    asker = Asker("denied")
    tools = make_tools(tmp_path, complete(real_backend(tmp_path)), ask=asker)
    with pytest.raises(BackendError, match="did not allow"):
        tools.save_setup("theirs.yaml")
    assert (tmp_path / "theirs.yaml").read_text(encoding="utf-8") == "keep me"
    assert asker.asked == ["replace theirs.yaml, a file this session did not create"]


def test_saving_to_a_new_file_and_then_to_its_own_does_not_ask(tmp_path):
    tools = make_tools(tmp_path, complete(real_backend(tmp_path)), ask=no_asks)
    tools.save_setup("mine.yaml")
    tools.save_setup()
    assert (tmp_path / "mine.yaml").is_file()


def test_new_setup_with_unsaved_changes_asks_and_keeps_them(tmp_path):
    backend = complete(real_backend(tmp_path))
    asker = Asker("denied")
    tools = make_tools(tmp_path, backend, ask=asker)
    with pytest.raises(BackendError, match="did not allow"):
        tools.new_setup()
    assert backend.get_setup()["setup"]["ion"]["symbol"] == "B"
    assert asker.asked == ["discard the unsaved changes of the new setup"]


def test_open_setup_with_unsaved_changes_names_the_file_it_would_discard(tmp_path):
    backend = complete(real_backend(tmp_path))
    backend.save_setup("a.yaml")
    backend.set_values(["number_ions=20"])
    asker = Asker("denied")
    tools = make_tools(tmp_path, backend, ask=asker)
    with pytest.raises(BackendError, match="did not allow"):
        tools.open_setup("a.yaml")
    assert asker.asked == [f"discard the unsaved changes of {tmp_path / 'a.yaml'}"]


def test_opening_and_starting_a_new_setup_without_changes_do_not_ask(tmp_path):
    backend = complete(real_backend(tmp_path))
    backend.save_setup("a.yaml")
    tools = make_tools(tmp_path, backend, ask=no_asks)
    tools.open_setup("a.yaml")
    tools.new_setup()


# --- reads, edits and the log ---------------------------------------------------------------------


def test_reads_and_edits_never_ask(tmp_path):
    table = {"srim": [step("fetch")]}
    tools = make_tools(tmp_path, complete(real_backend(tmp_path, table)), ask=no_asks)
    tools.status()
    tools.get_setup()
    tools.lookup_element("N15")
    tools.find_material("SiO2")
    tools.estimate()
    tools.install_plan(["srim"])
    tools.set_values(["ion.energy_keV=30"])
    tools.set_section("run", {"number_ions": 12})


def test_a_bad_value_is_refused_and_logged_as_an_error(tmp_path):
    backend = complete(real_backend(tmp_path))
    before = backend.get_setup()
    tools = make_tools(tmp_path, backend, ask=no_asks)
    with pytest.raises(BackendError, match="energy_keV"):
        tools.set_values(["ion.energy_keV=forty"])
    assert backend.get_setup() == before
    entry = tools.log.last(1)[0]
    assert entry.tool == "set_values" and "energy_keV" in entry.error


def test_undo_needs_the_app_and_is_logged(tmp_path):
    tools = make_tools(tmp_path, real_backend(tmp_path), ask=no_asks)
    with pytest.raises(NeedsApp, match="pysrim2 app"):
        tools.undo()
    assert tools.log.last(1)[0].tool == "undo"


def test_every_call_adds_exactly_one_entry_errors_included(tmp_path):
    tools = make_tools(tmp_path, complete(real_backend(tmp_path)), ask=no_asks)
    calls = [
        lambda: tools.status(),
        lambda: tools.get_setup(),
        lambda: tools.set_values(["ion.energy_keV=forty"]),  # an error
        lambda: tools.undo(),  # an error
        lambda: tools.find_material("SiO2"),
        lambda: tools.run_status(),  # an error: there is no run
        lambda: tools.summary("missing-folder"),  # an error
    ]
    for call in calls:
        before = len(tools.log.last(10_000))
        try:
            call()
        except BackendError:
            pass
        assert len(tools.log.last(10_000)) == before + 1


def test_results_say_which_mode_answered(tmp_path):
    tools = make_tools(tmp_path, real_backend(tmp_path), ask=no_asks)
    assert tools.status()["mode"] == "headless"
    assert tools.get_setup()["mode"] == "headless"
    assert tools.lookup_element("N15")["mode"] == "headless"
    found = tools.find_material("SiO2")
    assert found["mode"] == "headless" and found["items"][0]["density_g_cm3"] > 2


def test_the_log_records_what_was_asked_and_answered(tmp_path):
    tools = make_tools(tmp_path, FakeBackend(seconds=LONG), ask=Asker("denied"))
    with pytest.raises(BackendError):
        tools.start_run(workers=2)
    entry = tools.log.last(1)[0]
    assert entry.mode == "headless"
    assert entry.client == "test client 1.0"
    assert entry.tool == "start_run"
    assert entry.summary.startswith("start a run")
    assert entry.answer == "denied"
    assert entry.error is not None


def test_a_run_that_would_save_over_a_foreign_file_asks_first(tmp_path):
    # a file another session made, opened here and edited: the start would save it
    complete(real_backend(tmp_path)).save_setup("theirs.yaml")
    backend = real_backend(tmp_path)
    backend.open_setup("theirs.yaml")
    backend.set_values(["number_ions=12"])
    before = (tmp_path / "theirs.yaml").read_text(encoding="utf-8")
    asker = Asker("denied")
    tools = make_tools(tmp_path, backend, ask=asker)
    with pytest.raises(BackendError, match="did not allow"):
        tools.start_run()
    assert asker.asked == [
        f"start the run: save the unsaved changes into {tmp_path / 'theirs.yaml'}, "
        "a file this session did not create"
    ]
    assert (tmp_path / "theirs.yaml").read_text(encoding="utf-8") == before
    assert backend.list_runs() == []


def test_the_save_reason_is_asked_without_elicitation_as_a_code(tmp_path):
    complete(real_backend(tmp_path)).save_setup("theirs.yaml")
    backend = real_backend(tmp_path)
    backend.open_setup("theirs.yaml")
    backend.set_values(["number_ions=12"])
    tools = make_tools(tmp_path, backend)
    with pytest.raises(ApprovalNeeded) as info:
        tools.start_run()
    assert "save the unsaved changes into" in str(info.value)
    assert backend.list_runs() == []


def test_saving_through_a_dangling_link_asks_and_creates_nothing(tmp_path):
    try:
        os.symlink("nowhere.yaml", tmp_path / "dangling.yaml")
    except (OSError, NotImplementedError):
        pytest.skip("symbolic links cannot be made here")
    asker = Asker("denied")
    tools = make_tools(tmp_path, complete(real_backend(tmp_path)), ask=asker)
    with pytest.raises(BackendError, match="did not allow"):
        tools.save_setup("dangling.yaml")
    assert not (tmp_path / "nowhere.yaml").exists()
    assert len(asker.asked) == 1
