"""How SRIM runs: the three routes the app and ``pysrim2 install`` offer.

``native`` runs the Windows TRIM.exe directly, ``wine`` runs it in pysrim2's own wine
prefix, and ``container`` runs it in the pysrim2 image under Podman or Docker. This module
says which routes a platform offers, which one is recommended and which installer components
a route needs. It does not import Qt.
"""

from srim.installer import steps

ROUTES = ("native", "wine", "container")
ROUTE_LABELS = {
    "native": "Native TRIM.exe",
    "wine": "Wine (pysrim2's own wine prefix)",
    "container": "Container (Podman or Docker)",
}
_COMPONENTS = {
    "native": ["srim", "vb5"],
    "wine": ["wine", "srim"],
    "container": ["runtime", "image"],
}


def available_routes(system: str) -> list[str]:
    """Return the routes offered on ``system`` (``windows``, ``linux`` or ``macos``).

    wine is a Linux component, so it is offered on Linux only; macOS runs SRIM in the container.
    """
    if system == "windows":
        return ["native", "container"]
    if system == "linux":
        return ["wine", "container"]
    return ["container"]


def recommended_route() -> str:
    """Return the route to offer first on this machine.

    Same rule as the default ``pysrim2 install`` plan: native on Windows, the container when the
    default plan installs the image, wine otherwise.
    """
    if steps.current_platform() == "windows":
        return "native"
    if "image" in steps.default_components():
        return "container"
    return "wine"


def route_components(route: str) -> list[str]:
    """Return the installer components that set up ``route``; ValueError for an unknown route."""
    if route not in _COMPONENTS:
        raise ValueError(f"unknown route {route!r}; expected one of {', '.join(ROUTES)}")
    return list(_COMPONENTS[route])
