import os

import numpy as np
import pytest

from srim.output import Collision

SICC = os.path.join("test_files", "SiC-C")
STRAY = 56  # recoil lines numbered 0 that belong to no collision (see below)


pytestmark = pytest.mark.filterwarnings("ignore:.*stray recoil")


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


def test_iter_events_is_a_streaming_generator(collision):
    events = collision.iter_events()
    assert iter(events) is events
    first, prime, second = next(events), next(events), next(events)
    assert first == {
        "ion": 1,
        "kind": "ion",
        "energy_keV": 4002.0,
        "depth_A": 2717.9,
        "y_A": 0.9916,
        "z_A": 10.01,
        "atom": "Si",
        "recoil_energy_eV": 113.61,
        "target_displacements": 3,
        "target_vacancies": 3,
        "target_replacements": 0,
        "target_interstitials": 3,
        "atomic_number": None,
        "electronic_stopping_eV_per_A": 185.09,
        "vacancy": None,
        "replacement": None,
    }
    assert prime["kind"] == "recoil"
    assert prime["atom"] == "Si" and prime["atomic_number"] == 14
    assert prime["depth_A"] == 2718.0
    assert prime["energy_keV"] is None and prime["target_displacements"] is None
    assert (second["atom"], second["recoil_energy_eV"]) == ("C", 65.474)
    assert (second["y_A"], second["z_A"]) == (-1.626, 4.179)
    assert (second["vacancy"], second["replacement"]) == (1, 0)
    events.close()


def test_event_counts_match_the_file(collision):
    with open(os.path.join(SICC, "COLLISON.txt"), "rb") as f:
        lines = f.read().split(b"\r\n")
    ions = sum(1 for ln in lines if ln.startswith(b"\xb3") and b"Summary" not in ln)
    recoils = sum(1 for ln in lines if ln.startswith(b"\xdb"))
    events = list(collision.iter_events())
    assert sum(e["kind"] == "ion" for e in events) == ions == 8934
    # SRIM leaves stray recoil lines (recoil number 0) of cascades with a lost header line,
    # e.g. between ion 260 and 261 or after the summary of ion 68's cascade: skipped
    stray = sum(1 for ln in lines if ln.startswith(b"\xdb 00000"))
    assert stray > 0
    assert sum(e["kind"] == "recoil" for e in events) == recoils - stray
    # a cascade without a summary line has no totals
    no_summary = [e for e in events if e["kind"] == "ion" and e["target_displacements"] is None]
    # (8284 summary lines, 4 of them a second one for a cascade that already had its own)
    assert len(no_summary) == 8934 - 8284 + 4


def test_df(collision):
    df = collision.df
    assert len(df) == 8934 + 50755 - STRAY
    assert list(df.columns) == [
        "ion", "kind", "energy_keV", "depth_A", "y_A", "z_A", "atom", "recoil_energy_eV",
        "target_displacements", "target_vacancies", "target_replacements",
        "target_interstitials", "atomic_number", "electronic_stopping_eV_per_A", "vacancy",
        "replacement",
    ]  # fmt: skip
    assert df["ion"].iloc[-1] == 1000
    assert df.loc[df.kind == "ion", "energy_keV"].notna().all()
    assert df.loc[df.kind == "recoil", "energy_keV"].isna().all()
    assert df.iloc[0]["energy_keV"] == 4002.0


def test_results_collision_is_lazy_and_in_dataframes():
    from srim.output import Results

    res = Results(SICC)
    assert isinstance(res.collision, Collision)
    assert len(res.to_dataframes(include=["collision"])["collision"]) == 8934 + 50755 - STRAY


def test_results_without_collision_file(tmp_path):
    import shutil

    from srim.output import Results

    for name in os.listdir(SICC):
        if name != "COLLISON.txt":
            shutil.copy(os.path.join(SICC, name), tmp_path)
    res = Results(str(tmp_path))
    assert res.collision is None
    assert "collision" not in res.to_dataframes()
    with pytest.raises(ValueError, match="not available"):
        res.to_dataframes(include=["collision"])


