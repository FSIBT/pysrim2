import pytest

from srim.app.routes import (
    ROUTE_LABELS,
    ROUTES,
    available_routes,
    recommended_route,
    route_components,
)
from srim.installer import steps
from srim.installer.steps import Step
from srim.installer.wine import WineStatus


def _step(name):
    return Step(name, "s", lambda: False, lambda: [], lambda: None)


@pytest.fixture
def registry(monkeypatch):
    monkeypatch.setattr(steps, "_REGISTRY", {})
    for name in ("srim", "vb5", "wine", "image", "runtime", "skill"):
        steps.register(name, lambda name=name: _step(name))


def configure(monkeypatch, system, wine=False, runtime=None):
    monkeypatch.setattr(steps, "current_platform", lambda: system)
    monkeypatch.setattr(steps, "container_runtime", lambda: runtime)
    status = WineStatus("/usr/bin/wine" if wine else None, None, None, None, None, False)
    monkeypatch.setattr(steps, "wine_status", lambda: status)


@pytest.mark.parametrize(
    ("system", "wine", "runtime", "expected"),
    [
        ("windows", False, None, "native"),
        ("linux", True, "docker", "wine"),
        ("linux", False, None, "wine"),
        ("linux", False, "podman", "container"),
        ("macos", False, "docker", "container"),
    ],
)
def test_recommended_route_follows_the_default_install(
    monkeypatch, registry, system, wine, runtime, expected
):
    configure(monkeypatch, system, wine, runtime)
    assert recommended_route() == expected


def test_available_routes_per_platform():
    assert available_routes("windows") == ["native", "container"]
    assert available_routes("linux") == ["wine", "container"]
    assert available_routes("macos") == ["wine", "container"]


def test_labels_cover_every_route():
    assert set(ROUTE_LABELS) == set(ROUTES) == {"native", "wine", "container"}


def test_route_components():
    assert route_components("native") == ["srim", "vb5"]
    assert route_components("wine") == ["wine", "srim"]
    assert route_components("container") == ["runtime", "image"]
    with pytest.raises(ValueError):
        route_components("vm")
