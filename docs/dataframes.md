# DataFrames

Every SRIM output that pysrim2 reads has a `.df` attribute: a
{class}`pandas.DataFrame` with snake_case column names and the unit as a suffix
(`depth_A`, `energy_eV`, `y_A`, ...). The units are SRIM's own; nothing is
converted silently (`A` is Angstrom). The older attributes (`.depth`, `.ions`,
...) keep working unchanged.

```python
from srim.output import Results

results = Results("test_files/1")  # a folder with SRIM output files
results.ioniz.df.head()
results.ioniz.metadata  # {'ion': 'Ni', 'energy_eV': 5000000.0, 'num_ions': 1000, 'source': ...}
dfs = results.to_dataframes()  # dict: table name -> DataFrame, only the outputs that exist
```

`Results.to_dataframes()` returns the keys `ioniz`, `vacancy`, `novac`,
`e2recoil`, `phonon`, `range`, `range3d`, `exyz`, `backscat`, `transmit` and
`sputter`. An output whose file is missing is left out (for
example `novac`, which is typically absent for quick Kinchin-Pease runs). Because `collision`
can have millions of rows, it is not included by default; ask for it with
`results.to_dataframes(include=["collision"])` (the same `include=` selection as the
exports; naming a table that is not available raises `ValueError`), or stream it (see
[below](#collision-the-event-stream)).

## Lazy loading

The depth profiles (`ioniz`, `vacancy`, `novac`, `e2recoil`, `phonon`, `range`)
are read when `Results` is created. The large per-ion files (`range3d`, `exyz`,
`backscat`, `transmit`, `sputter`, `collision`) are read on first access of the
attribute and cached: creating `Results` stays cheap, and a broken file raises
(or warns) at that access. A file changed after the first access is not re-read;
create a new `Results`. The attribute is `None` if the file is absent.

## Depth profiles

TRIM normally writes these six files (`novac` is absent for Kinchin-Pease runs). All have `depth_A` as first column
(left edge of the depth bin, Angstrom) and one row per depth bin. `.metadata`
holds `ion` (symbol), `energy_eV`, `num_ions` and `source` (the file read).
Values are normalised per ion, so they do not depend on the number of ions.

The element columns are named after the elements of the target (`Ni`, `Si`,
`C`, ...), in the order SRIM lists them. In a multi-element target the element
names come from the table header; if they cannot be read, pysrim2 warns and
falls back to `1`, `2`, ... . The tables below show a single-element target
(Ni); `<el>` stands for an element symbol.

### `ioniz` (IONIZ.txt)

Energy lost to electrons (ionisation) by depth.

| Column | Unit | Meaning |
|---|---|---|
| `depth_A` | A | depth |
| `ions_eV_per_A_per_ion` | eV/A/ion | ionisation by the primary ions |
| `recoils_eV_per_A_per_ion` | eV/A/ion | ionisation by recoil atoms |

### `vacancy` (VACANCY.txt)

Vacancy production by depth.

| Column | Unit | Meaning |
|---|---|---|
| `depth_A` | A | depth |
| `knock_ons_per_A_per_ion` | 1/A/ion | knock-on atoms (ion-to-atom collisions) |
| `vacancies_<el>_per_A_per_ion` | 1/A/ion | vacancies of element `<el>`, one column per element |

### `novac` (NOVAC.txt)

Replacement collisions. Typically absent for quick Kinchin-Pease
calculations (`calculation=1`), present for full cascades.

| Column | Unit | Meaning |
|---|---|---|
| `depth_A` | A | depth |
| `replacements_per_A_per_ion` | 1/A/ion | replacement collisions |

### `e2recoil` (E2RECOIL.txt)

Energy transferred to recoil atoms by depth.

| Column | Unit | Meaning |
|---|---|---|
| `depth_A` | A | depth |
| `ions_eV_per_A_per_ion` | eV/A/ion | energy lost by the primary ions to recoils |
| `absorbed_<el>_eV_per_A_per_ion` | eV/A/ion | energy absorbed by recoils of element `<el>` |

### `phonon` (PHONON.txt)

Phonons (heat) produced by depth, in SRIM's unit Phonons/(Angstrom-Ion) (SRIM's plots treat them as energy).

| Column | Unit | Meaning |
|---|---|---|
| `depth_A` | A | depth |
| `ions_phonons_per_A_per_ion` | Phonons/(A ion) | phonons from the primary ions |
| `recoils_phonons_per_A_per_ion` | Phonons/(A ion) | phonons from recoils |

### `range` (RANGE.txt)

Final-position (implantation) profile of the ions, and of the recoils of each
element.

| Column | Unit | Meaning |
|---|---|---|
| `depth_A` | A | depth |
| `ions_atoms_per_cm3_per_atoms_per_cm2` | (atoms/cm3)/(atoms/cm2) | stopped ions |
| `<el>_atoms_per_cm3_per_atoms_per_cm2` | (atoms/cm3)/(atoms/cm2) | recoils of element `<el>` that came to rest |

Multiply by the fluence in atoms/cm2 to get a concentration in atoms/cm3.

`Range.summary` is a dict with the quantities SRIM prints in the header of
`RANGE.txt`: `mean_depth_A` (projected range), `straggle_depth_A`,
`lateral_range_A`, `lateral_straggle_A`, `radial_range_A`, `radial_straggle_A`,
`skewness` and `kurtosis`. SRIM prints them with 4 significant digits.

## Per-ion tables

### `range3d` (RANGE_3D.txt)

Final position of every ion. Written when `ranges=1` in the settings (`TRIM(..., ranges=1)`, or `settings: {ranges: 1}` in `run.yaml`; the default is 0).
The file is also found in the `SRIM Outputs` subfolder. `.metadata`: as above
with `num_ions` the number of rows (SRIM can leave out a few ions).

| Column | Unit | Meaning |
|---|---|---|
| `ion` | | ion number |
| `depth_A` | A | final depth (x, along the beam) |
| `y_A` | A | final lateral position y |
| `z_A` | A | final lateral position z |

### `exyz` (EXYZ.txt)

Energy, position and stopping of each ion along its path, sampled every time
the ion has lost another `exyz` eV (the setting is the energy increment in eV;
0 switches the file off). `.metadata` has `ion`, `energy_eV`,
`energy_interval_eV`, `num_ions` and `source`. The parser is based on work by
philis-johan.

| Column | Unit | Meaning |
|---|---|---|
| `ion` | | ion number |
| `energy_keV` | keV | ion energy at this point |
| `depth_A` | A | depth |
| `y_A`, `z_A` | A | lateral position |
| `electronic_stopping_eV_per_A` | eV/A | electronic stopping at this point |
| `last_recoil_energy_eV` | eV | energy given to the last recoil |

### `backscat`, `transmit`, `sputter` (BACKSCAT.txt, TRANSMIT.txt, SPUTTER.txt)

One row per atom leaving the target: backscattered ions (`backscattered=1`),
transmitted ions (`transmit=1`) and sputtered target atoms (`sputtered=1`;
sputtering needs a full cascade, `calculation=2`, to be meaningful). All three
have the same columns. `.metadata` has `ion`, `energy_eV`, `num_events` and
`source`. A file with only headers gives an empty frame; a zero-byte file gives
an empty frame and a warning.

| Column | Unit | Meaning |
|---|---|---|
| `ion` | | number of the ion that caused the event |
| `atomic_number` | | atomic number of the atom that left |
| `energy_eV` | eV | energy when leaving |
| `depth_A` | A | depth (see the note) |
| `y_A`, `z_A` | A | lateral position |
| `cos_x`, `cos_y`, `cos_z` | | direction cosines |

SRIM writes the depth of these files as an integer mantissa with an exponent
(`5020011E-04` is 502.0011 A), and for backscattered ions with a minus sign
separated from the digits (`-  6202433E-06` is -6.202433 A). pysrim2 decodes
both.

## `collision`: the event stream

`COLLISON.txt` (setting `collisions=1` for the ion's own collisions, `2` to add
the recoils of full cascades) records every collision. It can be gigabytes, so
it is never read as a whole. `Collision(directory)` reads only the header;
`iter_events()` streams the file line by line, and `.df` builds the DataFrame
chunk-wise from that stream.

```python
results = Results("test_files/SiC-C")
total = 0
for event in results.collision.iter_events():  # flat dicts, constant memory
    if event["kind"] == "recoil" and event["vacancy"]:
        total += 1
df = results.collision.df  # everything; large files give large frames
```

One row/dict per event, in file order: each ion collision (`kind="ion"`) is
followed by the recoils of its cascade (`kind="recoil"`). Fields that do not
apply to a kind are `None` in the dicts and `NaN` in the DataFrame.

| Column | Unit | `ion` events | `recoil` events |
|---|---|---|---|
| `ion` | | ion number | ion number |
| `kind` | | `"ion"` | `"recoil"` |
| `energy_keV` | keV | ion energy | none |
| `depth_A` | A | depth of the collision | depth of the recoil |
| `y_A`, `z_A` | A | lateral position | lateral position |
| `atom` | | symbol of the atom hit | symbol of the recoil |
| `recoil_energy_eV` | eV | energy given to the atom hit | recoil energy |
| `target_displacements` | | displacements of the whole cascade of this collision | none |
| `target_vacancies` | | vacancies of the cascade | none |
| `target_replacements` | | replacements of the cascade | none |
| `target_interstitials` | | interstitials of the cascade | none |
| `atomic_number` | | none | Z of the recoil |
| `electronic_stopping_eV_per_A` | eV/A | electronic stopping Se | none |
| `vacancy` | | none | 1 if the recoil left a vacancy |
| `replacement` | | none | 1 if it was a replacement |

SRIM prints cascade positions (recoils) with fewer digits than the collision
lines (`2718.E+00` against `27179.E-01`).

`Collision.metadata` holds `source`. Per-ion access also works:
`len(collision)` is the number of ions and `collision[i]` a dict with the
collisions of ion `i` and its totals.

### Skipped lines

SRIM sometimes writes recoil lines that belong to no collision (the cascade
header line was lost) and cascade summaries without a collision. They cannot be
attributed to an ion, so they are skipped, counted, and reported by one warning
per `Collision` after the first complete pass.
`Collision.skipped_lines` is the dict `{"recoil": n, "summary": m}` (counted
over the whole file; the first access streams the file once if needed).

## Implantation summaries

`Range.summary` (parsed from the `RANGE.txt` header) is what SRIM reports.
`srim.analysis.implant_summary(df)` computes the same quantities from the
per-ion positions of `range3d.df` (`depth_A`, `y_A`, `z_A`):

```python
from srim.analysis import implant_summary

implant_summary(results.range3d.df)
# {'n_ions': 996, 'mean_depth_A': ..., 'straggle_depth_A': ..., 'lateral_range_A': ...,
#  'lateral_straggle_A': ..., 'radial_range_A': ..., 'radial_straggle_A': ...,
#  'skewness': ..., 'kurtosis': ...}
```

| Key | Definition |
|---|---|
| `n_ions` | number of ions (rows) |
| `mean_depth_A` | projected range: mean of `depth_A` |
| `straggle_depth_A` | projected straggling: sample standard deviation (`ddof=1`) of `depth_A` |
| `lateral_range_A` | mean of `(|y| + |z|) / 2` |
| `lateral_straggle_A` | sample standard deviation of the signed `y` and `z` values taken together (`ddof=1`) |
| `radial_range_A` | mean of `r = sqrt(y^2 + z^2)` |
| `radial_straggle_A` | sample standard deviation of `r` |
| `skewness`, `kurtosis` | third and fourth standardised moments of `depth_A` (kurtosis is 3 for a Gaussian) |

SRIM does not document how it computes the lateral straggling. The definition
above is inferred; on the reference case it agrees with the `RANGE.txt` header
to within about 1 % (146.4, 241.8 and 154.9 A against SRIM's 145.9, 238.2 and
154.4 A for the depth, lateral and radial straggling), so do not expect exact
equality. The small differences also come from SRIM's 4-digit header and from
ions missing in `RANGE_3D.txt`.

Note that the root-mean-square radial distance (about 342 A for the reference
case) is a different quantity from SRIM's lateral or radial straggling.

## Exporting

Parquet, HDF5 and CSV export of all these tables is described in {doc}`export`.
