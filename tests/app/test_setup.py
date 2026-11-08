"""The welcome screen and the guided setup wizard (fake steps and checks only)."""

import threading

import pytest

pytest.importorskip("PySide6")
pytest.importorskip("pytestqt")

from PySide6.QtCore import Qt  # noqa: E402
from PySide6.QtWidgets import QDialog, QLabel, QWizard  # noqa: E402

from srim.app import main as app_main  # noqa: E402
from srim.app import routes, workers  # noqa: E402
from srim.app import setup as wizard_module  # noqa: E402
from srim.app.settings import AppSettings, load_settings  # noqa: E402
from srim.app.setup import SetupWizard  # noqa: E402
from srim.app.window import MainWindow  # noqa: E402
from srim.installer.runner import RunOutcome  # noqa: E402
from srim.installer.steps import ComponentUnavailable, Step  # noqa: E402


class Recorder:
    """Fake install steps: a step is done once it has run (or from the start)."""

    def __init__(self):
        self.ran = []
        self.done = set()
        self.gate = None  # a threading.Event a step waits on, when set

    def step(self, name, admin=False, requires=(), cmds=("cmd one",)):
        def run():
            if self.gate is not None:
                self.gate.wait(5)
            self.ran.append(name)
            self.done.add(name)

        return Step(
            name,
            f"summary of {name}",
            lambda: name in self.done,
            lambda: list(cmds),
            run,
            needs_admin=admin,
            requires=requires,
        )


@pytest.fixture(autouse=True)
def no_thread_left_behind(qtbot):
    """Every test that starts a thread waits until the thread has finished (or been forgotten)."""
    yield
    qtbot.waitUntil(lambda: not workers._live, timeout=10000)


@pytest.fixture
def rec():
    return Recorder()


@pytest.fixture
def plan_for(rec):
    """A plan function: wine has an admin step; srim and the container pieces are plain."""

    def make(components):
        table = {
            "srim": [rec.step("srim")],
            "wine": [
                rec.step("wine-packages", admin=True, cmds=("sudo apt-get install wine",)),
                rec.step("wine", requires=("wine-packages",)),
            ],
            "runtime": [rec.step("runtime")],
            "image": [rec.step("image")],
        }
        steps = []
        for name in components:
            if name not in table:
                raise ComponentUnavailable(name)
            steps.extend(table[name])
        return steps

    return make


def ok_checks(route):
    return [{"name": "trim", "ok": True, "detail": "fine", "fix": ""}]


def go_to(wiz, page_index):
    while wiz.currentId() != page_index:
        wiz.next()


def wait_for_install_button(qtbot, wiz):
    qtbot.waitUntil(lambda: wiz.install_page.install_button.isEnabled(), timeout=5000)


def test_the_recommended_route_is_preselected(qtbot, monkeypatch, plan_for):
    monkeypatch.setattr(routes, "recommended_route", lambda: "container")
    wiz = SetupWizard(AppSettings(), checks_fn=lambda r: [], plan_fn=plan_for, system="linux")
    qtbot.addWidget(wiz)
    wiz.next()  # welcome -> route
    assert wiz.route_page.selected_route() == "container"
    assert "(recommended)" in wiz.route_page.button("container").text()
    assert "(recommended)" not in wiz.route_page.button("wine").text()


def test_a_saved_route_is_preselected_over_the_recommended_one(qtbot, monkeypatch, plan_for):
    monkeypatch.setattr(routes, "recommended_route", lambda: "container")
    wiz = SetupWizard(
        AppSettings(route="wine"), checks_fn=ok_checks, plan_fn=plan_for, system="linux"
    )
    qtbot.addWidget(wiz)
    assert wiz.route_page.selected_route() == "wine"


def test_the_route_page_offers_only_the_routes_of_the_system(qtbot, plan_for):
    wiz = SetupWizard(AppSettings(), checks_fn=ok_checks, plan_fn=plan_for, system="windows")
    qtbot.addWidget(wiz)
    assert wiz.route_page.routes() == ["native", "container"]


def test_the_macos_route_page_offers_only_the_container_and_says_why(qtbot, plan_for):
    wiz = SetupWizard(AppSettings(), checks_fn=ok_checks, plan_fn=plan_for, system="macos")
    qtbot.addWidget(wiz)
    assert wiz.route_page.routes() == ["container"]
    assert wiz.route_page.selected_route() == "container"
    texts = [label.text() for label in wiz.route_page.findChildren(QLabel)]
    assert "native SRIM is not supported on macOS; it runs in the container" in texts


