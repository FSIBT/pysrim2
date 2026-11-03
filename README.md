![srim heatmap](https://raw.githubusercontent.com/FSIBT/pysrim2/main/examples/images/length-heatmap-log-cropped.png)

# pysrim2: Automation, Analysis, and Plotting of SRIM Calculations

> **pysrim2 is a maintained continuation of
> [pysrim](https://gitlab.com/costrouc/pysrim) by Chris Ostrouchov.**
> See [what changed](https://pysrim2.readthedocs.io/en/latest/changes-from-pysrim.html).

[![PyPI](https://img.shields.io/pypi/v/pysrim2.svg)](https://pypi.org/project/pysrim2/)
[![CI](https://github.com/FSIBT/pysrim2/actions/workflows/ci.yml/badge.svg)](https://github.com/FSIBT/pysrim2/actions/workflows/ci.yml)
[![Documentation](https://readthedocs.org/projects/pysrim2/badge/?version=latest)](https://pysrim2.readthedocs.io/en/latest/)
[![License](https://img.shields.io/pypi/l/pysrim2.svg)](LICENSE)

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

```bash
uv add pysrim2        # or: pip install pysrim2
```

If you have `pysrim` installed, remove it first:
`pip uninstall pysrim && pip install pysrim2`. Python 3.11 or newer is
required.

## SRIM

SRIM itself is not included. Download it from
[srim.org](http://www.srim.org/). SRIM is copyright (c) James F.
Ziegler and is free for non-commercial use only; check its licence
before using it. You do not need SRIM if you only analyse existing
output files.

- **Windows:** extract the SRIM files into a directory and pass that
  directory to `trim.run()`. `TRIM.exe` should be in it.
- **Linux and macOS:** install [wine](https://www.winehq.org/) and
  extract SRIM into a directory. SRIM is
  [compatible](https://appdb.winehq.org/objectManager.php?sClass=version&iId=13202)
  with wine. If `xvfb-run` is available, `pysrim2` uses it so no
  display is needed. An installer for SRIM under wine is planned.
- **Container:** a container image with SRIM under wine is planned.

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
calcualtion. Note that `/tmp/srim` is the path to the SRIM executable
directory (`SRIM.exe` should reside in this directory). `pysrim2` will
add all the necessary input files. If this ran successfully for you a
SRIM window will popup and start the calculation.

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

# Specify the directory of SRIM.exe
# For windows users the path will include C://...
srim_executable_directory = "/tmp/srim"

# takes about 10 seconds on my laptop
results = trim.run(srim_executable_directory)
# If all went successfull you should have seen a TRIM window popup and run 25 ions!
# results is `srim.output.Results` and contains all output files parsed
```

See [documentation](https://pysrim2.readthedocs.io/en/latest/) for all available options.

## Copy SRIM output files to directory

After a SRIM calculation has completed run `copy_output_files` to take
all of the output files and move them to a directory of your liking.

``` python
from srim import TRIM

TRIM.copy_output_files("/tmp/srim", "/home/user/scratch/srim")
```

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
will processes all output files in a directory and provide a
dictionary of each parsed output file. `pysrim2` comes with some
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
See [AGENTS.md](AGENTS.md) for commit conventions. Bug reports and pull
requests are welcome at <https://github.com/FSIBT/pysrim2>.

# Credits

pysrim was written by [Chris Ostrouchov](https://gitlab.com/costrouc)
with contributions from [Alex Hanson](https://gitlab.com/wahanson) and
[dschwen](https://github.com/dschwen). pysrim2 includes fixes from many
fork authors, listed in
[changes from pysrim](https://pysrim2.readthedocs.io/en/latest/changes-from-pysrim.html).

# License

MIT. See [LICENSE](LICENSE). SRIM is licensed separately by its author.
