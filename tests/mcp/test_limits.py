"""The ask-first limits of the agent (Qt-free: runs without the MCP extra)."""

from srim.app.settings import AppSettings
from srim.mcp.limits import RunRequest, default_workers, reasons_to_ask


def test_a_long_run_and_many_workers_need_an_ok():
    settings = AppSettings()
    long = RunRequest(seconds=31 * 60, workers=1, cores=8, units=1)
    wide = RunRequest(seconds=60, workers=5, cores=8, units=10)
    assert reasons_to_ask(long, settings) == ["it is estimated to take about 31 min (limit 30 min)"]
    assert reasons_to_ask(wide, settings) == ["it uses 5 of 8 cores (limit 4)"]
    assert reasons_to_ask(RunRequest(60, 4, 8, 10), settings) == []


def test_both_limits_give_both_reasons_in_order():
    request = RunRequest(seconds=31 * 60, workers=5, cores=8, units=10)
    assert len(reasons_to_ask(request, AppSettings())) == 2


def test_the_limits_come_from_the_settings():
    settings = AppSettings(agent_max_minutes=120, agent_max_core_fraction=1.0)
    assert reasons_to_ask(RunRequest(90 * 60, 8, 8, 10), settings) == []


def test_a_long_run_is_given_in_hours_with_one_decimal():
    settings = AppSettings()
    request = RunRequest(seconds=2.5 * 3600, workers=1, cores=8, units=1)
    assert reasons_to_ask(request, settings) == [
        "it is estimated to take about 2.5 h (limit 30 min)"
    ]


def test_default_workers_stay_within_half_the_cores():
    assert default_workers(units=10, cores=8) == 4
    assert default_workers(units=1, cores=8) == 1
    assert default_workers(units=10, cores=1) == 1
