import os

import numpy as np
import pandas as pd
import pytest

from srim.output import EnergyToRecoils, Ioniz, NoVacancy, Phonons, Range, Results, Vacancy

D = "test_files"

IONIZ = ["depth_A", "ions_eV_per_A_per_ion", "recoils_eV_per_A_per_ion"]
PHONON = ["depth_A", "ions_phonons_per_A_per_ion", "recoils_phonons_per_A_per_ion"]
NOVAC = ["depth_A", "replacements_per_A_per_ion"]

# directory -> (ion, energy_eV, num_ions, target elements in column order)
CASES = {
    "1": ("Ni", 5_000_000.0, None, ["Ni"]),
    "comma": ("Ni", 5_000_000.0, None, ["Ni"]),
    "SiC-C": ("C", 4_500_000.0, 999, ["C", "Si"]),
}


def columns(directory):
    els = CASES[directory][3]
    return {
        Ioniz: IONIZ,
        Vacancy: ["depth_A", "knock_ons_per_A_per_ion"]
        + [f"vacancies_{e}_per_A_per_ion" for e in els],
        NoVacancy: NOVAC,
        EnergyToRecoils: ["depth_A", "ions_eV_per_A_per_ion"]
        + [f"absorbed_{e}_eV_per_A_per_ion" for e in els],
        Phonons: PHONON,
        Range: ["depth_A", "ions_atoms_per_cm3_per_atoms_per_cm2"]
        + [f"{e}_atoms_per_cm3_per_atoms_per_cm2" for e in els],
    }


@pytest.mark.parametrize("directory", list(CASES))
@pytest.mark.parametrize("cls", [Ioniz, Vacancy, NoVacancy, EnergyToRecoils, Phonons, Range])
def test_df_shape_and_columns(directory, cls):
    obj = cls(os.path.join(D, directory))
    df = obj.df
    assert isinstance(df, pd.DataFrame)
    assert df.shape[0] == 100
    assert list(df.columns) == columns(directory)[cls]
    np.testing.assert_array_equal(df["depth_A"].to_numpy(), obj.depth)


@pytest.mark.parametrize("directory", list(CASES))
def test_df_values_equal_properties(directory):
    p = os.path.join(D, directory)
    i, v, n, e, ph, r = (
        Ioniz(p),
        Vacancy(p),
        NoVacancy(p),
        EnergyToRecoils(p),
        Phonons(p),
        Range(p),
    )
    np.testing.assert_array_equal(i.df.iloc[:, 1].to_numpy(), i.ions)
    np.testing.assert_array_equal(i.df.iloc[:, 2].to_numpy(), i.recoils)
    np.testing.assert_array_equal(v.df.iloc[:, 1].to_numpy(), v.knock_ons)
    np.testing.assert_array_equal(v.df.iloc[:, 2:].to_numpy(), v.vacancies)
    np.testing.assert_array_equal(n.df.iloc[:, 1].to_numpy(), n.number)
    np.testing.assert_array_equal(e.df.iloc[:, 1].to_numpy(), e.ions)
    np.testing.assert_array_equal(e.df.iloc[:, 2:].to_numpy(), e.absorbed)
    np.testing.assert_array_equal(ph.df.iloc[:, 1].to_numpy(), ph.ions)
    np.testing.assert_array_equal(ph.df.iloc[:, 2].to_numpy(), ph.recoils)
    np.testing.assert_array_equal(r.df.iloc[:, 1].to_numpy(), r.ions)
    np.testing.assert_array_equal(r.df.iloc[:, 2:].to_numpy(), r.elements)


@pytest.mark.parametrize("cls", [Ioniz, Vacancy, NoVacancy, EnergyToRecoils, Phonons, Range])
def test_comma_equals_dot(cls):
    pd.testing.assert_frame_equal(cls(os.path.join(D, "comma")).df, cls(os.path.join(D, "1")).df)


@pytest.mark.parametrize("directory", list(CASES))
def test_metadata(directory):
    ion, energy, num_ions, _ = CASES[directory]
    obj = Ioniz(os.path.join(D, directory))
    md = obj.metadata
    assert set(md) == {"ion", "energy_eV", "num_ions", "source"}
    assert md["ion"] == ion
    assert md["energy_eV"] == pytest.approx(energy)
    assert md["num_ions"] == obj.num_ions
    if num_ions is not None:
        assert md["num_ions"] == num_ions
    assert md["source"] == os.path.join(D, directory, "IONIZ.txt")


def test_repeated_element_names_are_unique():
    # test_files/3 has Si in two layers
    df = Vacancy(os.path.join(D, "3")).df
    assert list(df.columns) == [
        "depth_A",
        "knock_ons_per_A_per_ion",
        "vacancies_W_per_A_per_ion",
        "vacancies_Si_per_A_per_ion",
        "vacancies_O_per_A_per_ion",
        "vacancies_Si_2_per_A_per_ion",
    ]


@pytest.mark.parametrize("directory", list(CASES))
def test_results_to_dataframes(directory):
    dfs = Results(os.path.join(D, directory)).to_dataframes()
    assert list(dfs) == ["ioniz", "vacancy", "novac", "e2recoil", "phonon", "range"]
    assert all(isinstance(x, pd.DataFrame) for x in dfs.values())


def test_results_to_dataframes_omits_missing_novac():
    res = Results(os.path.join(D, "1"))
    res.novac = None
    assert "novac" not in res.to_dataframes()
