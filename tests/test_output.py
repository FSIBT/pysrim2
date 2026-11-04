import os
import re
import subprocess
import sys

import numpy as np
import pytest

from srim.output import (
    Collision,
    EnergyToRecoils,
    Ioniz,
    NoVacancy,
    Phonons,
    Range,
    Results,
    SRIM_Output,
    SRIMOutputParseError,
    SRResults,
    Vacancy,
    _to_float,
)

TESTDATA_DIRECTORY = "test_files"


@pytest.mark.parametrize("directory", [("1"), ("2"), ("3"), ("4")])
def test_ioniz_init(directory):
    ion = Ioniz(os.path.join(TESTDATA_DIRECTORY, directory))
    assert ion.depth.shape == (100,)
    assert ion.ions.shape == (100,)
    assert ion.recoils.shape == (100,)


def test_read_ion_accepts_leading_decimal_energy():
    ion = SRIM_Output()._read_ion(b"Ion = H Energy = .5 keV")
    assert ion.symbol == "H"
    assert ion.energy == 500.0


@pytest.mark.parametrize("directory", [("1"), ("2"), ("3"), ("4")])
def test_phonons_init(directory):
    phonons = Phonons(os.path.join(TESTDATA_DIRECTORY, directory))
    assert phonons.depth.shape == (100,)
    assert phonons.ions.shape == (100,)
    assert phonons.recoils.shape == (100,)


@pytest.mark.parametrize("directory", [("1"), ("2"), ("3"), ("4")])
def test_vacancy_init(directory):
    vac = Vacancy(os.path.join(TESTDATA_DIRECTORY, directory))
    assert vac.depth.shape == (100,)


@pytest.mark.parametrize("directory", [("1"), ("2"), ("3"), ("4")])
def test_range_init(directory):
    range = Range(os.path.join(TESTDATA_DIRECTORY, directory))
    assert range.depth.shape == (100,)


@pytest.mark.parametrize("directory", [("1"), ("2"), ("3")])
def test_novacancy_init_full_calculation(directory):
    novac = NoVacancy(os.path.join(TESTDATA_DIRECTORY, directory))
    assert novac.depth.shape == (100,)
    assert novac.number.shape == (100,)


def test_novacancy_init_kp_calculation():
    with pytest.raises(ValueError) as excinfo:
        NoVacancy(os.path.join(TESTDATA_DIRECTORY, "4"))
    assert excinfo.value.args[0] == "NOVAC has no data for KP calculations"


@pytest.mark.parametrize("directory", [("1"), ("2"), ("3"), ("4")])
def test_energytorecoils_init(directory):
    etorec = EnergyToRecoils(os.path.join(TESTDATA_DIRECTORY, directory))
    assert etorec.depth.shape == (100,)


@pytest.mark.parametrize("directory", [("1"), ("2"), ("3")])
def test_results_init_full(directory):
    results = Results(os.path.join(TESTDATA_DIRECTORY, directory))
    assert isinstance(results.ioniz, Ioniz)
    assert isinstance(results.vacancy, Vacancy)
    assert isinstance(results.novac, NoVacancy)
    assert isinstance(results.etorecoils, EnergyToRecoils)
    assert isinstance(results.phonons, Phonons)
    assert isinstance(results.range, Range)


def test_resuls_init_kp_calculation():
    results = Results(os.path.join(TESTDATA_DIRECTORY, "4"))
    assert isinstance(results.ioniz, Ioniz)
    assert isinstance(results.vacancy, Vacancy)
    assert results.novac == None
    assert isinstance(results.etorecoils, EnergyToRecoils)
    assert isinstance(results.phonons, Phonons)
    assert isinstance(results.range, Range)


