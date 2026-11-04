import os

import numpy as np
import pytest

from srim.output import Collision

SICC = os.path.join("test_files", "SiC-C")


@pytest.fixture(scope="module")
def collision():
    return Collision(SICC)


def test_every_ion_parses(collision):
    assert len(collision) == 1000
    ions = [collision[i] for i in range(len(collision))]
    assert [ion["ion_number"] for ion in ions] == list(range(1, 1001))
    # every collision belongs to its ion
    for ion in ions:
        assert all(c["ion_number"] == ion["ion_number"] for c in ion["collisions"])


def test_ion_1_values(collision):
    ion = collision[0]
    first = ion["collisions"][0]
    assert first["kinetic_energy"] == pytest.approx(4002.0)
    assert first["depth"] == pytest.approx(2717.9)
    assert first["lat_y_dist"] == pytest.approx(0.9916)
    assert first["lat_z_dist"] == pytest.approx(10.01)
    assert first["stopping_energy"] == pytest.approx(185.09)
    assert first["atom"] == "Si"
    assert first["recoil_energy"] == pytest.approx(113.61)
    assert (first["target_disp"], first["target_vac"]) == (3, 3)
    assert (first["target_replac"], first["target_inter"]) == (0, 3)
    assert len(first["cascade"]) == 3
    assert first["cascade"][0]["atom"] == 14
    np.testing.assert_allclose(first["cascade"][1]["position"], [2717.0, -1.626, 4.179])
    # a cascade without a summary line (a single recoil)
    assert ion["collisions"][1]["target_disp"] is None
    assert len(ion["collisions"][1]["cascade"]) == 1
    # footer
    assert ion["displacements"] == 14.0
    assert ion["avg_displacements"] == 14.0
    assert ion["vacancies"] == 13.0
    assert ion["transmitted_atoms"] == 1.0


def test_ion_7_and_1000_values(collision):
    ion = collision[6]
    assert len(ion["collisions"]) == 4
    c = ion["collisions"][0]
    assert (c["kinetic_energy"], c["depth"], c["atom"]) == (
        pytest.approx(3390.0),
        pytest.approx(6022.3),
        "Si",
    )
    assert c["lat_z_dist"] == pytest.approx(-15.59)
    assert len(c["cascade"]) == 2
    assert c["cascade"][1]["recoil_energy"] == pytest.approx(32.694)
    assert len(ion["collisions"][3]["cascade"]) == 19
    assert ion["displacements"] == 23.0
    assert ion["avg_displacements"] == pytest.approx(24.857)
    last = collision[999]
    assert last["ion_number"] == 1000
    assert last["displacements"] == 51.0
    assert last["avg_displacements"] == pytest.approx(49.188)


def test_comma_decimals(tmp_path):
    with open(os.path.join(SICC, "COLLISON.txt"), "rb") as f:
        text = f.read().decode("cp437")
    lines = text.splitlines(keepends=True)
    start = next(i for i, ln in enumerate(lines) if ln.startswith("│"))
    end = next(i for i, ln in enumerate(lines) if ln.startswith("- For Ion")) + 8
    body = [ln.replace(".", ",") if ln[0] in "│█-" else ln for ln in lines[start - 3 : end]]
    (tmp_path / "COLLISON.txt").write_bytes(
        "".join(lines[: start - 3] + body + lines[start - 3 : start]).encode("cp437")
    )
    ion = Collision(str(tmp_path))[0]
    assert ion["collisions"][0]["depth"] == pytest.approx(2717.9)
    assert ion["collisions"][0]["cascade"][1]["recoil_energy"] == pytest.approx(65.474)
    assert ion["avg_displacements"] == 14.0
