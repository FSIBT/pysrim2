# Beam distributions

A beam is rarely one energy and one angle. Real implanters give an energy spread,
a divergence, a spot size. pysrim2 lets each beam parameter be a *distribution*: the
ion energy, the angle of incidence, the azimuth around the beam axis, and the start
position. A run with distributions samples the beam and then runs SRIM on it.

A run spec without distributions is unchanged, so all earlier scripts and `run.yaml`
files keep working (see {doc}`run-spec`).

## Two modes

Set the mode with `beam: {mode: ...}` in the run spec. `exact` is the default.

| | `exact` (default) | `binned` |
|---|---|---|
| What SRIM runs | every sampled ion, from a `TRIM.DAT` file | one run per bin of the energy and angle |
| Faithful to the sampled beam | yes, each ion has its own energy and direction | approximate: a bin's ions share the bin's mean energy and angle |
| Parameters | energy, angle, azimuth, position | energy, angle, and azimuth only if uniform over the full turn |
| SRIM calculation | 4 (for `calculation: 1`) or 5 (for `calculation: 2`) | the spec's own `calculation` |
| Ions per SRIM run | at most 99 999 | no limit: the ions are split over the bins |
| Parallel | a run of up to 99 999 ions is one SRIM process; bigger beams are split into units | every bin is a unit, run on the scan workers |
| Cache | in a scan, every unit (a bin, or a unit of 99 999 ions) is cached | in a scan, every bin is cached, so a repeated scan reruns nothing |

Use `exact` when the start position or the azimuth matters, when you want the
sampled ions themselves (for example to check the sampling), or when you have up to a
few hundred thousand ions. Use `binned` for large ion counts when only energy and
tilt are spread: each bin is one SRIM run, and the bins run in parallel.

Binned mode is an approximation. Within a bin every ion has the bin's mean energy and
angle, so more bins give a closer answer and more SRIM runs. Exact mode has no such
error: SRIM runs every sampled ion with its own energy, direction and start position.

## Quick start

A distributed run in Python. Build the run spec with `TRIM(...)`, then replace the
energy with a distribution. `Gaussian(20.0, 1.0)` is 20 keV with a standard deviation
of 1 keV.

```python
from srim import TRIM, Ion, Layer, Target
from srim.analysis import implant_summary
from srim.beam import Gaussian, run_beam, sampled_ions
from srim.spec import RunSpec

target = Target([Layer.from_formula("Si", density=2.33, width=10000.0)])  # 1000 nm
data = (
    TRIM(target, Ion("B", energy=20e3), number_ions=200, calculation=1, ranges=1, random_seed=12345)
    .to_spec()
    .to_dict()
)
data["ion"]["energy_keV"] = Gaussian(20.0, 1.0).to_dict()
spec = RunSpec.from_dict(data)

if __name__ == "__main__":  # required for binned beams (worker processes import this file)
    with run_beam(spec, output_dir="beam-exact") as results:
        print(implant_summary(results.range3d.df)["mean_depth_A"])
        print(sampled_ions(results)[["ion", "energy_keV", "depth_nm"]].head())
```

The same beam in YAML. `beam:` is optional here: exact is the default.

```yaml
version: 1
spec:
  ion:
    symbol: B
    energy_keV: {gaussian: {mean: 20.0, sigma: 1.0}}
    angle_deg: 0.0
  number_ions: 200
  calculation: 1
  seed: 12345
  settings: {description: pysrim run, ranges: 1}
  target:
    - name: Si
      thickness_nm: 1000.0
      density_g_cm3: 2.33
      phase: 0
      elements:
        - {symbol: Si, stoich: 1.0, displacement_eV: 25.0, lattice_eV: 0.0, surface_eV: 3.0}
```

```shell
pysrim2 run beam.yaml --set beam.mode=binned --set beam.bins.energy_keV=5
```

`pysrim2 run` accepts a distributed run file and prints where its results are. The
`--set` keys are the same as for scans (see below).

## Shapes

Every distribution is a one-key mapping in YAML, such as `{gaussian: {mean: 20.0,
sigma: 1.0}}`. In Python, the classes in `srim.beam` give the same mapping with
`.to_dict()`. A number is a fixed value.

