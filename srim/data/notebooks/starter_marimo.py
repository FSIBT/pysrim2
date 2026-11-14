# pysrim2 starter notebook (marimo). It is copied into a run folder and saved with the run.
# The loading cell reads the run folder as it is when the notebook loads (or when you press
# Reload, or the "Follow the run" interval passes). Run it with: marimo edit notebook.py

import marimo

__generated_with = "0.25.2"
app = marimo.App()


@app.cell
def _():
    import marimo as mo

    mo.md(
        "# Run results\n\n"
        "The results of this run folder, as they were when the notebook loaded them. "
        "The table is the scan's table; the plot is a scan's first value or the ionisation of "
        "the first point."
    )
    return (mo,)


@app.cell
def _():
    from pathlib import Path

    import matplotlib.pyplot as plt

    from srim.notebook import NOTEBOOK_DIR, RELOAD_FILE, load_run

    return NOTEBOOK_DIR, Path, RELOAD_FILE, load_run, plt


@app.cell
def _(mo):
    follow = mo.ui.refresh(options=["5s", "10s", "30s", "1m"], label="Follow the run")
    follow
    return (follow,)


@app.cell
def _(NOTEBOOK_DIR, RELOAD_FILE, mo):
    folder = mo.notebook_dir()
    reload = mo.watch.file(folder / NOTEBOOK_DIR / RELOAD_FILE)
    return folder, reload


@app.cell
def _(folder, follow, load_run, reload):
    # reload and follow are listed so that this cell runs again when either changes.
    run = load_run(folder)
    run.mark_loaded()
    return (run,)


@app.cell
def _(run):
    run.result.table
    return


@app.cell
def _(mo, plt, run):
    if not run.points():
        mo.stop(True, mo.md("No point has results yet: run the simulation, then press Reload."))
    # an unfinished run has no extracted values (only result/scan.csv has them): plot a point
    if len(run.result.table) > 1 and run.finished and run.result.scan.extract:
        run.result.plot()
    else:
        ioniz = run.point(run.points()[0]).ioniz.df
        fig, ax = plt.subplots()
        ax.plot(ioniz["depth_A"], ioniz["ions_eV_per_A_per_ion"], label="ions")
        ax.plot(ioniz["depth_A"], ioniz["recoils_eV_per_A_per_ion"], label="recoils")
        ax.set(xlabel="depth (Å)", ylabel="ionisation (eV/Å/ion)")
        ax.legend()
    return


if __name__ == "__main__":
    app.run()