def test_install_runs_plain_steps_and_only_shows_admin_commands(qtbot, rec, plan_for):
    wiz = SetupWizard(
        AppSettings(route="wine"), checks_fn=ok_checks, plan_fn=plan_for, system="linux"
    )
    qtbot.addWidget(wiz)
    wiz.show()
    go_to(wiz, 2)
    wait_for_install_button(qtbot, wiz)
    with qtbot.waitSignal(wiz.install_finished, timeout=10000) as sig:
        qtbot.mouseClick(wiz.install_page.install_button, Qt.LeftButton)
    assert rec.ran == ["srim"]  # the admin step never ran
    assert sig.args[0].admin_skipped[0][0] == "wine-packages"
    assert "sudo apt-get install wine" in wiz.install_page.commands_box.toPlainText()


def test_the_wizard_cannot_be_left_while_installing(qtbot, rec, plan_for):
    rec.gate = threading.Event()
    wiz = SetupWizard(
        AppSettings(route="container"), checks_fn=ok_checks, plan_fn=plan_for, system="linux"
    )
    qtbot.addWidget(wiz)
    wiz.show()
    go_to(wiz, 2)
    wait_for_install_button(qtbot, wiz)
    try:
        wiz.install_page.install_button.click()
        qtbot.waitUntil(lambda: not wiz.button(QWizard.CancelButton).isEnabled(), timeout=5000)
        assert not wiz.button(QWizard.BackButton).isEnabled()
        assert not wiz.button(QWizard.NextButton).isEnabled()
        wiz.back()
        wiz.next()
        assert wiz.currentId() == 2  # the page did not change
        wiz.reject()
        assert wiz.isVisible()
        assert "Installing" in wiz.install_page.status_text()
    finally:
        rec.gate.set()
    with qtbot.waitSignal(wiz.install_finished, timeout=10000):
        pass
    assert wiz.button(QWizard.CancelButton).isEnabled()
    assert wiz.button(QWizard.BackButton).isEnabled()


def test_closing_is_ignored_while_installing(qtbot, rec, plan_for):
    rec.gate = threading.Event()
    wiz = SetupWizard(
        AppSettings(route="container"), checks_fn=ok_checks, plan_fn=plan_for, system="linux"
    )
    qtbot.addWidget(wiz)
    wiz.show()
    go_to(wiz, 2)
    wait_for_install_button(qtbot, wiz)
    try:
        wiz.install_page.install_button.click()
        qtbot.waitUntil(lambda: not wiz.button(QWizard.CancelButton).isEnabled(), timeout=5000)
        assert not wiz.close()
        assert wiz.isVisible()
    finally:
        rec.gate.set()
    with qtbot.waitSignal(wiz.install_finished, timeout=10000):
        pass


def test_an_install_that_raises_re_enables_the_page_and_reports_it(
    qtbot, monkeypatch, rec, plan_for
):
    def broken(steps, **kwargs):
        raise RuntimeError("boom")

    monkeypatch.setattr(wizard_module, "run_steps", broken)
    wiz = SetupWizard(
        AppSettings(route="container"), checks_fn=ok_checks, plan_fn=plan_for, system="linux"
    )
    qtbot.addWidget(wiz)
    wiz.show()
    go_to(wiz, 2)
    wait_for_install_button(qtbot, wiz)
    with qtbot.waitSignal(wiz.install_finished, timeout=10000) as sig:
        wiz.install_page.install_button.click()
    assert sig.args[0].failed == "install"
    assert "boom" in wiz.install_page.status_text()
    assert "[fail] install: boom" in wiz.install_page.log.toPlainText()
    assert wiz.button(QWizard.BackButton).isEnabled()
    assert wiz.button(QWizard.CancelButton).isEnabled()
    assert wiz.install_page.copy_button.isEnabled()
    assert wiz.install_page.check_button.isEnabled()
    qtbot.waitUntil(lambda: wiz.install_page.install_button.isEnabled(), timeout=5000)


def test_a_step_that_fails_is_named_on_the_page(qtbot, monkeypatch, plan_for):
    monkeypatch.setattr(
        wizard_module,
        "run_steps",
        lambda steps, **kwargs: RunOutcome(failed="srim"),
    )
    wiz = SetupWizard(
        AppSettings(route="container"), checks_fn=ok_checks, plan_fn=plan_for, system="linux"
    )
    qtbot.addWidget(wiz)
    wiz.show()
    go_to(wiz, 2)
    wait_for_install_button(qtbot, wiz)
    with qtbot.waitSignal(wiz.install_finished, timeout=10000) as sig:
        wiz.install_page.install_button.click()
    assert sig.args[0].failed == "srim"
    assert "The install stopped at srim" in wiz.install_page.status_text()
    assert wiz.button(QWizard.CancelButton).isEnabled()


