# Running from a run.yaml

`run.yaml` describes the whole calculation (ion, target, number of ions, seed, SRIM
settings). The same file reproduces the run later and travels with the results.

```shell
pysrim2 run run.yaml                                  # outputs in ./srim-output-<timestamp>/
pysrim2 run run.yaml --set ion.energy_keV=30 --output out-30keV
pysrim2 run run.yaml --set number_ions=1000 --set target.0.thickness_nm=50
```

`--set dotted.key=value` overrides a value (converted by the schema; list items by index).
The output folder holds SRIM's files and a `run.yaml` with a `provenance:` section
(pysrim2 version, SRIM checksum, platform, backend, times, seed).

From Python:

```python
from srim import TRIM
from srim.analysis import implant_summary

results = TRIM.from_yaml("run.yaml").run()
print(implant_summary(results.range3d.df))
results.to_parquet("out")  # the spec is stored in the file metadata
```

Turn an existing `TRIM.IN` into a spec with `pysrim2 import TRIM.IN -o run.yaml`.
See the [run.yaml page](https://pysrim2.readthedocs.io/en/latest/run-spec.html) and the
[command line page](https://pysrim2.readthedocs.io/en/latest/cli.html).
With the seed fixed, the mean depth is about 282 angstrom, as in the boron example.
