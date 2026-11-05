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
them as a dict. The attribute arrays (`.depth`, `.ions`, ...) still work. Stopping power tables:
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
the example), not the rms radial distance (radial straggle, about 155 A).

Event files, as DataFrames with `ion`, `depth_A`, `y_A`, `z_A`, ... (`None` if absent):
`results.exyz` (`exyz=<eV step>`), `.backscat` (`backscattered=1`), `.transmit` (`transmit=1`),
`.sputter` (`sputtered=1`).

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
- Each `.run()` overwrites SRIM's output files in its directory; run calculations one at a
  time per SRIM directory, and copy outputs you want to keep.
- On Linux a run starts wine under xvfb; a failure raises `srim.srim.SRIMProcessError`
  with SRIM's stderr in the message.

## 7. Installing this skill

`pysrim2 install skill [--agent claude|all] [--scope user|project] [--yes]` copies it to
`~/.claude/skills/pysrim2/` (user) or `./.claude/skills/pysrim2/` (project); `--agent all` also
writes `~/.agents/skills/pysrim2/`.
