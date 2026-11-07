---
name: pysrim2
description: Run SRIM/TRIM ion-implantation simulations from Python with pysrim2 (import srim). Use for ion ranges and straggle, implantation depth profiles, radiation damage (vacancies, displacements), stopping power tables, layered targets, and when SRIM must run natively (Windows, Linux with wine) or in a container (macOS, no wine).
---

# pysrim2 (SRIM/TRIM from Python)

pysrim2 is the maintained continuation of pysrim: install `pysrim2`, `import srim`. It
drives the real SRIM-2013 programs (TRIM, SR Module) and parses their output.

Licence: SRIM is (c) SRIM.com (James F. Ziegler), free for non-commercial use only. Say so
when you set it up for someone, and keep `LEGAL.rtf` next to any SRIM copy.

## 1. Check the setup first

```bash
pysrim2 doctor --json
```

Every entry in `checks` has `name`, `ok`, `detail`, `fix`. If all are ok, run natively.
If not, apply the `fix` command, or use the container route (section 2). If the
`pysrim2` command is missing: `uv tool install pysrim2` (or `pip install pysrim2`).

Native install (`pysrim2 install --dry-run` shows every step and command first; steps
that need admin rights ask for confirmation or `--yes`):

| Platform | Command | What it does |
|---|---|---|
| Windows | `pysrim2 install` | `vb5` + `srim` + `skill`: VB5 runtime, OCX controls, SRIM-2013 |
| Linux with wine, or with no container runtime | `pysrim2 install` | `wine-packages` + `wine` + `srim` + `skill`: private wine prefix, SRIM-2013 |
| Linux with a container runtime and no wine | `pysrim2 install` | `image` + `skill`: pulls the container image |
| macOS | `pysrim2 install` | container only: `image` + `skill`, plus `runtime` (Podman) first if none is installed |

SRIM is downloaded to pysrim2's data directory (override: `PYSRIM2_SRIM_DIR`); it is never
part of the PyPI package.

## 2. Container route (no native SRIM)

The image `ghcr.io/fsibt/pysrim2` (linux/amd64) has pysrim2, SRIM and wine. Prefer Podman:

```bash
podman run --rm --userns=keep-id:uid=1000,gid=1000 -v "$PWD:/data:Z" ghcr.io/fsibt/pysrim2:<tag> python /data/run.py
```

- `<tag>`: `latest`, `X.Y.Z` or `X.Y` (release; no `v` prefix), `main` (development). `pysrim2 install image` pulls it.
- Docker 29+ needs a seccomp profile for 32-bit wine; `pysrim2 install image` writes it and
  prints the exact command: `docker run --rm --security-opt seccomp=<path> -v "$PWD:/data" ...`.
- `--userns=keep-id:uid=1000,gid=1000` (Podman 4.3+) maps the container user to your own user, so results in `/data` belong to you.
- Never pass `-u`: the image's own non-root user owns what it needs.
- `/data` is the working directory. SRIM writes its raw output inside the container, so have
  the script copy what you need under `/data` (the example does this into `output/`).
- Apple silicon runs the amd64 image under emulation; expect it to be several times slower.

## 3. Building and running a calculation

```python
from srim import TRIM, Ion, Layer, Target

au = Layer.from_formula("Au", density=19.3, width=1000.0)  # g/cm3, angstrom
si = Layer.from_formula("Si", density=2.33, width=10000.0)
target = Target([au, si])  # first layer is hit first
ion = Ion("B", energy=20e3)  # energy in eV
results = TRIM(
    target,
    ion,
    number_ions=200,
    calculation=1,  # 1 = quick Kinchin-Pease, 2 = full damage cascade
    angle_ions=7.0,  # degrees from the surface normal
    ranges=1,  # also write RANGE_3D.txt (per-ion final positions)
    random_seed=12345,  # reproducible runs
).run()
```

