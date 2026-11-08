"""run_in_thread: a step's value or error reaches the GUI thread, whatever the exception type."""

import pytest

pytest.importorskip("PySide6")
pytest.importorskip("pytestqt")

from srim.app import workers  # noqa: E402


@pytest.fixture(autouse=True)
def no_thread_left_behind(qtbot):
    yield
    qtbot.waitUntil(lambda: not workers._live, timeout=10000)


def test_a_value_reaches_done(qtbot):
    done, failed = [], []
    workers.run_in_thread(lambda: 42, done.append, failed.append)
    qtbot.waitUntil(lambda: bool(done or failed), timeout=5000)
    assert done == [42] and failed == []


@pytest.mark.parametrize(
    "error",
    [RuntimeError("boom"), SystemExit(3), KeyboardInterrupt()],
    ids=["exception", "system-exit", "keyboard-interrupt"],
)
def test_an_error_of_any_kind_reaches_failed(qtbot, error):
    done, failed = [], []

    def fn():
        raise error

    workers.run_in_thread(fn, done.append, failed.append)
    qtbot.waitUntil(lambda: bool(done or failed), timeout=5000)
    assert done == []
    assert len(failed) == 1 and failed[0] is error
