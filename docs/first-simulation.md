# First simulation

This page runs one SRIM calculation from Python: the boron reference case, where 200
boron ions go into 100 nm of gold on silicon. The reference run is short: 200 ions with
a quick Kinchin-Pease calculation.

## Install

Install the library (`import srim`) in your project:

```shell
uv add pysrim2        # in a uv project
pip install pysrim2   # or with pip, in a virtual environment
```

Install the command line once, and let it set up SRIM:

```shell
uv tool install pysrim2
pysrim2 install       # SRIM, and on Linux or macOS wine or the container
pysrim2 doctor        # every line should say [ok]
```

`pysrim2 install` picks what your platform needs. On macOS, SRIM runs in the
container: it uses Docker or Podman if one is running, and otherwise installs Podman (see
{doc}`container`). The full list of steps and
the troubleshooting are in {doc}`installation`.

SRIM itself is free for non-commercial use only (see {doc}`installation`).

## Concepts

A calculation is built from three objects:

- an **{class}`~srim.core.Ion`** has an element and an energy in eV, for example
  `Ion("B", energy=20e3)` (20 keV);
- a **{class}`~srim.core.Layer`** is a set of elements with a density in g/cm³ and a width
  in Å (1 nm is 10 Å). Each element has a stoichiometry (`stoich`) and, optionally, a
  displacement energy (`E_d`, eV);
- a **{class}`~srim.core.Target`** is a list of layers, from the surface down.

A layer can be written with its elements spelled out:

```python
from srim import Layer

nickel = Layer(
    {"Ni": {"stoich": 1.0, "E_d": 30.0, "lattice": 0.0, "surface": 3.0}},
    density=8.9,  # g/cm^3
    width=20000.0,  # Å, 2 µm
)
```

or, when the default binding energies are fine, from a chemical formula:
`Layer.from_formula("Au", density=19.3, width=1000.0)`.

## The boron reference case

Build the target: 100 nm of gold (1000 Å) on a silicon substrate.

```python
from srim import Layer, Target

gold = Layer.from_formula("Au", density=19.3, width=1000.0)  # 100 nm
silicon = Layer.from_formula("Si", density=2.33, width=10000.0)  # substrate
target = Target([gold, silicon])
```

Build the ion: boron at 20 keV.

```python
from srim import Ion

ion = Ion("B", energy=20e3)  # eV
```

Set up the calculation with `TRIM` and run it. `ranges=1` writes the final position of
every ion (`RANGE_3D.txt`); `random_seed` makes the run repeatable.

```python
from srim import TRIM

trim = TRIM(
    target,
    ion,
    number_ions=200,
    angle_ions=7.0,  # degrees from the surface normal
    ranges=1,
    random_seed=12345,
)
results = trim.run()
```

`run()` works in a private copy of SRIM, so runs can happen side by side, and it returns
a {class}`srim.output.Results` object.

## Reading the results

`results` has one attribute per SRIM output. Each one has a `.df`, a
{class}`pandas.DataFrame` with snake_case columns and the unit in the name (see
{doc}`dataframes` for every column).

```python
results.ioniz.df.head()  # energy lost to electrons, by depth
results.vacancy.df  # vacancies by depth, one column per element
results.range.summary  # the statistics SRIM prints in RANGE.txt
```

`results.range.summary` is a dictionary; its `mean_depth_A` is the projected range.

The per-ion final positions are in `results.range3d.df`. From them,
{func}`srim.analysis.implant_summary` computes the range and straggling:

```python
from srim.analysis import implant_summary

depth = implant_summary(results.range3d.df)
print(depth["mean_depth_A"])  # about 282 Å
print(depth["lateral_straggle_A"])  # about 242 Å
```

The mean depth is about 282 Å (28 nm) and the lateral straggling about 242 Å. These
values come from SRIM with seed 12345; they are the reference for the agent example in
{doc}`agents`.

## Plot a depth profile

The `.df` tables are plain pandas tables, so any plotting library works. This one draws
the vacancies against depth, summed over the elements:

```python
import matplotlib.pyplot as plt

vacancy = results.vacancy.df
element_columns = [c for c in vacancy.columns if c.startswith("vacancies_")]

fig, ax = plt.subplots()
ax.plot(vacancy["depth_A"] / 10, vacancy[element_columns].sum(axis=1))
ax.set_xlabel("depth (nm)")
ax.set_ylabel("vacancies (1/Å/ion)")
fig.savefig("vacancies.png")
```

A picture of a depth profile from a full cascade is in the
{ref}`gallery <gallery-depth_profiles>`.

## Save the results

`run()` keeps its outputs in a run directory. Instead of `results = trim.run()` above, pass
`output_dir` to copy the outputs to a folder of your choice:

```python
results = trim.run(output_dir="boron-output")
```

Later, read the folder again with `Results`:

```python
from srim.output import Results

results = Results("boron-output")
```

Export every table to Parquet (needs `pip install pysrim2[parquet]`), one file per table,
with the run's settings in the file metadata:

```python
results.to_parquet("boron-parquet")
```

Other formats (HDF5, CSV) are in {doc}`export`.

## The same run as a file

A calculation can be written as a `run.yaml`, which describes the whole run and can be
run again later, from the command line or from Python:

```python
trim.to_yaml("run.yaml")
```

```shell
pysrim2 run run.yaml                          # outputs go to ./srim-output-<time>
pysrim2 run run.yaml --set ion.energy_keV=30  # change a value before the run
```

`TRIM.from_yaml("run.yaml")` reads it back in Python. The format is described in
{doc}`run-spec`.

## Next

- {doc}`scans`: vary energy, angle or thickness over a grid, in parallel and cached.
- {doc}`beam`: ions with an energy spread or an angular distribution.
- {doc}`dataframes`: the columns of every output table.
- {doc}`gallery`: pictures made with pysrim2, with the scripts that make them.