Units: widths in angstrom (1 nm = 10 A, 100 nm = 1000 A), energies in eV (20 keV = 20e3),
densities in g/cm3. Compounds: `Layer.from_formula("SiO2", density=2.65, width=...)`.

`run()` returns `Results`. Every table is a DataFrame: `results.vacancy.df` (also `.ioniz`, `.phonons`,
`.range`, `.etorecoils`, `.novac`; first column `depth_A`, names carry SRIM's units, e.g.
`knock_ons_per_A_per_ion`, `vacancies_Au_per_A_per_ion`), and `results.to_dataframes()` returns all of
them as a dict (without the potentially huge `collision` table; add it with `include=["collision"]`). The attribute arrays (`.depth`, `.ions`, ...) still work. Stopping power tables:
`from srim import SR` (`SR(layer, ion, output_type=5).run()`).

## 4. Reference example: implantation depth and straggling

`examples/boron_in_gold_on_silicon.py` (next to this file): 200 B ions at 20 keV, 7 degrees,
into 100 nm Au on Si, quick KP, seed 12345. It prints the mean depth (about 282 A) and the
straggling, and copies the output files to `./output/`.

Per-ion final positions (needs `ranges=1`) are `results.range3d.df` with columns
`ion, depth_A, y_A, z_A` (depth along the surface normal, y and z lateral); `range3d` is `None`
if `RANGE_3D.txt` was not written. `from srim.analysis import implant_summary` gives
`n_ions` and SRIM's own definitions (the `RANGE.txt` header, also `results.range.summary`):
`mean_depth_A`, `straggle_depth_A`, `lateral_range_A`, `lateral_straggle_A`, `radial_range_A`,
`radial_straggle_A`, `skewness`, `kurtosis`. Report SRIM's "lateral straggle" (about 242 A in
the example). The radial straggle (about 155 A) is the standard deviation of the radial distance r, a
different quantity; the mean depth is about 282 A.

Event files, parsed into objects whose `.df` is a DataFrame with `ion`, `depth_A`, `y_A`, `z_A`, ... (the object is `None` if absent):
`results.exyz.df` (`exyz=<eV step>`), `.backscat.df` (`backscattered=1`), `.transmit.df` (`transmit=1`),
`.sputter.df` (`sputtered=1`).

## 5. Cascades, export and run.yaml

- `collisions=2` writes `COLLISON.txt` (huge; full cascade is `calculation=2`). Never load it
  whole: `for event in results.collision.iter_events(): ...` streams flat dicts (`kind` is `ion` or
  `recoil`; `ion`, `depth_A`, `y_A`, `z_A`, `atom`, `recoil_energy_eV`, `target_vacancies`, ...);
  `results.collision.df` collects them; `results.collision.to_parquet(path, chunk_rows=100_000)`
  writes them in chunks with small memory.
- Export: `results.to_parquet(dir)` (needs `pysrim2[parquet]`), `.to_hdf5(file)`
  (`pysrim2[hdf5]`), `.to_csv(dir)`; `include=[...]` selects tables (`collision` only on
  request). Files carry metadata and the run spec.
- `run.yaml` describes a run (units in key names: `energy_keV`, `thickness_nm`, `density_g_cm3`;
  the seed is always recorded). `trim.to_yaml("run.yaml")`, `TRIM.from_yaml("run.yaml")`,
  `TRIM.from_spec(RunSpec)`; `TRIM.run()` writes `run.yaml` with provenance next to the outputs.
  Command line, no script needed:
  `pysrim2 run run.yaml --set ion.energy_keV=30 --output out/`; `pysrim2 import TRIM.IN -o run.yaml`.
  `--set` addresses list items by index: `target[0].thickness_nm=50` or `target.0.thickness_nm=50`.
  A sample is `examples/scripts/run_yaml_example/` in the pysrim2 repository.

## 6. Run time and practical advice

- Quick KP (`calculation=1`) is seconds for hundreds of ions natively. The full cascade
  (`calculation=2`) is far slower (minutes to hours for thousands of ions, per-ion cost grows
  with energy). Start with 100-200 ions, check the result, then scale up.