def test_kinchin_pease_collision_lines(tmp_path):
    # without full cascades each collision line carries the target totals itself
    text = (
        " \n"
        "  Ion    Energy    Depth     Lateral Distance (A)   Se    Atom  Recoil    Target\n"
        "  Numb   (keV)      (A)       Y Axis    Z Axis    (eV/A)  Hit  Energy(eV) DISP.\n"
        "------------------------------------------------------------------------------\n"
        "│00001│40.02E+02│27179.E-01│ 9916.E-04│ 1001.E-02│0185.09│ Si │11361.E-02│000002│"
        "000001│000001│000001│\n"
        "==============================================================================\n"
        "- For Ion 0000001:               For All Ions to date :                      º\n"
        + "".join(
            f"- {name}  = 000001.0   Average {name}/Ion = 0000001.000  º\n"
            for name in [
                "Displacements",
                "Replacements",
                "Vacancies",
                "Interstitials",
                "Sputtered Atoms",
                "Transmitted Atoms",
            ]
        )  # fmt: skip
        + "==============================================================================\n"
        "  Ion    Energy    Depth     Lateral Distance (A)   Se    Atom  Recoil    Target\n"
    )
    (tmp_path / "COLLISON.txt").write_bytes(text.encode("cp437"))
    c = Collision(str(tmp_path))
    ion = c[0]
    assert ion["collisions"][0]["cascade"] is None
    assert ion["collisions"][0]["target_disp"] == 2
    (event,) = c.iter_events()
    assert (event["target_displacements"], event["target_interstitials"]) == (2, 1)


def test_stray_recoil_and_second_summary_do_not_overwrite_a_cascade(collision):
    # ion 68: after the summary of its 2nd cascade SRIM wrote a stray recoil (number 0)
    # and a second summary line (1 displacement) without a collision line
    (cascade,) = [c for c in collision[67]["collisions"] if c["kinetic_energy"] == 4012.0]
    assert len(cascade["cascade"]) == 1
    assert (cascade["target_disp"], cascade["target_inter"]) == (2, 2)


def test_skipped_lines_are_counted_and_warned_about_once():
    c = Collision(SICC)
    with pytest.warns(UserWarning, match="skipped 56 stray recoil.*4 cascade summary") as w:
        assert c.skipped_lines == {"recoil": 56, "summary": 4}
        list(c.iter_events())
        _ = c.df
    # only this warning counts (Windows CI can record unrelated ones in the same block)
    assert len([x for x in w if "skipped" in str(x.message)]) == 1


def test_clean_file_has_no_skipped_lines_and_no_warning(tmp_path, recwarn):
    with open(os.path.join(SICC, "COLLISON.txt"), encoding="cp437", newline="") as f:
        lines = f.readlines()
    start = next(i for i, ln in enumerate(lines) if ln.startswith("│")) - 3
    ion1_end = next(i for i, ln in enumerate(lines) if ln.startswith("- For Ion")) + 8
    text = "".join(lines[:ion1_end] + lines[start : start + 3])
    (tmp_path / "COLLISON.txt").write_bytes(text.encode("cp437"))
    c = Collision(str(tmp_path))
    assert c.skipped_lines == {"recoil": 0, "summary": 0}
    assert not recwarn.list


def test_to_dataframes_excludes_collision_unless_asked(monkeypatch):
    from srim.output import Results

    res = Results(SICC)
    monkeypatch.setattr(
        Collision, "df", property(lambda self: pytest.fail("collision.df was loaded"))
    )
    assert "collision" not in res.to_dataframes()
    monkeypatch.undo()
    only = res.to_dataframes(include="collision")
    assert list(only) == ["collision"]
    picked = res.to_dataframes(include=["vacancy", "ioniz"])
    assert list(picked) == ["ioniz", "vacancy"]
    with pytest.raises(ValueError, match="unknown table"):
        res.to_dataframes(include=["nope"])
