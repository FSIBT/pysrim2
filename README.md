![srim heatmap](https://raw.githubusercontent.com/FSIBT/pysrim2/main/examples/images/length-heatmap-log-cropped.png)

# pysrim2: Automation, Analysis, and Plotting of SRIM Calculations

> **pysrim2 is a maintained continuation of
> [pysrim](https://gitlab.com/costrouc/pysrim) by Chris Ostrouchov.**
> See [what changed](https://pysrim2.readthedocs.io/en/latest/changes-from-pysrim.html).

[![PyPI](https://img.shields.io/pypi/v/pysrim2.svg)](https://pypi.org/project/pysrim2/)
[![CI](https://github.com/FSIBT/pysrim2/actions/workflows/ci.yml/badge.svg)](https://github.com/FSIBT/pysrim2/actions/workflows/ci.yml)
[![Documentation](https://readthedocs.org/projects/pysrim2/badge/?version=latest)](https://pysrim2.readthedocs.io/en/latest/)
[![License](https://img.shields.io/pypi/l/pysrim2.svg)](https://github.com/FSIBT/pysrim2/blob/main/LICENSE)
[![Python versions](https://img.shields.io/pypi/pyversions/pysrim2.svg)](https://pypi.org/project/pysrim2/)
[![Downloads](https://img.shields.io/pypi/dm/pysrim2.svg)](https://pypistats.org/packages/pysrim2)
[![Container](https://img.shields.io/badge/ghcr.io-fsibt%2Fpysrim2-blue?logo=docker)](https://github.com/FSIBT/pysrim2/pkgs/container/pysrim2)
[![uv](https://img.shields.io/endpoint?url=https://raw.githubusercontent.com/astral-sh/uv/main/assets/badge/v0.json)](https://github.com/astral-sh/uv)
[![Ruff](https://img.shields.io/endpoint?url=https://raw.githubusercontent.com/astral-sh/ruff/main/assets/badge/v2.json)](https://github.com/astral-sh/ruff)
[![pre-commit](https://img.shields.io/badge/pre--commit-enabled-brightgreen?logo=pre-commit)](https://github.com/pre-commit/pre-commit)

`pysrim2` is a Python package that wraps and extends SRIM, a popular
tool for simulating ions traveling through a material. It addresses
several pain points of SRIM: automation and crash recovery of SRIM
calculations, parsing of all output files, publication quality plots,
and running on Linux and macOS.

The import name is unchanged: `import srim`. Because both packages
provide the `srim` module, `pysrim2` cannot be installed alongside the
original `pysrim`.

Documentation: <https://pysrim2.readthedocs.io/en/latest/>

# Installation

pysrim2 has two parts that are installed separately, and most users want
both: the library (`import srim`, used in your own Python code) and the
command line (`pysrim2 install`, which sets up SRIM itself).

```bash
# 1. The library, pick the way you work:
uv add pysrim2                                  # in a uv project
uv venv && source .venv/bin/activate && uv pip install pysrim2   # uv venv
python -m venv .venv && source .venv/bin/activate && pip install pysrim2  # pip venv
uv run --with pysrim2 my_simulation.py          # one-off script, nothing installed

# 2. The command line, which installs SRIM (it does not make `import srim`
#    available to your own Python):
uv tool install pysrim2 && pysrim2 install
pysrim2 install --dry-run     # show every step, change nothing
pysrim2 doctor                # check the result
```

If you have `pysrim` installed, remove it first: in a uv project
`uv remove pysrim && uv add pysrim2`; in a uv venv
`uv pip uninstall pysrim && uv pip install pysrim2`; with pip
`pip uninstall pysrim && pip install pysrim2`. Python 3.11 or newer is
required. SRIM runs natively on Windows and (through wine, in a private prefix
that never touches `~/.wine`) on Linux; on macOS it runs in the container
`ghcr.io/fsibt/pysrim2` (linux/amd64, Podman or Docker). See the
[installation guide](https://pysrim2.readthedocs.io/en/latest/installation.html),
the [container page](https://pysrim2.readthedocs.io/en/latest/container.html)
(Docker 29 needs a seccomp profile there) and the
[command line reference](https://pysrim2.readthedocs.io/en/latest/cli.html).
`pysrim2 install skill` installs an
[agent skill](https://pysrim2.readthedocs.io/en/latest/agents.html) so coding
agents can run SRIM for you.

SRIM is not part of this package. SRIM © SRIM.com (James F. Ziegler) — free
for non-commercial use only; see `LEGAL.rtf`, which the installer keeps next
to every copy. You do not need SRIM if you only analyse existing output files.

# Features

## Automate running SRIM and TRIM on all operating systems

While TRIM is a great code it has many downsides regarding
automation. The `TRIM.IN` input file is tedious to write yourself and
the gui that constructs the `TRIM.IN` will crash at unexpeced moments.
One of these crashes everyone has encountered is the fact that a float
text field can never be empty. TRIM also has a tendancy to crash
becuase it stores all cascades in memory. Meaning that for large runs
with full cascades greater than 1,000 ions it will run out of
memory. `pysrim2` addresses all of these issues by providing a simple
API wrapper for the input file (supporting all of the features),
ability to run on all operating systems (using
[wine](https://appdb.winehq.org/objectManager.php?sClass=version&iId=13202)
for linux and OSX), and allowing batch runs of calculations see [this
notebook
example](https://github.com/FSIBT/pysrim2/blob/main/examples/notebooks/SiC.ipynb).

Below is a hello world example of using `pysrim2` for running a TRIM
calcualtion. `trim.run()` uses the SRIM directory that `pysrim2 install`
set up (see `srim.paths.srim_dir()`); `pysrim2` adds all the necessary
input files. With wine and `xvfb-run` installed it runs headless;
otherwise a TRIM window may appear.

``` python
from srim import Ion, Layer, Target, TRIM

# Construct a 3MeV Nickel ion
ion = Ion("Ni", energy=3.0e6)

# Construct a layer of nick 20um thick with a displacement energy of 30 eV
layer = Layer(
    {"Ni": {"stoich": 1.0, "E_d": 30.0, "lattice": 0.0, "surface": 3.0}}, density=8.9, width=20000.0
)

# Construct a target of a single layer of Nickel
target = Target([layer])

# Initialize a TRIM calculation with given target and ion for 25 ions, quick calculation
trim = TRIM(target, ion, number_ions=25, calculation=1)

# takes about 10 seconds on my laptop; uses the default SRIM directory
# (pass a path to run() to use a SRIM installation somewhere else)
results = trim.run()
# If all went successfully, 25 ions were run (a TRIM window may have appeared
# if wine or xvfb-run is not available)!
# results is `srim.output.Results` and contains all output files parsed

# every output is a pandas DataFrame with SRIM's units in the column names
print(results.ioniz.df.head())  # depth_A, ions_eV_per_A_per_ion, ...
print(results.range.summary)  # range and straggling as SRIM prints them
```

With the setting `ranges=1` TRIM also writes the final position of every ion, and
`implant_summary` computes SRIM's range and straggling values from it:

``` python
from srim.analysis import implant_summary

trim = TRIM(target, ion, number_ions=100, calculation=1, ranges=1)
results = trim.run()
print(implant_summary(results.range3d.df))  # mean_depth_A, straggle_depth_A, ...
results.to_parquet("out")  # needs pysrim2[parquet]; also .to_hdf5() and .to_csv()
```

The same run can be described in a `run.yaml` and started with
`pysrim2 run run.yaml`. See the pages on
[DataFrames](https://pysrim2.readthedocs.io/en/latest/dataframes.html),
[`run.yaml`](https://pysrim2.readthedocs.io/en/latest/run-spec.html) and
[export](https://pysrim2.readthedocs.io/en/latest/export.html).
```

See [documentation](https://pysrim2.readthedocs.io/en/latest/) for all available options.

## Copy SRIM output files to directory

Every `run()` happens in its own private copy of the SRIM directory (so several
runs can be active at once). Pass `output_dir=` to get the output files in a
directory of your liking, or copy them afterwards from `results.directory`:

``` python
from srim import TRIM

results = trim.run(output_dir="/home/user/scratch/srim")
# or: TRIM.copy_output_files(results.directory, "/home/user/scratch/srim")
```

## Parameter scans

A scan runs a calculation over a grid or a list of parameter values, in worker processes,
and keeps every run in a cache, so a rerun computes nothing new. Define it with
`srim.scan.Scan` (or a `scan:` section in a `run.yaml`) and run it with `run_scan`, or with
`pysrim2 scan run scan.yaml`:

``` python
from srim.scan import Scan, run_scan

scan = Scan(base, grid={"ion.energy_keV": [10.0, 20.0, 30.0]}, ions=1000, seed=1)
result = run_scan(scan)  # a table with one row per point, and a scan folder
```

Precision targets, batches of a fixed ion count, the result table, Slurm and containers are
described in [docs/scans.md](https://github.com/FSIBT/pysrim2/blob/main/docs/scans.md).

## Post processes SRIM output as numpy arrays

By far the hardest part about running TRIM calculations is analyzing
the output files. `pysrim2` comes with parsers for
[IONIZ.txt](https://pysrim2.readthedocs.io/en/latest/source/srim.html#srim.output.Ioniz),
[VACANCY.txt](https://pysrim2.readthedocs.io/en/latest/source/srim.html#srim.output.Vacancy),
[NOVAC.txt](https://pysrim2.readthedocs.io/en/latest/source/srim.html#srim.output.NoVacancy),
[E2RECOIL.txt](https://pysrim2.readthedocs.io/en/latest/source/srim.html#srim.output.EnergyToRecoils),
[PHONON.txt](https://pysrim2.readthedocs.io/en/latest/source/srim.html#srim.output.Phonons),
[RANGE.txt](https://pysrim2.readthedocs.io/en/latest/source/srim.html#srim.output.Range),
and
[COLLISON.txt](https://pysrim2.readthedocs.io/en/latest/source/srim.html#srim.output.Collision). The
COLLISON.txt file can get quite large so the `Collision` parser uses a
buffered reader that can handle any file size. Additinally a class
[srim.output.Results](https://pysrim2.readthedocs.io/en/latest/source/srim.html#srim.output.Results)
will processes all output files in a directory and provide an
object whose attributes are the parsed output files (for example
`results.phonons`). `pysrim2` comes with some
helpful plotting utilities such a plotting the DPA vs depth. However,
`pysrim2's` most powerful feature is that all of the text files are
exposed as numpy arrays. The example below shows how to plot DPA using
a simple math and numpy. This enables the user to seamlessly use TRIM
and do analysis.

``` python
from srim.output import Phonons, Ioniz


def plot_damage_energy(folder, ax):
    phon = Phonons(folder)
    dx = max(phon.depth) / 100.0
    energy_damage = (phon.ions + phon.recoils) * dx
    ax.plot(phon.depth, energy_damage / phon.num_ions, label="{}".format(folder))
    return sum(energy_damage)


def plot_ionization(folder, ax):
    ioniz = Ioniz(folder)
    dx = max(ioniz.depth) / 100.0
    ax.plot(ioniz.depth, ioniz.ions, label="Ionization from Ions")
    ax.plot(ioniz.depth, ioniz.recoils, label="Ionization from Recoils")
```

Set `folders` to list of directories to SRIM outputs. See
[Analysis](https://github.com/FSIBT/pysrim2/blob/main/examples/notebooks/Analysis.ipynb)
for detailed example. Notice how there is a python class for each SRIM
output file and gives simple access to each column. This did require
some complex regex to get working just right.

``` python
folders = ["test_files/2", "test_files/4"]
image_directory = "examples/images"

fig, axes = plt.subplots(1, len(folders), sharey=True, sharex=True)

for ax, folder in zip(np.ravel(axes), folders):
    plot_damage_energy(folder, ax)
    plot_ionization(folder, ax)
    ax.legend()
    ax.set_ylabel("eV")
    ax.set_xlabel("Depth [Angstroms]")
fig.suptitle("Ionization Energy vs Depth", fontsize=15)
fig.set_size_inches((20, 6))
fig.savefig(os.path.join(image_directory, "ionizationvsdepth.png"), transparent=True)
```

![srim heatmap](https://raw.githubusercontent.com/FSIBT/pysrim2/main/examples/images/ionizationvsdepth.png)

See [jupyter
notebook](https://github.com/FSIBT/pysrim2/blob/main/examples/notebooks/Analysis.ipynb)
for full demonstration of features.

An example of creating some publication graphics with pysrim2 can also
be found in the
[SiC notebook](https://github.com/FSIBT/pysrim2/blob/main/examples/notebooks/SiC.ipynb).
Chris Ostrouchov used it in a
[publication](https://doi.org/10.1016/j.cossms.2017.09.003).

# Development

```bash
uv sync                          # set up the environment
uv run pytest                    # run the tests (from the repository root)
uv run pre-commit run --all-files   # lint and format
git config blame.ignoreRevsFile .git-blame-ignore-revs
```

The last command makes `git blame` skip the one-off formatting commit.
See [AGENTS.md](https://github.com/FSIBT/pysrim2/blob/main/AGENTS.md) for commit conventions. Bug reports and pull
requests are welcome at <https://github.com/FSIBT/pysrim2>.

# Credits

pysrim was written by [Chris Ostrouchov](https://gitlab.com/costrouc)
with contributions from [Alex Hanson](https://gitlab.com/wahanson) and
[dschwen](https://github.com/dschwen). pysrim2 includes fixes from many
fork authors, listed in
[changes from pysrim](https://pysrim2.readthedocs.io/en/latest/changes-from-pysrim.html).

# License

MIT. See [LICENSE](https://github.com/FSIBT/pysrim2/blob/main/LICENSE). SRIM is licensed separately by its author.
