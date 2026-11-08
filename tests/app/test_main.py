"""The app's startup: which saved settings the window starts with (no event loop is run)."""

import pytest

pytest.importorskip("PySide6")

from srim.app import main as app_main  # noqa: E402
from srim.app.settings import AppSettings, save_settings, settings_path  # noqa: E402
from srim.installer import steps  # noqa: E402


def test_a_saved_route_the_platform_does_not_offer_starts_as_no_route(app_home, monkeypatch):
    save_settings(AppSettings(route="wine", theme="dark"))
    before = settings_path().read_text(encoding="utf-8")
    monkeypatch.setattr(steps, "current_platform", lambda: "macos")

    settings = app_main.startup_settings()

    assert settings.route is None  # guidance then says to choose a route, and the wizard opens
    assert settings.theme == "dark"
    assert settings_path().read_text(encoding="utf-8") == before  # the file is not rewritten


def test_a_saved_route_the_platform_offers_is_kept(app_home, monkeypatch):
    save_settings(AppSettings(route="wine"))
    monkeypatch.setattr(steps, "current_platform", lambda: "linux")

    assert app_main.startup_settings().route == "wine"


def test_no_saved_route_stays_no_route(app_home, monkeypatch):
    monkeypatch.setattr(steps, "current_platform", lambda: "macos")

    assert app_main.startup_settings().route is None
