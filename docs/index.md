# Welcome to pysrim2's documentation!

```{admonition} pysrim2 is a continuation of pysrim
:class: note

pysrim2 is a maintained continuation of
[pysrim](https://gitlab.com/costrouc/pysrim) by Chris Ostrouchov.
See {doc}`changes-from-pysrim`.
```

![Length heatmap](../examples/images/length-heatmap-log-cropped.png)

`pysrim2` is a Python package that wraps and extends SRIM, a popular
tool for simulating ions traveling through a material. It addresses
several pain points of SRIM: compatibility with all operating systems,
automation and crash recovery of SRIM calculations, parsing of all
output files, and publication quality plots.

The import name is `srim`. See {doc}`installation` for getting
`pysrim2` and SRIM installed. Next follow the simple {doc}`tutorial`
to get started. For a more advanced example see the [SiC ion damage
production jupyter notebook](https://github.com/FSIBT/pysrim2/blob/main/examples/notebooks/SiC.ipynb).
Runnable example scripts are in [examples/scripts](https://github.com/FSIBT/pysrim2/tree/main/examples/scripts).
Use {ref}`modindex` to search the API documentation.

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

## Indices and tables

* {ref}`genindex`
* {ref}`modindex`
* {ref}`search`
