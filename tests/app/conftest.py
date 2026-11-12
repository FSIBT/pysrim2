"""Shared setup for the app tests.

The offscreen platform is chosen before Qt is imported, so the Qt tests run without a display.
Nothing here imports PySide6: the Qt-free tests in this folder run without the app extra.
"""

import os

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")


@pytest.fixture(autouse=True)
def app_home(tmp_path, monkeypatch):
    """Keep every test's settings file in a temporary folder."""
    home = tmp_path / "pysrim2-home"
    monkeypatch.setenv("PYSRIM2_HOME", str(home))
    return home


@pytest.fixture
def boron_data():
    """A complete setup in the shape of the boron reference case (one gold layer)."""
    return {
        "version": 1,
        "ion": {"symbol": "B", "energy_keV": 20.0, "angle_deg": 7.0},
        "number_ions": 200,
        "calculation": 1,
        "seed": 12345,
        "settings": {},
        "target": [
            {
                "name": "gold",
                "thickness_nm": 100.0,
                "density_g_cm3": 19.3,
                "phase": 0,
                "elements": [
                    {
                        "symbol": "Au",
                        "stoich": 1.0,
                        "displacement_eV": 25.0,
                        "lattice_eV": 3.0,
                        "surface_eV": 3.0,
                    }
                ],
            }
        ],
    }


@pytest.fixture
def write_setup():
    """Write a setup dict as a ``run.yaml`` (through the library) and return its path."""
    from srim.spec import RunSpec

    def write(path, data):
        path.parent.mkdir(parents=True, exist_ok=True)
        RunSpec.from_dict(data, base_dir=path.parent).to_yaml(path, provenance={"test": "setup"})
        return path

    return write
