# Scans

A scan runs one SRIM calculation for many values of its parameters: the ion energy,
the angle, a layer thickness. Each value is a *point*. The points run on worker
processes, each point's outputs go to its own folder, and the result is one table
with a row per point.

Use a scan when you would otherwise write a loop over runs. Use a single
`TRIM(...).run()` (see {doc}`run-spec`) when you need the full output of one run
for a plot or a report.

## Concepts

- **Base run.** A scan starts from one run spec (a `RunSpec`, the same thing a
  `run.yaml` holds). Every point is the base run with some values changed.
- **Parameters.** Dotted keys, as in `pysrim2 run --set`: `ion.energy_keV`,
  `ion.angle_deg`, `target.0.thickness_nm`. The form `target[0].thickness_nm` is
  accepted too and becomes `target.0.thickness_nm`, which is the name of the column in
  the result. The keys `number_ions` and `seed` are not parameters: use `ions` and
  `seed` of the scan.
- **Points.** `grid` gives lists of values; the points are all combinations (the last
  key changes fastest). `points` gives rows that are used together; each row is
  combined with each grid combination. A key may be in `grid` or in `points`, not both.
  Two points that give the same run spec are an error.
- **Point id.** `id` is the SHA-256 of the point's run spec, without the seed and the
  ion count. The point's folder is `points/<first 16 characters of id>`. Changing the
  number of ions keeps the id.