def test_results_srim_calcluation():
    results = SRResults(os.path.join(TESTDATA_DIRECTORY, "SRIM"))
    assert results.ion == {"A1": 131.293, "Z1": 54, "name": "Xenon"}
    assert results.data.shape == (6, 159)
    assert results.units == "MeV/(mg/cm2)"
    assert results.target == {
        "density atoms/cm3": 9.6421e22,
        "density g/cm3": 3.21,
        "target composition": {"C": [6, 50.0, 29.95], "Si": [14, 50.0, 70.05]},
    }


@pytest.mark.parametrize(
    "energy, expected_ev",
    [
        (b".3", 300.0),
        (b"3.", 3000.0),
        (b"0.5", 500.0),
        (b"20", 20000.0),
        (b"1.5E+03", 1.5e6),
    ],
)
def test_read_ion_parses_energy_formats(energy, expected_ev):
    header = b"Ion = Ni   Energy = " + energy + b" keV"
    ion = SRIM_Output()._read_ion(header)
    assert ion.symbol == "Ni"
    assert ion.energy == pytest.approx(expected_ev)


def test_to_float_accepts_comma_and_dot_decimals():
    assert _to_float(b"1,5E+02") == 150.0
    assert _to_float("1,5E+02") == 150.0
    assert _to_float(b"1.5E+02") == 150.0
    assert _to_float(" 2,5 ") == 2.5


@pytest.mark.parametrize(
    "cls, names",
    [
        (Ioniz, ("depth", "ions", "recoils")),
        (Phonons, ("depth", "ions", "recoils")),
        (Vacancy, ("depth", "knock_ons", "vacancies")),
        (Range, ("depth", "ions", "elements")),
        (NoVacancy, ("depth", "number")),
        (EnergyToRecoils, ("depth", "ions", "absorbed")),
    ],
)
def test_comma_decimal_tables_match_dot_decimal(cls, names):
    dot = cls(os.path.join(TESTDATA_DIRECTORY, "1"))
    comma = cls(os.path.join(TESTDATA_DIRECTORY, "comma"))
    for name in names:
        np.testing.assert_allclose(getattr(comma, name), getattr(dot, name))


def test_comma_decimal_range_total_ions_and_ion():
    dot = Range(os.path.join(TESTDATA_DIRECTORY, "1"))
    comma = Range(os.path.join(TESTDATA_DIRECTORY, "comma"))
    assert comma.num_ions == dot.num_ions == 1000
    assert comma.ion.energy == dot.ion.energy


def test_comma_decimal_results_load():
    results = Results(os.path.join(TESTDATA_DIRECTORY, "comma"))
    assert results.range.depth.shape == (100,)


def test_comma_decimal_sr_results(tmp_path):
    src = os.path.join(TESTDATA_DIRECTORY, "SRIM", "SR_OUTPUT.txt")
    with open(src, "rb") as f:
        raw = f.read()
    lines = [
        line if b"SRIM-" in line else re.sub(rb"(?<=\d)\.(?=\d|[Ee][-+]?\d)", b",", line)
        for line in raw.split(b"\n")
    ]
    (tmp_path / "SR_OUTPUT.txt").write_bytes(b"\n".join(lines))
    assert b"," in lines[9] + b"".join(lines)
    dot = SRResults(os.path.join(TESTDATA_DIRECTORY, "SRIM"))
    comma = SRResults(str(tmp_path))
    assert comma.ion == dot.ion
    assert comma.target == dot.target
    np.testing.assert_allclose(comma.data, dot.data)


def test_sr_results_existing_units_unchanged():
    results = SRResults(os.path.join(TESTDATA_DIRECTORY, "SRIM"))
    energy, _, _, rng, long_straggle, lat_straggle = results.data
    assert energy[0] == pytest.approx(0.999999)  # 999.999 eV -> keV
    assert energy[1] == pytest.approx(1.10)  # keV
    assert rng[1] == pytest.approx(28e-4)  # "28 A" -> um
    assert long_straggle[1] == pytest.approx(9e-4)  # "9 A" -> um
    assert lat_straggle[1] == pytest.approx(6e-4)  # "6 A" -> um


