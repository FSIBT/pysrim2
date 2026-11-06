import os

import pytest

from srim.output import Results
from srim.plot import plot_damage_energy, plot_ionization, plot_vacancies

matplotlib = pytest.importorskip("matplotlib")
matplotlib.use("Agg")
plt = pytest.importorskip("matplotlib.pyplot")

TESTDATA_DIRECTORY = "test_files"


@pytest.fixture
def results():
    return Results(os.path.join(TESTDATA_DIRECTORY, "1"))


@pytest.fixture
def ax():
    fig, ax = plt.subplots()
    yield ax
    plt.close(fig)


def test_plot_damage_energy_smoke(results, ax):
    total = plot_damage_energy(results, ax)
    assert len(ax.lines) == 1
    assert ax.lines[0].get_label() == "Damage energy"
    assert isinstance(total, float)


def test_plot_damage_energy_uses_folder_label(results, ax):
    plot_damage_energy(results, ax, folder="run-1")
    assert ax.lines[0].get_label() == "run-1"


def test_plot_ionization_smoke(results, ax):
    plot_ionization(results, ax)
    assert [line.get_label() for line in ax.lines] == [
        "Ionization from Ions",
        "Ionization from Recoils",
    ]


def test_plot_vacancies_smoke(results, ax):
    total = plot_vacancies(results, ax)
    assert len(ax.lines) == 1
    assert ax.lines[0].get_label() == "Total vacancies at depth"
    assert isinstance(total, float)


def test_plot_vacancies_excludes_knock_ons(results, ax):
    # VACANCY.txt: knock-ons are primary knock-on atoms, not vacancies; the plotted total
    # is the sum of the element columns only (the header "Total Target Vacancies")
    df = results.vacancy.df
    elements = [c for c in df.columns if c.startswith("vacancies_")]
    expected = df[elements].sum(axis=1).to_numpy()
    plot_vacancies(results, ax)
    assert ax.lines[0].get_ydata() == pytest.approx(expected)