- **Ions.** `ions` is the number of ions per point. Instead, `precision` runs each
  point in batches until a value is known well enough (see [Precision targets](#precision-targets)).
- **Seeds.** The base seed (`seed`) is recorded in `scan.yaml`. Each point gets a seed
  derived from the base seed and its id, so a point keeps its seed when other points
  are added or reordered. Without `seed`, each `run_scan` draws a base seed and records
  it. The same scan with the same seed gives the same numbers on the same platform.
- **Extractors.** An extractor turns the outputs of one run into columns of the result
  (for example `mean_depth_A`). See [Extractors](#extractors).

## A first scan

The same scan in Python and in YAML. It is also in the repository:
`examples/scripts/angle_energy_scan.py` and `examples/scripts/scan.yaml`.

Python:

```python
from srim import TRIM, Ion, Layer, Target
from srim.scan import Scan, run_scan
from srim.ui import PlainProgress


def main():
    target = Target([Layer.from_formula("Si", density=2.33, width=10000.0)])  # 1000 nm
    base = TRIM(
        target,
        Ion("B", energy=20e3),  # eV
        number_ions=50,
        calculation=1,  # quick Kinchin-Pease
        ranges=1,  # RANGE_3D.txt, needed by implant_summary
        random_seed=12345,
    ).to_spec()
    scan = Scan(
        base,
        grid={"ion.energy_keV": [20.0, 40.0], "ion.angle_deg": [0.0, 30.0]},
        extract=["implant_summary"],
        ions=50,
        seed=12345,
    )
    result = run_scan(scan, workers=2, scan_dir="scan-si", on_event=PlainProgress())
    print(result.table)


if __name__ == "__main__":  # required: worker processes import this file (macOS, Windows)
    main()
```

YAML (`scan.yaml`):

```yaml
version: 1
spec:
  ion: {symbol: B, energy_keV: 20.0, angle_deg: 0.0}
  number_ions: 50
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
scan:
  grid:
    ion.energy_keV: [20.0, 40.0]
    ion.angle_deg: [0.0, 30.0]
  extract: [implant_summary]
  ions: 50
  seed: 12345
```

```shell
pysrim2 scan run scan.yaml --ui plain
```

The `scan:` section sits next to `spec:` in a `run.yaml` file. A plain run ignores it,
and `RunSpec.from_yaml` reads the same file. Python and YAML give the same point ids and
seeds (a test keeps the example's two files equal).

The scan folder is `./scan-<UTC time>` for the command line (`--output DIR` chooses
it) and `scan-si` in the Python example. The folder holds:

```text
scan.yaml                    the scan, with the base seed that was used
points/<id[:16]>/            one point's SRIM outputs and run.yaml
points/<id[:16]>/error.log   the errors of a point that failed (one entry per attempt)
points/<id[:16]>/batches/<b>/  the batches of a point with a precision target
result/scan.csv              the result table (one row per point)
result/metadata.yaml         the scan and the base spec, with the pysrim2 version
cache/                       finished runs, reused by later scans (see Cache and reruns)
```

## Extractors

An extractor is a function `f(results, point) -> dict`. `results` is the `Results` of
one run (or the merged results of a precision point's batches), `point` the scan point.
Each key of its dict becomes a column. Name the column with its unit, as the predefined
ones do.

Predefined extractors. Give the name as a string, or with parameters as a one-key
mapping, for example `{vacancy_at: {depth_nm: 50.0}}`.

| Name | Columns | Unit | Needs in the run |
|---|---|---|---|
| `implant_summary` (default) | `n_ions_stopped` (count of stopped ions) | count | `ranges: 1` |
| | `mean_depth_A` (projected range) | Angstrom | |
| | `straggle_depth_A` (projected straggling, sample SD) | Angstrom | |
| | `lateral_range_A` (mean of (\|y\|+\|z\|)/2) | Angstrom | |
| | `lateral_straggle_A` (SD of the signed y and z values) | Angstrom | |
| | `radial_range_A` (mean of r = sqrt(y²+z²)) | Angstrom | |
| | `radial_straggle_A` (SD of r) | Angstrom | |
| | `skewness`, `kurtosis` (of depth; kurtosis is 3 for a Gaussian) | dimensionless | |
| `peak_vacancy_depth` | `peak_vacancy_depth_A`: centre of the bin with most vacancies (element columns) | Angstrom | nothing extra |
| `sputter_yield` | `sputter_yield_atoms_per_ion` (sputtered atoms per incident ion) | atoms/ion | `sputtered: 1`, `calculation: 2` |
| `backscattered_fraction` | `backscattered_fraction` (atoms leaving backwards per incident ion) | per ion | `backscattered: 1` |
| `transmitted_fraction` | `transmitted_fraction` (atoms leaving the back per incident ion) | per ion | `transmit: 1` |
| `energy_to_recoils` | `energy_to_recoils_eV_per_ion` (energy given to recoils, summed over depth) | eV/ion | nothing extra |
| `vacancy_at(depth_nm)` | `vacancies_per_A_per_ion_at_<depth>nm`: vacancies of the target elements at that depth | 1/(Angstrom ion) | nothing extra |
| | `knock_ons_per_A_per_ion_at_<depth>nm`: primary knock-on atoms at that depth | 1/(Angstrom ion) | |
| `ions_in_layer(layer_index)` | `fraction_of_stopped_ions_in_layer_<i>` (0 = the surface layer) | fraction | `ranges: 1` |

Notes on the table:

- `vacancy_at` writes `..._at_50nm` for 50 nm (the depth is formatted with `:g`). The
  value is linearly interpolated between the bin centres of `VACANCY.txt`, and is 0 beyond
  its last bin.
- `backscattered_fraction` and `transmitted_fraction` count atoms, one row per atom. A
  full cascade can give more than one per ion, so the value can be above 1.
- `ions_in_layer` takes the layer boundaries from the run spec (the thicknesses of
  `target`). It divides by the number of stopped ions, not by the ions run. Without a run
  spec, pass `boundaries_nm=[0, t1, t1 + t2, ...]` (in nm).
- An extractor that lacks its output (no `RANGE_3D.txt`, say) fails the point with a
  message that names the setting to change.

Extractors in YAML: a name, a one-key mapping, or a Python reference:

```yaml
scan:
  extract:
    - implant_summary
    - sputter_yield
    - {vacancy_at: {depth_nm: 50.0}}
    - {ions_in_layer: {layer_index: 1}}
    - python:my_extractors:deep_fraction
```

### Writing your own extractor

An extractor takes `(results, point)` and returns a dict. Use `results.range3d.df`,
`results.vacancy.df` and the other tables (see {doc}`dataframes`):

```python
# my_extractors.py
def deep_fraction(results, point=None):
    """Fraction of the stopped ions that are deeper than 50 nm."""
    depth_a = results.range3d.df["depth_A"]  # Angstrom
    return {"deep_fraction_50nm": float((depth_a > 500.0).mean())}
```

Use it from Python by passing the function:

```python
from my_extractors import deep_fraction

scan = Scan(base, grid={...}, extract=["implant_summary", deep_fraction])
```

A function given as a callable is written to `scan.yaml` as `python:<module>:<name>`
(`my_extractors:deep_fraction` here). Only module-level functions can be written this
way; a lambda or a function made inside another function raises an error when the
file is read.

To use a name in `scan.yaml` (or from the command line), write the reference yourself:
`extract: [python:my_extractors:deep_fraction]`. A registered name
(`srim.scan.extract.register("name", fn)`) works only in the Python process that
registers it. Other processes, including `pysrim2 scan run`, do not see it, so they
cannot read the file.

The module must be importable. The command `pysrim2` does not add the current folder to
the import path, so put the module on `PYTHONPATH` (or install it). `python -m srim scan
run` does add it. Worker processes inherit `PYTHONPATH`.

Trust: reading a `python:` reference imports the module, which runs its top-level code.
Load only scan files you trust.

A column name must not repeat a name of the scan (a parameter, `id`, `status`, `error`,
`path`, `n_ions`, `seeds`) or another extractor's column. Either case fails the point.

## Precision targets

With a fixed number of ions, the error of a value is not known. A precision target runs
each point until the error is small enough:

```python
from srim.scan import Precision

scan = Scan(
    base,
    grid={"ion.energy_keV": [20.0, 40.0]},
    extract=["sputter_yield"],
    ions=Precision(target="sputter_yield", rel=0.05, min_ions=1000, max_ions=20000, batch_ions=1000),
    seed=12345,
)
```

```yaml
scan:
  grid:
    ion.energy_keV: [20.0, 40.0]
  extract: [sputter_yield]
  precision: {target: sputter_yield, rel: 0.05, min_ions: 1000, max_ions: 20000, batch_ions: 1000}
  seed: 12345
```

The keys:

| Key | Meaning |
|---|---|
| `target` | the name of an extracted value (a column such as `sputter_yield_atoms_per_ion`) |
| `rel` or `abs` | the goal: relative error (of the value) or absolute error. Give exactly one. |
| `min_ions` | ions a point runs at least (default 1000) |
| `max_ions` | ions a point may run at most (default 100000) |
| `batch_ions` | ions per batch (default 1000) |

`sputter_yield` needs `sputtered: 1` and `calculation: 2` in the base run, as the table
above says. Full cascades are slow, so start with a small `max_ions`.

How a point runs:

1. The point is split into batches of `batch_ions` ions, each with its own seed derived
   from the point's seed. It starts with enough batches for `min_ions` (at least two, so
   that an error exists), never more than `max_ions` allows.
2. The extractors run on all the batches merged. The value is the one of the merged
   results.
3. The error is the batch-means error: each batch is also evaluated alone, and with `w_i`
   the ions of batch `i` and `xbar` the ion-weighted mean of the batch values `x_i`,
   `err = sqrt(sum_i w_i² (x_i - xbar)²) / sum_i w_i * sqrt(n / (n - 1))`. It is written
   to `<value>_err`. With one batch the error is NaN, and only numeric columns get one.
4. When all the batches planned so far are done, the error is compared with the goal (`abs`,
   or `rel` times the absolute value). If it is met, the point is `done`. If not, batches
   are added: the ions that would reach the goal are estimated as `ions * (err / goal)²`
   with a 10 % margin. Every round adds at least one batch, and no more than `max_ions`
   allows.
5. At `max_ions` without the goal met, the point has status `max_ions`. The scan prints a
   warning, and the point's error is above the goal. The command line still exits with 0.

Edge cases. For `rel` with a value of zero (or NaN), the goal is zero, which is never met:
the batches double until `max_ions`. A target that is not an extracted column fails the
point at its first decision (it is not retried).

The `batch_finished` events carry each decision (see [Live views](#live-views)). `n_ions`
in the result is the sum of the batch ions. `seeds` lists the seeds of the finished batches,
comma-separated.

A stopped precision point keeps its finished batches: its row has status `stopped` and
the estimate of those batches.

## The result table

`run_scan` returns a `ScanResult`, and writes the same table to `result/scan.csv` with
`result/metadata.yaml`. Columns, in this order:

1. one column per parameter (for example `ion.energy_keV`)
2. `id`: the point id
3. one column per extracted value, and `<value>_err` next to it (NaN unless an estimator
   filled it in)
4. `n_ions`: ions run for the point (NaN if it did not run)
5. `seeds`: the seed of the point, or the seeds of the batches (comma-separated)
6. `status` (see below)
7. `error`: the error of a failed or stopped point, else NaN
8. `path`: where the point's outputs are, relative to the scan folder

Statuses:

| Status | Meaning |
|---|---|
| `done` | the point finished |
| `cached` | the point finished, and every run it needed came from the cache (no SRIM run) |
| `failed` | the point failed after its retries; `error` says why and `points/<id>/error.log` has the details |
| `max_ions` | a precision point reached `max_ions` before its goal (a warning was printed) |
| `stopped` | the scan was stopped before the point finished |
| `missing` | a collected Slurm scan: the point has no outputs yet (`error` says "not run yet") |

Exports and the plot:

```python
result.to_csv("out")  # out/scan.csv and out/metadata.yaml
result.to_parquet("out/scan.parquet")  # needs pysrim2[parquet]
result.to_hdf5("out/scan.h5")  # needs pysrim2[hdf5]
ax = result.plot("ion.energy_keV", "mean_depth_A")  # needs pysrim2[plot]
```

`plot(x, y, hue=True)` draws `y` against the parameter `x`, one series per value of the
hue parameter (by default the second scanned parameter; `hue=False` for one series), with
error bars from `<y>_err`. `ax` is a matplotlib Axes, or a new one is made.

The exports hold no host paths: `path` is relative to the scan folder.

## Cache and reruns

Every finished SRIM run is kept in a cache. The key of a run is the SHA-256 of its run
spec (the canonical YAML that writes `TRIM.IN`, `number_ions` included) and its seed. The
key does not include provenance, so the same run from another scan has the same key.

Where the cache is:

- by default, `<scan folder>/cache`;
- `$PYSRIM2_CACHE` replaces that default for every scan that does not name a cache;
- `--cache DIR` (command line) or `cache=DIR` (Python) names a folder;
- `--no-cache` or `cache=False` turns it off.

Consequences:

- A rerun in the same scan folder computes nothing: its points have status `cached`.
- A scan in a new folder reuses nothing, unless it shares a cache (`--cache`, or
  `PYSRIM2_CACHE`). Set the variable once to share a cache between all your scans.
- A precision rerun with a tighter goal runs only its new batches.
- Two scans that need the same run compute it once: the second one waits for the first
  and then uses its result.
- Failed runs are never stored.

Entries record the SRIM build they were made with. If the installed build differs, the
run is computed again (with one warning per entry), and the new run replaces the old
entry.

Large output files of an entry are hard links shared with the point folders (files of at
least 1 MiB that are not `run.yaml` or an input). Do not edit those files in place. Copy them
first if you change them.

Clearing the cache:

```shell
pysrim2 scan clean scan-si                  # the cache of that scan folder
pysrim2 scan clean --cache ~/pysrim-cache   # a shared cache
pysrim2 scan clean scan-si --all            # the whole scan folder (only one with a scan.yaml and marker)
pysrim2 scan clean scan-si --dry-run        # list what would go
```

Cache entries that a running scan uses are kept. `srim.scan.clear_cache(path)` does the
same from Python.

## Backends

Where the units of a scan (points, or batches of a precision point) run.

### Local workers (the default)

`run_scan` starts `workers` worker processes (default: the CPU count minus one, at least
one, and never more than the number of units). Each worker has one private run directory
(see {doc}`concurrency`), reused for all its units. The parent process hands out the
units, runs the extractors and writes the events. Extractors therefore need not be
picklable.

A failed unit is run again with the same seed, `retries` times (default 1, from
`--retries`). Then its point has status `failed`, and the scan goes on. Each attempt
appends to `error.log` in the point's folder. A worker that dies without a message counts
as a failed attempt. If workers keep dying (more than twice the number of workers), the
scan stops with an error.

### Containers

```shell
pysrim2 scan run scan.yaml --backend container --ui plain
pysrim2 scan run scan.yaml --backend container --image ghcr.io/fsibt/pysrim2:main --ui plain
```

Each unit runs in its own container of the pysrim2 image (see {doc}`container`), started
as `pysrim2 run-job` on a job folder in a temporary directory. The events, the result and
the cache are the same as for the local backend.

- The image is `$PYSRIM2_IMAGE` if set, else the image of this pysrim2 release (`--image`
  overrides both).
- The runtime is the first of Podman and Docker that is installed and answers `info`
  (Podman is tried first). The command line has no option for
  it: from Python, pass `runtime="podman"` or `runtime="docker"` to `run_scan`. Without a
  runtime the scan fails before any worker starts (exit 2), with the hint to run
  `pysrim2 install runtime`.
- Podman runs each container with `--userns=keep-id:uid=1000,gid=1000` and relabels the
  job folder (`:Z`). Docker gets the seccomp profile that wine needs (pysrim2 copies it into
  its data directory) and a job folder that everyone can write.
- When a worker is stopped, it removes its container (`rm -f`, up to 30 s). A worker killed
  with SIGKILL cannot do this: its container then stops at the end of its SRIM run, because
  it was started with `--rm`.
- A custom `runner=` cannot be used with this backend.

### Slurm (job arrays)

On a cluster, pysrim2 can write the job folders and an array script, and collect the
results afterwards. The compute nodes need no scan process:

```shell
pysrim2 scan prepare scan.yaml -o my-scan              # one job folder per point
pysrim2 scan prepare scan.yaml -o my-scan --jobs 8     # 8 job folders, points dealt round-robin
cd my-scan && sbatch submit.sbatch                     # the array: one task per job folder
# when the tasks are done:
pysrim2 scan collect my-scan
```

- `submit.sbatch` sets `#SBATCH --array=0-<jobs - 1>`. Its other `#SBATCH` lines are
  commented out (`##SBATCH`). Remove one `#` from the ones you need (time, memory,
  partition) before you submit.
- `pysrim2` must be on `PATH` on the compute nodes (load its module or activate its
  environment).
- Each task runs `pysrim2 run-job jobs/<k>`. Its outputs go to `jobs/<k>/out/<point>/`.
  You can run a job folder by hand the same way, and run it again: finished units are
  skipped.
- `collect` builds the result from whatever the tasks produced. A point without outputs
  has status `missing`, so you can collect early and again later. The finished runs go
  into the cache (`--cache DIR` or `--no-cache`, as for `scan run`).
- Fixed ion counts only. A precision target needs the local or container backend, because
  its batches depend on the results of earlier batches.

Exit codes of the job commands: `run-job` 0 every unit done, 1 some unit failed, 2 not a job
folder. `scan collect` 0 every point done, 1 some point failed or missing, 2 bad input.

## Live views

While a scan runs, `pysrim2 scan run` shows its progress in one of these views. The
`--ui` option chooses it:

| `--ui` | What it is | Needs |
|---|---|---|
| `plot` (default) | the live plot, with a status strip | `pysrim2[tui]`, and stdin and stdout on a terminal |
| `dashboard` | the full-screen dashboard: counts, workers, throughput, the points table, warnings | `pysrim2[tui]`, and a terminal |
| `web` | the same app as `plot`/`dashboard`, served to a browser | `pysrim2[tui]` (textual-serve included) |
| `plain` | one status line when the state changes | nothing |
| `json` | every event as one JSON line on stdout | nothing |

When a view cannot run, `pysrim2` prints the reason on stderr and uses `plain`. `json`
writes only events to stdout; the result path goes to stderr.

The plain status line looks like this, printed when the counts or the ions change, at most
every two seconds (the first and the last line are always printed):

```text
done 3/10  running 2  queued 5  failed 0  ions 1200  elapsed 0:01:02  eta 0:02:10
```

The terminal views (`plot` and `dashboard`):

| Key | Action |
|---|---|
| `p` | pause or resume: no new point starts while paused, running points finish |
| `q` | stop: running points are killed, finished points are kept (Ctrl-C does the same; a second Ctrl-C quits the view at once) |
| `v` | switch between plot and dashboard |
| `l` | show the `error.log` of the selected point, or of the last failed one |

The plot shows the extracted value `y` against the parameter `x`, with one series per value
of a second parameter. The defaults are the first scanned parameter for `x`, the second for
the series, and the first extracted value for `y`. Choose them with `--plot X,Y[,HUE]`:
`--plot ion.energy_keV,mean_depth_A` or `--plot ion.energy_keV,mean_depth_A,` (no series).

The views redraw in place, work over SSH and in tmux, and never hold up the workers.

### Web view

```shell
pysrim2 scan run scan.yaml --ui web
```

The command prints a URL (`http://127.0.0.1:8000` by default). Open it in a browser on the
same machine, or reach it over SSH: `ssh -L 8000:127.0.0.1:8000 user@host`, then open the
URL on your machine. Each browser tab runs its own viewer and replays the scan's events
file, so several tabs can watch one scan, and a tab opened late catches up. `p` and `q` work in the browser. The server stops a few seconds after the scan ends, so
the final state stays visible.

`--port` changes the port (default 8000). `--host` changes the address the server listens
on (default `127.0.0.1`, this machine only).

Security. The web view has **no authentication** and **no Origin check**. Any web page that
a browser on this machine opens can connect to the port and pause or stop the scan, even
with the default host. Use `--ui web` on trusted machines only. A `--host` other than the
loopback address lets the network connect; `pysrim2` warns when the view starts. The web view
writes `events.jsonl` and `control.jsonl` into the scan folder.

### Your own consumer

`run_scan(..., on_event=f)` calls `f(event)` for each event, from one thread, in order. An
exception in `f` is logged and the scan goes on. Keep `f` quick: the events of a worker
are handled in the scan loop. Events are dataclasses in `srim.scan.events`, with `kind`,
`time` and their fields; `event.to_dict()` and `event.to_json()` give JSON-safe data.
`srim.scan.events.event_from_dict` reads them back.

Notebook. `srim.ui.LiveView` draws the status and the plot in a Jupyter cell while the scan
runs:

```python
from srim.ui import LiveView

result = run_scan(scan, on_event=LiveView())
```

It needs IPython and matplotlib (`pip install "pysrim2[plot]" ipython`), and a Jupyter
session: in a script it refuses to start, so use `PlainProgress` there. Its plot updates at
most every `every` seconds (default 1). See the scan notebook in `examples/notebooks`.

## Stopping

- Ctrl-C (or SIGTERM, or `q` in a view, or `stop_event` in Python) stops the scan. No new
  unit starts. Running units get SIGTERM, with their SRIM programs, and SIGKILL after a grace
  period of 10 s. The finished points are kept; the others get status `stopped`, and the result is
  written. A second Ctrl-C raises `KeyboardInterrupt` in the Python caller.
- `pause_event` (Python) or `p` in a view pauses without stopping.

## Exit codes

`pysrim2 scan run`:

| Code | Meaning |
|---|---|
| 0 | every point done or cached (a point at `max_ions` prints a warning) |
| 1 | some point failed or is missing, or the scan failed after it started |
| 2 | bad input: the spec, `--set` or the scan folder is invalid, or the scan could not start (no SRIM, no container runtime), or another scan holds the folder |
| 130 | stopped by Ctrl-C or SIGTERM; the partial result is written |

`pysrim2 scan prepare`: 0 written; 2 bad input (spec, `--set`, `--jobs`, or the folder already
holds a prepared scan); 1 the files could not be written.

`pysrim2 scan collect`: see [Backends](#slurm-job-arrays). `pysrim2 scan clean`: 0 done or
nothing to do; 1 refused (a symbolic link, the working directory or a folder above it, the
home folder, a scan that is running in the folder, a removal that failed); 2 bad input
(neither `DIR` nor `--cache`, or `--all` without `DIR`, a folder without the scan marker, not a
cache).

## Long runs over SSH: use tmux

A scan can run for hours. Start it in tmux, so that it keeps running when your SSH
connection drops:

```shell
tmux new -s scan                                      # a new session named scan
pysrim2 scan run scan.yaml --ui plain --output scan-run1
# press Ctrl-b, then d: detach. The scan goes on.
tmux ls                                               # list the sessions
tmux attach -t scan                                   # come back and look at it
```

`--ui plain` writes text lines that stay readable in tmux and in a log file. Use the
web view over an SSH port forward (see [Web view](#web-view)) if you prefer a browser.

pysrim2 has no detach or watch command on purpose: tmux does that job. Without tmux (or
`nohup`), a dropped connection sends SIGHUP to the scan, which pysrim2 does not catch: the
process ends and no result is written. The finished runs stay in the cache, so a rerun in
the same folder reuses them.
