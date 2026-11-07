# Examples

Runnable examples come in two forms: scripts, which print or write results to a folder,
and notebooks, which show the same APIs step by step with plots and tables. Both are in
the repository.

## Scripts

The scripts are in
[`examples/scripts`](https://github.com/FSIBT/pysrim2/tree/main/examples/scripts). The
boron script is the reference case of the [agent route](agents.md) too.

| Script | What it does |
|---|---|
| [`boron_in_gold_on_silicon.py`](https://github.com/FSIBT/pysrim2/blob/main/examples/scripts/boron_in_gold_on_silicon.py) `[ions]` | The reference case: 20 keV boron at 7 degrees into 100 nm Au on Si. Prints the mean depth (about 282 Å for 200 ions, seed 12345), the straggling and the damage peak depth. |
| [`multilayer_damage.py`](https://github.com/FSIBT/pysrim2/blob/main/examples/scripts/multilayer_damage.py) `[ions] [folder]` | A full-cascade run through a three-layer target (Au / Si / Al). Writes the vacancy profile to `vacancy_profile.csv`. |
| [`stopping_table.py`](https://github.com/FSIBT/pysrim2/blob/main/examples/scripts/stopping_table.py) `[folder]` | SRIM's SR module: stopping power and range of He in Si from 10 keV to 1 MeV, as `stopping_table.csv`. Fast. |
| [`run_yaml_example/`](https://github.com/FSIBT/pysrim2/tree/main/examples/scripts/run_yaml_example) | The boron case as a `run.yaml`, for `pysrim2 run run.yaml --set ion.energy_keV=30`. |
| [`angle_energy_scan.py`](https://github.com/FSIBT/pysrim2/blob/main/examples/scripts/angle_energy_scan.py) `[folder]` | A scan of B into 1000 nm of Si at 20 and 40 keV, each at 0 and 30 degrees, on two worker processes. Writes `result/scan.csv`. |
| [`scan.yaml`](https://github.com/FSIBT/pysrim2/blob/main/examples/scripts/scan.yaml) | The same scan as a file: `pysrim2 scan run scan.yaml --ui plain`. |
| [`beam_energy_spread.py`](https://github.com/FSIBT/pysrim2/blob/main/examples/scripts/beam_energy_spread.py) `[folder]` | A beam with a 1 keV Gaussian energy spread, in exact and in binned mode. Writes `beam_energy_spread.csv`. |

## Notebooks

The notebooks are in
[`examples/notebooks`](https://github.com/FSIBT/pysrim2/tree/main/examples/notebooks).
They are committed without outputs.

| Notebook | What it shows |
|---|---|
| [`Analysis.ipynb`](https://github.com/FSIBT/pysrim2/blob/main/examples/notebooks/Analysis.ipynb) | Running TRIM and turning every output table into a pandas DataFrame, with a summary. |
| [`Beam.ipynb`](https://github.com/FSIBT/pysrim2/blob/main/examples/notebooks/Beam.ipynb) | A beam with an energy spread, in exact and in binned mode, with plots. |
| [`Collision.ipynb`](https://github.com/FSIBT/pysrim2/blob/main/examples/notebooks/Collision.ipynb) | Collisions and cascades: streaming `COLLISON.txt` with `Collision.iter_events()` and exporting it in chunks. |
| [`Multilayer.ipynb`](https://github.com/FSIBT/pysrim2/blob/main/examples/notebooks/Multilayer.ipynb) | A layered target described once as a run spec (`run.yaml`) and used for TRIM, the command line and the exports. |
| [`Regular_SRIM_Calculation.ipynb`](https://github.com/FSIBT/pysrim2/blob/main/examples/notebooks/Regular_SRIM_Calculation.ipynb) | Stopping power and range with SRIM's SR module, which needs no ion histories. Provided by Arto Javanainen. |
| [`Scan.ipynb`](https://github.com/FSIBT/pysrim2/blob/main/examples/notebooks/Scan.ipynb) | A parameter scan with a live view: the same run for several energies, on worker processes. |
| [`SiC.ipynb`](https://github.com/FSIBT/pysrim2/blob/main/examples/notebooks/SiC.ipynb) | Damage maps of ions in SiC from full cascades, showing where the collisions happen. |

## Run them

Install pysrim2 and set up SRIM once. Check the setup with `pysrim2 doctor`:

```shell
pip install pysrim2
pysrim2 install
pysrim2 doctor
```

Then run a script from any folder, for example `python boron_in_gold_on_silicon.py`.
Each script writes its results to a local `output` folder.

The scripts also run in the container, which needs no SRIM on your machine. See
{doc}`container` for the commands and the Docker seccomp profile. The
[examples README](https://github.com/FSIBT/pysrim2/blob/main/examples/scripts/README.md)
has the full list of commands.

The notebooks find SRIM in its default location, natively or in the container, so run
`pysrim2 install` first. The beam plots need matplotlib: `pip install "pysrim2[plot]"`.

SRIM is free for non-commercial use only. See the `LEGAL.rtf` file of your SRIM copy.
