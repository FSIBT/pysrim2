import pytest

pytest.importorskip("PySide6")

from srim import cli  # noqa: E402
from srim.installer import app as app_install  # noqa: E402


def test_pysrim2_app_calls_the_app_main_when_the_extra_is_installed(monkeypatch):
    import srim.app.main as app_main

    calls = []
    monkeypatch.setattr(app_install, "app_supported", lambda: True)
    monkeypatch.setattr(app_install, "app_available", lambda: True)
    monkeypatch.setattr(app_main, "main", lambda argv=None: calls.append(argv) or 0)
    assert cli.main(["app"]) == 0
    assert calls == [[]]
