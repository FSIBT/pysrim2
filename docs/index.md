# pysrim2

## SRIM from Python, on any operating system

SRIM (Ziegler's Stopping and Range of Ions in Matter) simulates how ions slow down and
stop in a material. pysrim2 runs SRIM from Python on Windows, Linux and macOS (macOS
through the container). Every output file is parsed into pandas tables. Parameter scans
run in parallel and are cached, beam distributions are supported, and plots are built in.
pysrim2 continues the original pysrim; see {doc}`changes-from-pysrim`.

{doc}`Get started <first-simulation>` · {doc}`Gallery <gallery>` · {doc}`Guides <dataframes>` · {doc}`Reference <api/index>`

::::{grid} 1 1 2 2
:gutter: 3

:::{grid-item-card} Start in Python

Install pysrim2, then set up SRIM once:

```shell
pip install pysrim2
pysrim2 install
```

The boron reference case (100 nm of gold on silicon, 200 ions):

```python
from srim import TRIM, Ion, Layer, Target
from srim.analysis import implant_summary

au = Layer.from_formula("Au", 19.3, 1000.0)
si = Layer.from_formula("Si", 2.33, 10000)
results = TRIM(
    Target([au, si]),
    Ion("B", energy=20e3),
    number_ions=200,
    angle_ions=7.0,
    ranges=1,
    random_seed=12345,
).run()
depth = implant_summary(results.range3d.df)
print(depth["mean_depth_A"])  # about 282
```

```{button-ref} first-simulation
:ref-type: doc
:color: primary
:outline:

First simulation
```
:::

:::{grid-item-card} Start with an AI agent

Install the pysrim2 skill for your coding agent (for example Claude Code). The skill
needs SRIM too, which `pysrim2 install` sets up.

```shell
pysrim2 install skill
```

Then give the agent a task, for example:

> Simulate a 100 nm gold layer on a silicon wafer, implant 200 boron ions at 7 degrees and 20 keV, and give me a pandas DataFrame of implantation depth and xy straggle.

The agent writes and runs the script and reports a mean depth of about 282 Å.

```{button-ref} agents
:ref-type: doc
:color: primary
:outline:

How the agent route works
```
:::
::::

## Gallery

::::{grid} 1 2 3 5
:gutter: 2

:::{grid-item-card} Where ions stop
:img-top: gallery/images/heatmap.png
:img-alt: Heatmap of where 5 MeV nickel ions stop
:link: gallery-heatmap
:link-type: ref
:::

:::{grid-item-card} Depth profiles
:img-top: gallery/images/depth_profiles.png
:img-alt: Ionization and vacancy profiles against depth
:link: gallery-depth_profiles
:link-type: ref
:::

:::{grid-item-card} A parameter scan
:img-top: gallery/images/scan_plot.png
:img-alt: Mean depth against energy, one line per angle
:link: gallery-scan_plot
:link-type: ref
:::

:::{grid-item-card} Collision cascades
:img-top: gallery/images/tracks.png
:img-alt: Map of the collisions of carbon ions in SiC
:link: gallery-tracks
:link-type: ref
:::

:::{grid-item-card} A beam with energy spread
:img-top: gallery/images/beam.png
:img-alt: Energy distribution of a beam with a 1 keV spread
:link: gallery-beam
:link-type: ref
:::

::::

{doc}`More in the gallery <gallery>`

## Guides and reference

The sidebar lists the guides (run spec, output tables, scans, beam distributions, export,
concurrency, the container and the command line) and the reference (the API and the
changelog).

```{toctree}
:hidden:
:caption: Get started
:maxdepth: 1

first-simulation
installation
agents
container
```

```{toctree}
:hidden:
:caption: Gallery
:maxdepth: 1

gallery
examples
```

```{toctree}
:hidden:
:caption: Guides
:maxdepth: 1

dataframes
run-spec
export
scans
beam
concurrency
```

```{toctree}
:hidden:
:caption: Reference
:maxdepth: 1

cli
api/index
citing
changes-from-pysrim
changelog
```