def test_sr_range_in_metres_converted_to_micrometres():
    dot = SRResults(os.path.join(TESTDATA_DIRECTORY, "SRIM"))
    metres = SRResults(os.path.join(TESTDATA_DIRECTORY, "SRIM_m"))
    assert metres.data[3][2] == pytest.approx(1.2e6)  # the 1.20 keV row
    expected = dot.data.copy()
    expected[3][2] = 1.2e6
    np.testing.assert_allclose(metres.data, expected)


def test_sr_unknown_range_unit_raises(tmp_path):
    with open(os.path.join(TESTDATA_DIRECTORY, "SRIM", "SR_OUTPUT.txt"), "rb") as f:
        raw = f.read()
    old = b"   1.20 keV   1.751E-01  2.329E+00      29 A  "
    new = b"   1.20 keV   1.751E-01  2.329E+00     29 nm  "
    assert raw.count(old) == 1
    (tmp_path / "SR_OUTPUT.txt").write_bytes(raw.replace(old, new))
    with pytest.raises(SRIMOutputParseError, match="nm"):
        SRResults(str(tmp_path))


def test_importing_output_does_not_change_locale():
    code = (
        "import locale; before = locale.setlocale(locale.LC_NUMERIC); "
        "import srim.output; "
        "assert locale.setlocale(locale.LC_NUMERIC) == before"
    )
    subprocess.run([sys.executable, "-c", code], check=True)


def test_read_target_with_layers_raises_not_implemented(capsys):
    # _read_target is not reachable from a public method yet; exercise it directly.
    output = (
        b"=====\r\n"
        b"Layer  1 : Si\r\n"
        b"Layer Width = 1.0 A ;\r\n"
        b"  Layer # 1- Density = 1.0 atoms/cm3 = 1.0 g/cm3\r\n"
        b"  Layer # 1-  Si = 1.0   Atomic Percent = 1.0 Mass Percent\r\n"
        b"=====\r\n"
    )
    with pytest.raises(NotImplementedError):
        SRIM_Output()._read_target(output)
    assert capsys.readouterr().out == ""


def test_collision_parsing_prints_nothing(capsys):
    collision = Collision(os.path.join(TESTDATA_DIRECTORY, "SiC-C"))
    parsed = 0
    for i in range(len(collision)):
        try:
            collision[i]
        except (ValueError, IndexError):
            # Some ions in this fixture trip over existing parser gaps; those are out of scope here.
            continue
        parsed += 1
    assert parsed > 0
    assert capsys.readouterr().out == ""


def test_sr_range_in_kilometres_converted_to_micrometres(tmp_path):
    with open(os.path.join(TESTDATA_DIRECTORY, "SRIM", "SR_OUTPUT.txt"), "rb") as f:
        raw = f.read()
    old = b"   1.20 keV   1.751E-01  2.329E+00      29 A  "
    new = b"   1.20 keV   1.751E-01  2.329E+00     0.5 km  "
    assert raw.count(old) == 1
    (tmp_path / "SR_OUTPUT.txt").write_bytes(raw.replace(old, new))
    results = SRResults(str(tmp_path))
    assert results.data[3][2] == pytest.approx(5e8)  # 0.5 km -> um


@pytest.mark.parametrize(
    "name",
    [
        "Backscat", "Collision", "EnergyToRecoils", "Ioniz", "NoVacancy", "Phonons", "Range",
        "Results", "SRIM_Output", "SRIMOutputParseError", "SRResults", "Sputter", "Transmit",
        "Vacancy", "buffered_findall", "double_regex", "int_regex", "symbol_regex",
        "energy_prefixes", "length_prefixes", "_to_float", "_prefix_factor",
    ],
)  # fmt: skip
def test_output_package_exposes_public_names(name):
    import srim.output

    assert hasattr(srim.output, name)
