# run.yaml

A `run.yaml` describes a TRIM run completely. It round-trips to `TRIM.IN`: a
`TRIM` object, its spec and the `TRIM.IN` written from the spec are
equivalent (byte-identical text for typical values). The file has a schema
`version` (currently `1`), a `spec:` section (everything needed to regenerate
`TRIM.IN`) and an informational `provenance:` section.

## Example

```yaml
version: 1
spec:
  ion: {symbol: B, energy_keV: 20.0, angle_deg: 7.0}
  number_ions: 200
  calculation: 1
  seed: 12345
  settings: {description: pysrim run, ranges: 1}
  target:
    - name: Au
      thickness_nm: 100.0
      density_g_cm3: 19.3
      phase: 0
      elements:
        - {symbol: Au, stoich: 1.0, displacement_eV: 25.0, lattice_eV: 0.0, surface_eV: 3.0}
provenance:
  pysrim2_version: 0.8.0
  srim_sha256: ...
  platform: linux
  backend: wine
  started_utc: "2026-11-04T10:00:00Z"
  finished_utc: "2026-11-04T10:00:41Z"
  host: mybox
  seed: 12345
```

## Schema (version 1)

Units are part of the key names. Unknown keys are rejected.

Top level of `spec:`

| Key | Type | Default | Meaning |
|---|---|---|---|
| `ion` | mapping | required | the projectile |
| `target` | list of layers | required, at least one | layers from the surface inwards |
| `number_ions` | int | 1000 | ions to simulate (> 0) |
| `calculation` | int | 1 | TRIM calculation type, 1 to 7 (1 = quick Kinchin-Pease, 2 = full cascade) |
| `seed` | int | picked at load | random seed (>= 0), see [Seed](#seed) |
| `settings` | mapping | `{}` | `TRIMSettings` values that differ from the defaults |
| `beam` | mapping | absent | how a distributed beam runs: `mode` (`exact` or `binned`), `bins`, `max_bins`; see [Beam distributions](beam.md) |

`ion`

| Key | Type | Default | Meaning |
|---|---|---|---|
| `symbol` | str | required | element symbol |
| `mass_amu` | float | from the element table | mass in amu (> 0); written only if it differs from the table |
| `energy_keV` | float or distribution | required | energy in keV (> 0); a number or a distribution (see below) |
| `angle_deg` | float or distribution | 0.0 | angle of incidence in degrees, `0 <= angle < 90`; a number or a distribution |
| `azimuth_deg` | float or distribution | absent | direction around the beam axis in degrees, `0 <= azimuth < 360` |
| `position` | mapping | absent | start position: `y_nm`, `z_nm` and `depth_nm` (numbers or distributions, default 0); exact mode only |

A beam is *distributed* when `energy_keV` or `angle_deg` is a distribution, or when
`azimuth_deg` or `position` is given. A distributed beam is run with `run_beam` (or
`pysrim2 run`), not with a plain `TRIM` run: the sampled ions are written to `TRIM.DAT`
(exact mode) or the bins become runs (binned mode). A spec without a distribution is a single
beam, as before.

A distribution is a one-key mapping, such as `{gaussian: {mean: 20.0, sigma: 1.0}}`. The
kinds are `fixed`, `gaussian`, `uniform`, `triangular`, `lognormal`, `histogram` (a `file`,
or `edges` with `counts`, or `centres` with `counts`) and `function` (`ref:
python:<module>:<name>` with a `range`). Each takes an optional `range: [low, high]`. The
physical limits (energy above 0, angle in `[0, 90)`, depth inside the target) are applied
before sampling; see [Beam distributions](beam.md).

```yaml
ion:
  symbol: B
  energy_keV: {gaussian: {mean: 20.0, sigma: 1.0, range: [17.0, 23.0]}}
  angle_deg: {uniform: {low: 0.0, high: 5.0}}
  position:
    depth_nm: 0.0
    y_nm: {gaussian: {mean: 0.0, sigma: 50.0}}
```

`beam`

| Key | Type | Default | Meaning |
|---|---|---|---|
| `mode` | str | `exact` | `exact`: every ion is in a `TRIM.DAT` file; `binned`: one run per bin |
| `bins` | mapping | 10 energy bins, 6 angle bins | `energy_keV` and/or `angle_deg`: an int (equal-probability bins) or a list of edges (binned mode only) |
| `max_bins` | int | 2000 | binned mode refuses more combined bins than this |

`beam` is refused when no beam parameter is a distribution. In exact mode, `calculation` must be
1 or 2 (SRIM 4 or 5); a `position` distribution or a non-uniform azimuth needs exact mode.

Each entry of `target` (a layer)

| Key | Type | Default | Meaning |
|---|---|---|---|
| `name` | str | element symbols joined by `+`, like `Layer` (`Si+C`) | layer name (no double quote) |
| `thickness_nm` | float | required | thickness in nm (> 0) |
| `density_g_cm3` | float | required | density in g/cm3 (> 0) |
| `phase` | int | 0 | 0 solid, 1 gas |
| `elements` | list | required, at least one | the elements of the layer |

Each entry of `elements`

| Key | Type | Default | Meaning |
|---|---|---|---|
| `symbol` | str | required | element symbol |
| `stoich` | float | required | stoichiometry, any positive number (> 0) |
| `displacement_eV` | float | 25.0 | displacement energy in eV (>= 0) |
| `lattice_eV` | float | 0.0 | lattice binding energy in eV (>= 0) |
| `surface_eV` | float | 3.0 | surface binding energy in eV (>= 0) |
| `mass_amu` | float | from the element table | mass in amu; written only if it differs from the table (as in many SRIM-written `TRIM.IN` files) |

`settings` (keys of `srim.TRIMSettings`; the defaults are the values below)

| Key | Type | Default | Meaning |
|---|---|---|---|
| `description` | str | `pysrim run` | run title (no double quote); always written |
| `ranges` | int 0/1 | 0 | write `RANGE_3D.txt`; always written |
| `backscattered` | int 0/1 | 0 | write `BACKSCAT.txt` |
| `transmit` | int 0/1 | 0 | write `TRANSMIT.txt` |
| `sputtered` | int 0/1 | 0 | write `SPUTTER.txt` |
| `collisions` | int 0, 1, 2 | 0 | `COLLISON.txt`: 1 ion collisions, 2 with recoils |
| `exyz` | int | 0 | `EXYZ.txt` energy increment in eV (0 = off) |
| `reminders` | int 0/1 | 0 | SRIM reminders |
| `autosave` | int | 0 | autosave every N ions |
| `plot_mode` | int 0..5 | 5 | SRIM plot window (5 = no plot) |
| `plot_xmin`, `plot_xmax` | float | 0.0 | plot depth range in Angstrom (`xmin <= xmax`) |
| `bragg_correction` | float | 1.0 | Bragg correction |
| `version` | int 0/1 | 0 | SRIM version flag |

`settings` lists only values that differ from the defaults, plus
`description` and `ranges`. The angle is `ion.angle_deg` and the random seed
is the top-level `seed` (neither is in `settings`).

Other details:

- Unit conversion (nm to Angstrom, keV to eV) is done in decimal arithmetic;
  values with a short decimal form round-trip exactly. A width such as
  `0.1 + 0.2` Angstrom, which has no short form, may differ in the last digit.
- A `run.yaml` is read with `RunSpec.from_yaml`; `provenance:` is ignored when
  loading, so a file written by `TRIM.run()` is a valid input.

## Validation errors

Invalid files raise `srim.spec.SpecError` (a `ValueError`) naming the dotted key
at fault. `pysrim2 run` and `pysrim2 import` print the message and exit with
code 2, without running anything. Examples:

```text
spec.target[0].thickness_nm must be > 0, got 0.0
unknown key 'spec.ion.energy' (allowed: angle_deg, energy_keV, mass_amu, symbol)
missing required key 'spec.ion.energy_keV'
spec.ion.symbol 'Xx' is not a chemical element symbol
spec.calculation must be 1..7, got 9
spec.ion.angle_deg must be in [0, 90), got 95.0
spec.settings.ranges has an invalid value 2 (must be 0 or 1)
```

Errors for `--set` assignments name the key without the `spec.` prefix
(`ion.angle_deg must be in [0, 90), got 95.0`). Types are checked too (`number_ions must be an integer, got 'many'`);
booleans are not accepted where numbers are expected.

## Seed

TRIM draws all randomness from the seed in `TRIM.IN`.

- A `seed` in the file is used as given.
- Without one, pysrim2 picks a random seed when the file is loaded and records
  it in the spec, so the run that follows can be repeated exactly.
- `TRIM.to_spec()` and `TRIM.run()` always record the seed that was used, in
  `spec.seed` and in `provenance.seed`.
- `pysrim2 import` keeps the seed found in the `TRIM.IN` (SRIM's own files
  often have 0).

## Provenance

`provenance:` records how a file was produced and never affects what is run:

| Key | Meaning |
|---|---|
| `pysrim2_version` | version of pysrim2 |
| `srim_sha256` | SHA-256 of the SRIM download that was installed (from the `.pysrim2-installed` marker; `null` if unknown) |
| `platform` | `sys.platform` (`linux`, `win32`, `darwin`) |
| `backend` | `wine` or `native` |
| `started_utc`, `finished_utc` | start and end of the run, ISO 8601 UTC |
| `host` | host name of the machine |
| `seed` | the seed that was used |

The host name only appears in `run.yaml` (it identifies a machine, so it is
kept out of everything that is shared): `Results.spec` is the spec without
provenance, and the Parquet, HDF5 and CSV exports contain the spec but never the
provenance or host (see {doc}`export`).

## Python

```python
trim = TRIM.from_yaml("run.yaml")  # or TRIM.from_spec(RunSpec / dict)
trim.to_yaml("run.yaml")  # trim.to_spec() gives the RunSpec
results = trim.run()  # writes run.yaml (with provenance) into the run directory
results.to_parquet("out")  # the spec (not the provenance or host) goes into the metadata
```

`TRIM.run(write_spec=False)` skips the file. `run.yaml` is written into the
SRIM directory next to the outputs (the directory the results are read from);
`TRIM.copy_output_files` copies it along with them. `RunSpec.with_overrides`
applies the same `key=value` assignments as `--set`.

## Command line

See {doc}`cli`.

```shell
pysrim2 run run.yaml [--set KEY=VALUE ...] [--output DIR]
pysrim2 import TRIM.IN -o run.yaml
```

`--set` takes dotted keys into the `spec:` section and converts the value with
the schema (`ion.energy_keV=30` is a float, `number_ions=500` an integer).
Items of a list are addressed by index, either with brackets or with a dot:

```shell
pysrim2 run run.yaml --set 'target[0].thickness_nm=50'
pysrim2 run run.yaml --set target.0.thickness_nm=50 target.0.elements.0.stoich=0.5
```

(quote the bracket form in shells that expand `[...]`). The index must exist;
you cannot add layers or elements with `--set`, edit the file for that.

Changing a symbol with `--set` also drops the old mass: after
`--set ion.symbol=Au` the ion has the element table's mass for gold (196.967
amu), not the boron mass that was in the file. The same goes for
`target.N.elements.M.symbol`. Set `ion.mass_amu` (or the element's `mass_amu`)
in the same call to use a different mass (an isotope); the explicit value then
wins.

`pysrim2 import TRIM.IN -o run.yaml` converts an existing `TRIM.IN` (written by
SRIM or pysrim): thicknesses go from Angstrom to nm, masses that differ from the
element table are kept as `mass_amu`. A `TRIM.IN` that SRIM wrote is not always
reproduced byte for byte by the spec (SRIM formats numbers its own way), but
importing it, writing `TRIM.IN` from the spec and importing that again gives
the same spec.

## Reproducibility

The same spec with the same seed on the same machine gives identical ion
positions (`results.range3d.df`); the integration tests assert this. Across
platforms it is not guaranteed. What CI observed for the reference case
(boron in gold, 200 ions, seed 12345), comparing a digest of the sorted
`RANGE_3D` table:

- Linux under wine and the container image gave bit-identical per-ion results.
  The container runs the same SRIM binary under wine.
- Native Windows gave a different per-ion result, but the same summary: mean
  depth 281.977 A and 147 stopped ions.

So the same machine, spec and seed give identical results, and results from
other platforms should agree statistically (and for this case in the summary
numbers) but not ion by ion. The `repro-report` job in `ci.yml` compares the
Linux (wine) and Windows digests in its job summary; the container workflow
uploads its own digest separately. The comparison is informational and never
fails the build.