- Statistical error falls as 1/sqrt(N); use `random_seed` for reproducible comparisons.
- Quick KP gives vacancy estimates; use the full cascade when displacement detail matters.
- Results are SRIM-2013 Monte Carlo output, good to roughly 10 % for ranges. Do not quote
  more digits than the statistics support.
- Each `.run()` works in its own private copy of the SRIM directory (so runs can overlap);
  read outputs from the returned results (`results.directory` is the folder) or pass
  `output_dir=` to get them in a folder you choose.
- On Linux a run starts wine under xvfb; a failure raises `srim.srim.SRIMProcessError`
  with SRIM's stderr in the message.

## 7. Parameter scans

Use a scan when one calculation must be repeated for many values of a parameter (ion
energy, angle, a layer thickness) and you want one table back. Use `TRIM(...).run()` (section 3)
for a single run whose full output you need.

A scan is a base run plus the values to vary. Python:

```python
from srim import TRIM, Ion, Layer, Target
from srim.scan import Scan, run_scan

target = Target([Layer.from_formula("Si", density=2.33, width=10000.0)])  # 1000 nm
base = TRIM(
    target, Ion("B", energy=20e3), number_ions=100, calculation=1, ranges=1, random_seed=12345
).to_spec()
scan = Scan(
    base,
    grid={"ion.energy_keV": [10.0, 20.0, 40.0]},
    extract=["implant_summary"],
    ions=100,
    seed=12345,
)
if __name__ == "__main__":  # required: worker processes import this file
    result = run_scan(scan, workers=2, scan_dir="my-scan")
    print(result.table[["ion.energy_keV", "mean_depth_A", "status"]])
```

The same scan as a file (a `scan:` section next to `spec:`, as in `run.yaml`), run from the
shell with JSON events for a program to read:

```bash
pysrim2 scan run scan.yaml --ui json --output my-scan > events.jsonl 2> scan.log
echo $?  # 0 all points done or cached; 1 a point failed or is missing; 2 bad input; 130 stopped
```

- The result is `my-scan/result/scan.csv`, one row per point: the parameters (`ion.energy_keV`),
  the extracted values (`mean_depth_A`, ...), `<value>_err` (NaN for a fixed ion count), `n_ions`,
  `seeds`, `status` (`done`, `cached`, `failed`, `max_ions`, `stopped`, `missing`), `error`, `path`.
  Read it with `pandas.read_csv`.
- Extractors name their columns: `implant_summary` (needs `ranges=1`: `mean_depth_A`,
  `straggle_depth_A`, `lateral_straggle_A`, ... in Angstrom), `sputter_yield` (needs
  `sputtered=1` and `calculation=2`), `backscattered_fraction` and `transmitted_fraction`
  (`backscattered=1`, `transmit=1`), `vacancy_at` and `ions_in_layer` (YAML:
  `{vacancy_at: {depth_nm: 50}}`). The full list with units is in `docs/scans.md`.
- Precision targets (`precision: {target: ..., rel: 0.05, max_ions: ...}` in `scan:`) run each
  point until its error is small enough; a point at `max_ions` has status `max_ions`.
- Results are reused: a rerun in the same folder runs nothing (status `cached`), with or without
  `seed`. A new folder reuses nothing unless both scans name one cache (`--cache DIR` or
  `PYSRIM2_CACHE`) and give the same `seed`.
- A scan folder is new or empty, or an existing scan folder (its `scan.yaml` is rewritten). Two
  scans in one folder at once: the second exits 2.
- `--backend container` runs each point in a container of the pysrim2 image (Podman or Docker).
- Do not start two scans with the same `--output` at once, and do not use `--ui web` on a shared
  machine: it has no authentication. Long scans: run them in tmux
  (`tmux new -s scan`, then `Ctrl-b d` to detach).