def test_an_installed_step_shows_only_its_name(qtbot, rec, plan_for):
    rec.done.add("srim")
    wiz = SetupWizard(
        AppSettings(route="wine"), checks_fn=ok_checks, plan_fn=plan_for, system="linux"
    )
    qtbot.addWidget(wiz)
    wiz.show()
    go_to(wiz, 2)
    # the wine route plans wine-packages, wine and srim
    qtbot.waitUntil(lambda: wiz.install_page.step_list.count() == 3, timeout=5000)
    items = [wiz.install_page.step_list.item(i).text() for i in range(3)]
    assert "srim: installed" in items
    assert "summary of srim" not in "".join(items)


def test_only_admin_steps_left_disables_install(qtbot, rec, plan_for):
    rec.done.update({"srim", "wine"})
    wiz = SetupWizard(
        AppSettings(route="wine"), checks_fn=ok_checks, plan_fn=plan_for, system="linux"
    )
    qtbot.addWidget(wiz)
    wiz.show()
    go_to(wiz, 2)
    qtbot.waitUntil(
        lambda: "Run the commands below" in wiz.install_page.status_text(), timeout=5000
    )
    assert not wiz.install_page.install_button.isEnabled()
    assert "sudo apt-get install wine" in wiz.install_page.commands_box.toPlainText()


def test_finish_saves_the_route(qtbot, app_home, rec, plan_for):
    wiz = SetupWizard(AppSettings(), checks_fn=ok_checks, plan_fn=plan_for, system="linux")
    qtbot.addWidget(wiz)
    wiz.show()
    go_to(wiz, 1)
    wiz.route_page.button("container").setChecked(True)
    wiz.next()  # route -> install (container: the image and runtime steps, both fake)
    wiz.next()  # install -> done
    qtbot.waitUntil(lambda: "SRIM is ready." in wiz.done_page.result_text(), timeout=5000)
    wiz.accept()
    assert load_settings().route == "container"


def test_the_done_page_lists_what_is_still_wrong(qtbot, plan_for):
    checks = [{"name": "trim", "ok": False, "detail": "missing", "fix": "run the installer"}]
    wiz = SetupWizard(AppSettings(), checks_fn=lambda r: checks, plan_fn=plan_for, system="linux")
    qtbot.addWidget(wiz)
    wiz.show()
    go_to(wiz, 1)
    wiz.route_page.button("container").setChecked(True)
    wiz.next()
    wiz.next()
    qtbot.waitUntil(lambda: "trim" in wiz.done_page.result_text(), timeout=5000)
    assert "SRIM is ready." not in wiz.done_page.result_text()


def test_a_stale_done_page_check_is_ignored(qtbot, plan_for):
    gate = threading.Event()
    calls = []

    def checks(route):
        calls.append(route)
        if len(calls) == 1:
            gate.wait(5)
            return [{"name": "old", "ok": False, "detail": "stale", "fix": "x"}]
        return ok_checks(route)

    wiz = SetupWizard(AppSettings(), checks_fn=checks, plan_fn=plan_for, system="linux")
    qtbot.addWidget(wiz)
    wiz.show()
    go_to(wiz, 1)
    wiz.route_page.button("container").setChecked(True)
    wiz.next()  # install
    wiz.next()  # done: the first check starts and blocks
    try:
        wiz.back()  # back to install (allowed: nothing is installing)
        wiz.next()  # done again: the second check runs at once
        qtbot.waitUntil(lambda: "SRIM is ready." in wiz.done_page.result_text(), timeout=5000)
    finally:
        gate.set()
    qtbot.waitUntil(lambda: not workers._live, timeout=5000)
    assert "SRIM is ready." in wiz.done_page.result_text()
    assert "stale" not in wiz.done_page.result_text()


def test_a_check_that_outlives_finish_is_ignored(qtbot, app_home, plan_for):
    gate = threading.Event()

    def checks(route):
        gate.wait(5)
        return [{"name": "late", "ok": False, "detail": "x", "fix": "y"}]

    wiz = SetupWizard(AppSettings(), checks_fn=checks, plan_fn=plan_for, system="linux")
    qtbot.addWidget(wiz)
    wiz.show()
    go_to(wiz, 1)
    wiz.route_page.button("container").setChecked(True)
    wiz.next()
    wiz.next()
    try:
        wiz.accept()
        assert wiz.is_closed()
    finally:
        gate.set()
    qtbot.waitUntil(lambda: not workers._live, timeout=5000)
    assert load_settings().route == "container"


