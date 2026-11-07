"""Depth profiles of ionization and vacancies from a Kinchin-Pease style run."""

from pathlib import Path

import matplotlib.pyplot as plt

from srim.output import Results

TITLE = "Ionization, damage energy and vacancies against depth"
NEEDS_SRIM = False
OUTPUT = "depth_profiles.png"

FIXTURE = Path(__file__).resolve().parents[2] / "test_files" / "2"


def make(outdir):
    results = Results(str(FIXTURE))
    ioniz = results.ioniz.df
    vacancy = results.vacancy.df
    vacancy_columns = [c for c in vacancy.columns if c.startswith("vacancies_")]

    fig, (left, right) = plt.subplots(1, 2, figsize=(6, 4), dpi=100)
    left.plot(ioniz["depth_A"] / 10, ioniz["ions_eV_per_A_per_ion"], label="from ions")
    left.plot(ioniz["depth_A"] / 10, ioniz["recoils_eV_per_A_per_ion"], label="from recoils")
    left.set_xlabel("depth (nm)")
    left.set_ylabel("ionization (eV/Å/ion)")
    left.set_title("Ionization")
    left.legend()

    right.plot(vacancy["depth_A"] / 10, vacancy[vacancy_columns].sum(axis=1))
    right.set_xlabel("depth (nm)")
    right.set_title("Vacancies, summed over elements")
    right.set_ylabel("vacancies (1/Å/ion)")

    fig.suptitle("3 MeV Au into SiC")
    fig.tight_layout()

    path = Path(outdir) / OUTPUT
    fig.savefig(path, bbox_inches="tight")
    plt.close(fig)
    return path
