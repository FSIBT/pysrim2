import numpy as np


def plot_damage_energy(results, ax, folder=None):
    """Plot damage energy (ions + recoils) per unit depth

    Parameters
    ----------
    results : :class:`srim.output.Results`
        results from srim calcualtion
    ax : matplotlib.Axes
        matplotlib axes to plot into
    folder : :obj:`str`, optional
        label for the line; defaults to ``"Damage energy"``
    """
    phon = results.phonons
    dx = max(phon.depth) / 100.0  # to units of Angstroms
    energy_damage = (phon.ions + phon.recoils) * dx
    label = folder if folder is not None else "Damage energy"
    ax.plot(phon.depth, energy_damage / phon.num_ions, label=label)
    return sum(energy_damage)


def plot_ionization(results, ax):
    """Plot ionization (ion vs recoils) per unit depth

    Parameters
    ----------
    results : :class:`srim.output.Results`
        results from srim calcualtion
    ax : matplotlib.Axes
        matplotlib axes to plot into
    """
    ioniz = results.ioniz
    dx = max(ioniz.depth) / 100.0  # to units of Angstroms
    ax.plot(ioniz.depth, ioniz.ions, label="Ionization from Ions")
    ax.plot(ioniz.depth, ioniz.recoils, label="Ionization from Recoils")


def plot_vacancies(results, ax):
    """Plot vacancies (ion + recoils produced) per unit depth

    Parameters
    ----------
    results : :class:`srim.output.Results`
        results from srim calcualtion
    ax : matplotlib.Axes
        matplotlib axes to plot into
    """
    vac = results.vacancy
    # vacancies of the elements only: knock-ons are not vacancies (see VACANCY.txt)
    vacancy_depth = np.sum(vac.vacancies, axis=1)
    ax.plot(vac.depth, vacancy_depth, label="Total vacancies at depth")
    return sum(vacancy_depth)