| Shape | YAML | Python | Density |
|---|---|---|---|
| fixed | `{fixed: {value: 20.0}}` or just `20.0` | `Fixed(20.0)` | one value (no spread) |
| Gaussian | `{gaussian: {mean: 20.0, sigma: 1.0}}` | `Gaussian(20.0, 1.0)` | normal |
| uniform | `{uniform: {low: 15.0, high: 25.0}}` | `Uniform(15.0, 25.0)` | flat between `low` and `high` |
| triangular | `{triangular: {low: 15.0, mode: 20.0, high: 25.0}}` | `Triangular(15.0, 20.0, 25.0)` | peak at `mode` |
| log-normal | `{lognormal: {mu: 3.0, sigma: 0.1}}` | `LogNormal(3.0, 0.1)` | `ln x` is normal with `mu` and `sigma` |
| histogram | see [histogram files](#histograms) | `Histogram.from_file(...)`, `Histogram(edges, counts)`, `Histogram.from_centres(...)` | piecewise constant |
| Python function | see [Python functions](#python-functions) | `Function("python:mod:name", range=(a, b))` | your density |

Any shape can take `range: [low, high]` to cut its tails (truncation). Without it,
a Gaussian or a log-normal runs from mean ± 5 sigma (for a log-normal, `exp(mu ± 5 sigma)`).
The density is renormalised after the cut.

A zero-width shape (`sigma: 0`, or a uniform with `low == high`) is a fixed value.
`beam.bins` for a fixed parameter is an error, because there is nothing to bin.

Each continuous shape is tabulated on 4097 points of its support, and the sampled
distribution is the piecewise-linear cumulative distribution through those points.
Histograms are exact (their bin edges are the nodes).

Python:

```python
from srim.beam import Fixed, Gaussian, LogNormal, Triangular, Uniform

energy = Gaussian(20.0, 1.0, range=(17.0, 23.0))  # truncated at 17 and 23 keV
angle = Uniform(0.0, 5.0).to_dict()  # the mapping, for a run spec dict
```

YAML, with a truncation:

```yaml
ion:
  energy_keV: {gaussian: {mean: 20.0, sigma: 1.0, range: [17.0, 23.0]}}
  angle_deg: {uniform: {low: 0.0, high: 5.0}}
```

## Which parameters can be distributions

| Key | Exact | Binned | Notes |
|---|---|---|---|
| `ion.energy_keV` | yes | yes | cut to energies above 0 |
| `ion.angle_deg` | yes | yes | cut to `[0, 90)` |
| `ion.azimuth_deg` | yes | uniform over `[0, 360)` only | cut to `[0, 360)`; a number in `[0, 360)` is allowed |
| `ion.position.y_nm`, `z_nm`, `depth_nm` | yes | no | `depth_nm` must lie inside the target |

`ion.azimuth_deg` is the direction around the beam axis. In exact mode a distributed
beam with no azimuth gets a uniform azimuth over the full turn, so the tilt is the
same for every ion but the ions spread around the axis. Give `azimuth_deg: 0.0` to
put every ion in one plane. In binned mode the azimuth is not binned, so a uniform
azimuth over the full turn is accepted and a narrower one is refused (it needs exact
mode).

`ion.position` gives the start point in the target's frame. `y_nm` and `z_nm` are the
lateral coordinates (default 0), and `depth_nm` is the depth (default 0). Position
distributions are exact-only:

```yaml
ion:
  energy_keV: 20.0
  position:
    y_nm: {gaussian: {mean: 0.0, sigma: 50.0}}
    z_nm: {gaussian: {mean: 0.0, sigma: 50.0}}
    depth_nm: 0.0
```

Each beam parameter is drawn on its own, so the energy, the angle and the position are
independent. Correlated distributions (for example an energy that depends on the angle)
are not possible. A beam has one ion species.

## Truncation and physical limits

pysrim2 refuses a beam that differs from the one you defined, rather than running a
clipped one silently. Errors name the dotted key, for example
`ion.energy_keV: no probability in [...]`. These checks apply:

- energy: only the part above 0 is sampled (`ion.energy_keV`, must have probability above 0)
- angle: only `[0, 90)` is sampled
- azimuth: only `[0, 360)` is sampled
- depth: the start depth must be inside the target, `[0, total thickness)`

A `range` that leaves no probability is an error, and so is a `range` with `low > high`.

## Histograms

A histogram is a piecewise-constant density. The counts are weights: they need not sum
to one. A histogram takes exactly one of three forms: a `file`; `edges` with `counts`; or
`centres` with `counts`. Examples, one per form:

```yaml
ion:
  energy_keV: {histogram: {file: energy_spectrum.txt}}  # from a text file
```

```yaml
ion:
  angle_deg: {histogram: {edges: [0.0, 2.0, 4.0], counts: [1.0, 3.0]}}  # edges and counts
```

```yaml
ion:
  angle_deg: {histogram: {centres: [1.0, 3.0], counts: [1.0, 3.0]}}  # centres and counts
```

Python:

```python
from srim.beam import Histogram

spectrum = Histogram.from_file("energy_spectrum.txt")
binned_by_centre = Histogram.from_centres([10, 20, 40], [1, 3, 1])
explicit = Histogram([0.0, 2.0, 4.0], [1.0, 3.0])
```

A relative `file` in a YAML file is taken from the folder of the YAML file. In Python it
is taken from the working directory. The spec stores the absolute path, and the cache
key and point id use the file's *content*, so editing the file gives a new point.

### Histogram files

A text file has one bin per line, in one of two layouts:

- two columns, `centre count`: the bin edges are the midpoints between centres, and the
  outer edges are half a bin beyond the first and last centre. Centres must increase.
- three columns, `low high count`: one bin per line, in order. A gap between two bins
  becomes a bin with count 0. Bins must not overlap.

Rules that apply to both:

- columns are separated by whitespace, tabs, `;` or `,`
- when the separator is `;` or a tab, a comma is a decimal mark (`1,5;10` is 1.5 and 10);
  when the separator is `,`, commas separate fields
- `#` starts a comment, blank lines are skipped, and CRLF line ends are accepted
- a first row with no digits is a header and is skipped
- trailing empty fields (a final separator) are ignored
- counts must not be negative and must have a positive sum

This file gives centres 10, 20 and 40 keV with counts 1, 3 and 1:

```text
# energy spectrum, keV counts
energy_keV;counts
10,0;1
20,0;3
40,0;1
```

An error names the file and the line, for example
`energy_spectrum.txt: line 3: centres must be strictly increasing`.

## Python functions

For a density that is not one of the shapes, give a module-level function. It must
accept a numpy array and return an array of the same shape (or a scalar, which is then
broadcast), with values that are finite and not negative. `range` is required and is the
support of the distribution. The density is tabulated on the same grid as the other
shapes.

A function is referred to as `python:<module>:<name>`. The module must be importable by
its name from the worker processes: put it next to your script (or on `PYTHONPATH`) and
run from that folder. Functions in `__main__`, lambdas and functions made inside another
function are refused, because a worker cannot import them.

`my_beams.py`:

```python
import numpy as np


def ramp(energy_keV):
    """A density that rises linearly from 5 to 40 keV (vectorised)."""
    return np.asarray(energy_keV, dtype=float)
```

Use it in YAML:

```yaml
ion:
  energy_keV: {function: {ref: "python:my_beams:ramp", range: [5.0, 40.0]}}
```

or in Python, where the function object is accepted too (it must be importable):

```python
from srim.beam import Function
from my_beams import ramp

energy = Function("python:my_beams:ramp", range=(5.0, 40.0))
same = Function(ramp, range=(5.0, 40.0))  # stored as the same python: reference
```

The source text of the function is part of the cache key. Changing the function (or the
module it is in) gives a new point, so an old run is never reused for a changed density.

## Seeds and reproducibility

A spec's `seed` decides the beam. Each sampled parameter has its own random stream,
spawned from the seed in a fixed order: energy, angle, azimuth, `y`, `z`, depth. The same
spec and seed give the same sampled beam and the same results on the same platform.
Because the streams are separate, adding a position to a spec does not change the
sampled energies.

Exact mode samples `number_ions` ions. A scan with several points gives each point a seed
derived from the scan's seed and the point id (see {doc}`scans`), so a point keeps its
seed when other points are added.

## Running a beam

`run_beam(spec)` runs a distributed spec and returns its results. It chooses the route:

- an exact beam of up to 99 999 ions is one SRIM run, the same as `TRIM.from_spec(spec).run()`;
- anything else is a one-point scan: a binned beam runs one SRIM run per bin, an exact beam
  of more ions is split into units of 99 999 ions, and `precision=` (see
  precision targets in {doc}`scans`) runs batches until a precision goal is met.

```python
from srim.beam import run_beam

with run_beam(spec) as results:  # a temporary folder, deleted by close()
    ...

with run_beam(spec, output_dir="beam-run") as results:  # keeps the outputs
    ...
```

`output_dir` is the folder of a single run, or the scan folder of a scan (its point folder
is `beam-run/points/<id[:16]>`). Use `results` as a context manager, or call `close()`, to
delete the temporary folder.

### What you get back

Exact single run: a `srim.output.Results` (with `TRIM.DAT` and `run.yaml` in its
directory, next to the usual outputs). Its per-ion tables (`range3d`, ...) are the ions
SRIM ran.

Batched runs (an exact beam above 99 999 ions, a binned beam, or a precision target): a
`srim.scan.MergedResults`, with the surface of `Results`:

- per-ion tables are concatenated. `ion` is the global ion number, and a `batch` column
  gives the unit (batch) each ion came from.
- a binned beam adds `beam_batch`, `bin`, `bin_energy_keV` and `bin_angle_deg` after `batch`.
- `results.beam_bins` is the table of the bins of a binned run (one row per bin, with the
  columns listed in {doc}`dataframes`). It is None for an exact merged result. A single run's
  `Results` has no such attribute.

A finished point folder can be opened again, for example later or in another script. Pass
the point's folder (`points/<id[:16]>`, under the `output_dir` of the run):

```python
from pathlib import Path

from srim.beam import open_results

point = next(Path("beam-run/points").iterdir())  # the point folder of a finished binned run
with open_results(point) as results:
    print(results.beam_bins)
```

### The sampled ions

`sampled_ions(results)` reads the ions an exact run ran back from its `TRIM.DAT` file(s):
one row per ion, with the energy, the depth, the lateral position and the direction
cosines. For a merged result the units are concatenated, and `batch` gives the unit.
Binned results raise an error, because a binned run has no sampled ions (it has bins).

```python
with run_beam(spec, output_dir="beam-exact") as results:
    ions = sampled_ions(results)
    print(ions[["ion", "energy_keV", "angle_deg", "azimuth_deg", "depth_nm"]].head())
```

The values come from the file, which stores 7 significant digits, so they can differ from
the sampled values in the last digit.

## Binned mode

In a binned run each distributed energy and angle is cut into bins, and every bin is one
SRIM run with a fixed energy and angle.

- `beam.bins.energy_keV` and `beam.bins.angle_deg` give the number of bins. An integer
  gives equal-probability bins; a list gives the bin edges. The defaults are 10 energy bins
  and 6 angle bins.
- A bin's energy and angle are the mean of the bin's interval. With edges, probability
  outside the edges is dropped and the rest renormalised (a warning above 0.1 %).
- The beam has one run per bin of the product (energy-major). Bins with no probability are
  dropped.
- The ions are split over the bins by Webster (Sainte-Laguë) apportionment, so each bin gets
  its share of `number_ions` and the total is exact. A bin that gets no ion has no run.
- `beam.max_bins` (default 2000) caps the number of bins. More bins is an error that names
  `beam.max_bins`. More than 200 bins warns, because each bin is a SRIM run.
- The calculation is the one in the spec (1 or 2, or any other), not 4 or 5.

```yaml
ion:
  energy_keV: {gaussian: {mean: 20.0, sigma: 1.0}}
  angle_deg: {uniform: {low: 0.0, high: 5.0}}
beam:
  mode: binned
  bins:
    energy_keV: 8
    angle_deg: [0.0, 2.5, 5.0]
  max_bins: 500
```

Python (start from the spec of the quick start; run it inside the `__main__` guard, as there):

```python
from srim.beam import Uniform

data["ion"]["energy_keV"] = Gaussian(20.0, 1.0).to_dict()
data["ion"]["angle_deg"] = Uniform(0.0, 5.0).to_dict()
data["beam"] = {"mode": "binned", "bins": {"energy_keV": 8, "angle_deg": [0.0, 2.5, 5.0]}}
spec = RunSpec.from_dict(data)

if __name__ == "__main__":
    with run_beam(spec, output_dir="beam-binned") as results:
        print(results.beam_bins)
```

## Exact mode details

Exact mode writes one line per sampled ion into `TRIM.DAT` (SRIM's file of ions with
their energy, start position and direction) and runs SRIM on that file:

- `calculation: 1` (quick Kinchin-Pease) runs SRIM calculation 4, and `calculation: 2`
  (full cascades) runs SRIM calculation 5. Calculation 3 (sputtering) and the other
  values are refused with a distributed beam in exact mode (`SpecError`), so use
  `beam.mode: binned` for those.
- The ion energy in `TRIM.IN` is the largest sampled energy (SRIM needs one value there;
  each ion's own energy comes from `TRIM.DAT`). The angle in `TRIM.IN` is ignored.
- More than 99 999 ions do not fit one `TRIM.DAT` file. `run_beam` splits the beam into
  units of 99 999 ions and merges them. `TRIM.from_spec(spec).run()` refuses such a spec, so
  use `run_beam`.
- `settings.description` must be ASCII: it goes into `TRIM.DAT`, which SRIM reads as
  ASCII. A spec with another character is refused when it is read.
- An in-place run (`srim_directory=`) only deletes the `TRIM.DAT` and the `beam/` copies
  that pysrim2 wrote. An exact run there is refused if a `TRIM.DAT` that pysrim2 did not
  write is in the way (on Windows and macOS that includes SRIM's own example `TRIM.dat`);
  run in an isolated copy (the default) instead.

## Plotting

`plot_beam(results, parameter)` draws a parameter's defined density with the sampled beam.
It needs matplotlib (`pip install "pysrim2[plot]"`).

```python
from srim.beam import plot_beam

with run_beam(spec, output_dir="beam-plot") as results:  # the spec of the binned example above
    ax = plot_beam(results, "energy_keV")  # one bar per bin
    ax.figure.savefig("beam-energy.png")
```

For an exact run the sampled ions are drawn as a density histogram, and the defined
density (the clipped distribution) as a line. For a binned run each bin is a bar, with the
probability per unit of the parameter, and the line is the defined density. A binned run
has bins only for energy and angle, so other parameters raise an error. A parameter that is
fixed in the run has nothing to plot and raises an error too.

## Scans of beams

A scan can vary the shape parameters of a distribution, and the bin counts. Keys are
dotted, as for `--set`. The base is a run spec with a distribution (here the binned spec
above; `Scan` only defines the points, `run_scan` runs them):

```python
from srim.scan import Scan

scan = Scan(
    spec,
    grid={
        "ion.energy_keV.gaussian.sigma": [0.5, 1.0, 2.0],
        "beam.bins.energy_keV": [5, 10],
    },
    extract=["implant_summary"],
    ions=1000,
)
```

A binned point of a scan is a batched point: its units are the bins, and its folder
records them (see {doc}`scans`, section "Beam distributions in scans"). Exact points
above 99 999 ions are split into units the same way.

## Limits

- One ion species per beam.
- Parameters are independent (no correlated distributions).
- Positions and non-uniform azimuths are exact-only.
- Exact runs: at most 99 999 ions per SRIM run (larger beams are split), and an ASCII
  `settings.description`.
- Binned runs: at most `beam.max_bins` bins (2000 by default), and the bins are
  approximations of the continuous distribution.
- A histogram file is read when the spec is read, so the file must exist then.
