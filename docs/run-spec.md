# run.yaml

A `run.yaml` describes a TRIM run completely. It round-trips to `TRIM.IN`: a
`TRIM` object, its spec and the `TRIM.IN` written from the spec are
equivalent (byte-identical text for typical values).

```yaml
version: 1
spec:
  ion: {symbol: B, mass_amu: 10.8109998703, energy_keV: 20.0, angle_deg: 7.0}
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
  pysrim2_version: 0.8.0.dev0
  srim_sha256: ...
  platform: linux
  backend: wine
  started_utc: "2026-11-04T10:00:00Z"
  finished_utc: "2026-11-04T10:00:41Z"
  host: mybox
  seed: 12345
```

`spec:` is everything needed to regenerate `TRIM.IN`; `provenance:` is
informational and ignored when loading. Units are in the key names.

- `settings` holds only values that differ from the `TRIMSettings` defaults
  (plus `description` and `ranges`, always written). The angle is
  `ion.angle_deg`, the random seed is `seed`.
- `seed`: if omitted, one is picked when the file is loaded. `TRIM.to_spec()`
  always records the seed that was used, so a rerun reproduces the run.
- `mass_amu` on an element is only written when it differs from the element
  database (it does in many SRIM-written `TRIM.IN` files).
- Unit conversion (nm to Angstrom, keV to eV) is done in decimal arithmetic;
  values with a short decimal form round-trip exactly. A width such as
  `0.1 + 0.2` Angstrom, which has no short form, may differ in the last digit.
- Invalid files raise `srim.spec.SpecError` (a `ValueError`) naming the dotted key,
  e.g. `spec.target[0].thickness_nm must be > 0, got 0.0`.

## Python

```python
trim = TRIM.from_yaml("run.yaml")  # or TRIM.from_spec(RunSpec / dict)
trim.to_yaml("run.yaml")  # trim.to_spec() gives the RunSpec
results = trim.run()  # writes run.yaml (with provenance) into the SRIM directory
results.to_parquet("out")  # the spec (not the provenance or host) goes into the metadata
```

`TRIM.run(write_spec=False)` skips the file. `run.yaml` is written into the
SRIM directory next to the outputs (the directory the results are read from);
`TRIM.copy_output_files` copies it along with them.

## Command line

See {doc}`cli`: `pysrim2 run run.yaml [--set ion.energy_keV=30] [--output DIR]` and
`pysrim2 import TRIM.IN -o run.yaml`.
