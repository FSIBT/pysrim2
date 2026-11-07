# TRIM.DAT spike (SRIM-2013, verified under wine in ghcr.io/fsibt/pysrim2:main)

## Verdict: works. TRIM honours per-ion energy, start position and direction from TRIM.DAT.

## Switch
TRIM.IN line 5 (the "Cascades" field, first value): 4 = TRIM.DAT ions with quick Kinchin-Pease
damage, 5 = TRIM.DAT ions with full cascades (pysrim `calculation=4|5`). Values 6/7 = TRIM.DAT
used as recoil (neutron/electron/photon) source, not wanted here. TRIMAUTO=1 batch mode works
unchanged. Source: SRIM docs `SRIMHelp/helpTR18.rtf`, `SRIM Tools/TRIMdat HELP.rtf`,
`HELP - TRIM Input.pdf` (Table 8-1, p. 8-14..8-16); confirmed by run (header says
"Kinchin-Pease", per-ion depths follow file).

## File: TRIM.DAT in the SRIM root (same dir as TRIM.exe, i.e. the run dir copy)
- 10 comment lines. Line 8 is copied into all TRIM output files as the run description.
  Lines 9-10 are the column headers (still comments).
- Then one line per ion, fields separated by spaces or commas. The name is exactly 5 characters:
  SRIM reads the first 5 characters of the line as the name (found in 0.10.0: `I001 5 ...` with one
  space ran into the Z field and TRIM stopped with a division-by-zero dialog).
  `Name(5 chars)  Z  Energy_eV  X_depth_A  Y_A  Z_A  cosX  cosY  cosZ`
  e.g. `I001   5   40000   0   0   0   0.5  0.866025  0.0`
- Any numeric style OK (1.31E4, .123, -.0). cosX > 0 = into target, X is depth (Ang, must be > 0
  or 0 and < target depth). Directions: cosines of the velocity, x = depth axis. Direction cosines
  should be normalised (we wrote cos/sin of the angle; sum of squares = 1).
- Lines end CR+LF; file should end with ASCII 0x1A (EOF); manual says missing EOF gives Error #62.
  (Our verified files had CRLF and a trailing 0x1A.)
- Output files TRANSMIT/BACKSCAT/SPUTTER.txt have the same column layout (can be chained).
- Start position is jittered randomly within an atomic diameter even for (0,0,0).

## Verified evidence (script: spike2.py, 120 B ions into 5000 A Si, 3 energies x 2 angles)
Setup: TRIM.IN ion B 40 keV (= max energy in file), Number=120, Cascades=4, Ranges=1;
TRIM.DAT lines 1-60 normal incidence, 61-120 at 60 deg to the normal (cosX=0.5), energies cycling
10/20/40 keV by ion index. RANGE_3D.txt final depths (A), mean/std per group:

| angle | E (keV) | n listed | mean depth | std |
|-------|---------|----------|-----------|-----|
| 0  | 10 | 19 | 525  | 230 |
| 0  | 20 | 18 | 719  | 301 |
| 0  | 40 | 20 | 1461 | 494 |
| 60 | 10 | 14 | 323  | 168 |
| 60 | 20 | 18 | 601  | 286 |
| 60 | 40 | 16 | 899  | 445 |

Depth grows with per-ion energy and shrinks at the tilted angle; if the header energy (40 keV) were
used for all ions all rows would be ~1460. Ion index n in RANGE_3D.txt = line n of the data table.
A 20-ion pre-run showed the same behaviour. Raw outputs: out/, out2/ (RANGE_3D.txt, TDATA.txt).

## Limitations / notes
- Max 99999 data lines (manual). Number of ions in TRIM.IN should equal the number of data lines
  (docs: run ends after the last line; TRIM.IN count drives the loop).
- TRIM.IN ion energy must be the MAXIMUM energy in the file; target depth must exceed the max start
  depth (an ion starting deeper than the target raises an error dialog; with xvfb that would hang or
  skip the ion - validate beforehand). Energy in eV in the file, keV in TRIM.IN.
- TRIM.IN "Angle" field is ignored (per-ion cosines used); the ion's name/Z/mass in TRIM.IN define
  the stopping tables. Header "Ion Angle to Surface = 0" in outputs is therefore misleading.
- Species: docs say every Z used in TRIM.DAT must be set up in TRIM.IN so stopping powers exist;
  pysrim2 TRIM has one Ion, so mixed species is NOT supported by the pysrim model and was not
  tested. Plan: single-species only (Z in column 2 = ion Z; mass taken from TRIM.IN).
- RANGE_3D.txt omits ions (backscattered/transmitted; 105 of 120 listed here), as already known.
  Use ion number column (not row index) to join to input.
- Precision: free-form decimal text; 6 significant digits for cosines is plenty.
- Ions are processed in file order; random seed field still applies for the physics.

## Recommendation
Implement `Source`/incident distribution sampling in pysrim2 by writing TRIM.DAT into the run dir
and setting calculation 4/5 (reject 1-3). Writer: CRLF, 10 header lines, row per ion
(a 5-character name such as `f"I{n%10000:04d}"`), terminate with `\x1a`, set TRIM.IN energy = max(E)/1000 keV,
number_ions = N, assert normalised cosines and X < layer stack depth, single Z. Add a
direction-normalisation check and a test using the depth-vs-energy ordering above.