def test_cancel_does_not_save_the_route(qtbot, app_home, plan_for):
    settings = AppSettings()
    wiz = SetupWizard(settings, checks_fn=ok_checks, plan_fn=plan_for, system="linux")
    qtbot.addWidget(wiz)
    wiz.show()
    go_to(wiz, 1)
    wiz.route_page.button("container").setChecked(True)
    wiz.reject()
    assert settings.route is None
    assert load_settings().route is None


def test_an_unavailable_component_is_shown_on_the_page(qtbot):
    def plan(components):
        raise ComponentUnavailable("vb5", "component vb5 is only for Windows")

    wiz = SetupWizard(
        AppSettings(route="native"), checks_fn=ok_checks, plan_fn=plan, system="windows"
    )
    qtbot.addWidget(wiz)
    wiz.show()
    go_to(wiz, 2)
    assert "component vb5 is only for Windows" in wiz.install_page.status_text()
    assert not wiz.install_page.install_button.isEnabled()
    assert wiz.button(QWizard.NextButton).isEnabled()


def test_the_window_opens_setup_and_shows_geometry_after_it(qtbot, monkeypatch):
    checks_runs = []

    def checks(route):
        checks_runs.append(route)
        return []

    win = MainWindow(AppSettings(route="wine"), checks_fn=checks)
    qtbot.addWidget(win)
    win.tabs.setCurrentIndex(2)
    monkeypatch.setattr(SetupWizard, "exec", lambda self: QDialog.DialogCode.Accepted)
    before = len(checks_runs)
    win.open_setup()
    qtbot.waitUntil(lambda: len(checks_runs) > before, timeout=5000)
    assert win.tabs.currentIndex() == 0


def test_a_cancelled_setup_leaves_the_tab_alone_but_checks_again(qtbot, monkeypatch):
    # Cancel can follow an install that already ran, so the window checks again either way.
    runs = []
    win = MainWindow(AppSettings(route="wine"), checks_fn=lambda r: runs.append(r) or [])
    qtbot.addWidget(win)
    win.tabs.setCurrentIndex(2)
    monkeypatch.setattr(SetupWizard, "exec", lambda self: QDialog.DialogCode.Rejected)
    win.open_setup()
    qtbot.waitUntil(lambda: runs == ["wine"], timeout=5000)
    assert win.tabs.currentIndex() == 2


def test_open_setup_never_runs_two_wizards_and_not_while_closing(qtbot, monkeypatch):
    execs = []
    win = MainWindow(AppSettings(route="wine"), checks_fn=lambda r: [])
    qtbot.addWidget(win)

    def fake_exec(self):
        execs.append(True)
        win.open_setup()  # a nested request while the wizard is open does nothing
        return QDialog.DialogCode.Rejected

    monkeypatch.setattr(SetupWizard, "exec", fake_exec)
    win.open_setup()
    assert execs == [True]
    win.close()
    win.open_setup()
    assert execs == [True]


def test_the_setup_opens_on_the_first_check_that_needs_it(qtbot, monkeypatch):
    opened = []
    failing = [{"name": "trim", "ok": False, "detail": "x", "fix": "y"}]
    win = MainWindow(AppSettings(route="wine"), checks_fn=lambda r: [])
    qtbot.addWidget(win)
    monkeypatch.setattr(win, "open_setup", lambda: opened.append(True))
    app_main.open_setup_when_needed(win)
    with qtbot.waitSignal(win.checked, timeout=5000):
        win.refresh_checks()
    qtbot.wait(50)
    assert opened == []  # the route is set and every check passes

    win2 = MainWindow(AppSettings(route="wine"), checks_fn=lambda r: failing)
    qtbot.addWidget(win2)
    monkeypatch.setattr(win2, "open_setup", lambda: opened.append(True))
    app_main.open_setup_when_needed(win2)
    with qtbot.waitSignal(win2.checked, timeout=5000):
        win2.refresh_checks()
    qtbot.waitUntil(lambda: opened == [True], timeout=5000)  # opened on the next loop turn
    with qtbot.waitSignal(win2.checked, timeout=5000):
        win2.refresh_checks()
    qtbot.wait(50)
    assert opened == [True]  # a later check does not reopen it