- Cluster: `pysrim2 scan prepare scan.yaml -o DIR`, `sbatch submit.sbatch` in `DIR`, then
  `pysrim2 scan collect DIR` once the array has finished.
- Never edit the output files of a point or the cache in place: large files are hard links.

## 8. Beam distributions

Use this when the beam is not one energy and one angle: an energy spread, a divergence, an
azimuth, or a start position. Full reference: `docs/beam.md` in the repository, or the
[beam page](https://pysrim2.readthedocs.io/en/latest/beam.html).

Python (the energy is a Gaussian with mean 20 keV and sigma 1 keV):

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
    with run_beam(spec, output_dir="beam-out") as results:
        print(implant_summary(results.range3d.df)["mean_depth_A"])
        print(sampled_ions(results).head())  # exact mode: the ions SRIM ran
```

The same beam in `run.yaml`: `ion: {symbol: B, energy_keV: {gaussian: {mean: 20.0, sigma: 1.0}}, ...}`,
then `pysrim2 run run.yaml` (add `--set beam.mode=binned` to switch modes).

- **Shapes**: a number (fixed), `gaussian` (mean, sigma), `uniform` (low, high), `triangular`
  (low, mode, high), `lognormal` (mu, sigma), `histogram` (`file`, or `edges` with `counts`, or
  `centres` with `counts`), `function` (`ref: python:<module>:<name>` and a `range`). Any shape
  takes `range: [low, high]` to truncate.
- **What can be a distribution**: `ion.energy_keV`, `ion.angle_deg`, `ion.azimuth_deg`, and
  `ion.position` (`y_nm`, `z_nm`, `depth_nm`). Each is drawn on its own (no correlations).
- **Which mode**: `exact` (the default) samples every ion and runs SRIM on exactly those ions
  (`TRIM.DAT`). Use it for positions, a non-uniform azimuth, or when the sampled ions matter.
  `binned` (`beam: {mode: binned, bins: {energy_keV: 10, angle_deg: 6}}`) runs one SRIM run per
  bin and has no ion limit; use it for large ion counts when only energy and angle are spread.
  Binned mode keeps `calculation` as given; exact mode maps 1 to SRIM 4 and 2 to SRIM 5, and
  refuses 3 (sputtering), so use binned for those.
- **Limits**: at most 99 999 ions per TRIM.DAT. `run_beam` splits a bigger exact beam into units and
  merges them; `TRIM.from_spec(spec).run()` refuses it. Binned beams are capped at `beam.max_bins`
  (2000 by default). `settings.description` must be ASCII for an exact run.
- **Reading results**: `run_beam` returns `Results` for one exact run, and `MergedResults` for a
  batched or binned run. Merged per-ion tables have a `batch` column; binned ones also have
  `bin`, `bin_energy_keV`, `bin_angle_deg`, and `results.beam_bins` lists the bins. `sampled_ions`
  gives the exact run's ions (energy, angle, azimuth, position). `open_results(point_dir)` reopens a
  finished scan point. `plot_beam(results, "energy_keV")` needs `pysrim2[plot]`.
- **Scans**: vary a shape parameter with a dotted key, for example
  `ion.energy_keV.gaussian.sigma` or `beam.bins.energy_keV`, in `grid=`. An exact distributed point
  above 99 999 ions needs `batch_ions: 99999`.
- **Refusals** are `SpecError`s that name the dotted key (for example `ion.energy_keV: no
  probability in [...]` or `beam.max_bins: ...`). Read the message and fix the spec rather than
  changing the beam by hand.
- **Python density functions** must be module-level functions in an importable module (not
  `__main__`, not a lambda). Run from the folder that holds the module.

## 9. Installing this skill

`pysrim2 install skill [--agent claude|all] [--scope user|project] [--yes]` copies it to
`~/.claude/skills/pysrim2/` (user) or `./.claude/skills/pysrim2/` (project); `--agent all` also
writes `~/.agents/skills/pysrim2/`.
