# Event-output fixtures (pysrim2 0.8.0)

Generated with real SRIM-2013 (wine, container `ghcr.io/fsibt/pysrim2:main`) by
`generate.py`, from the repository root:

```
docker run --rm --platform linux/amd64 --security-opt seccomp=srim/data/seccomp-docker.json \
  -v "$PWD:/work" -w /work -e PYTHONPATH=/work ghcr.io/fsibt/pysrim2:main \
  python test_files/outputs-0.8/generate.py
```

All runs use `random_seed=12345`. Each folder also holds the run's `TRIM.IN` and `RANGE.txt`.

| folder | run | files |
| --- | --- | --- |
| `exyz/` | 10 He ions, 50 keV, 200 nm Si, quick Kinchin-Pease, `exyz=1000` (eV increment) | `EXYZ.txt` |
| `thin/` | 60 H ions, 15 keV, 100 nm Au, `backscattered=1`, `transmit=1` | `BACKSCAT.txt`, `TRANSMIT.txt` |
| `sputter/` | 20 Ar ions, 2 keV, 50 nm Cu, full cascades (`calculation=2`), `sputtered=1` | `SPUTTER.txt` |

A first attempt with 20 H ions at 100 keV in 50 nm Au produced an empty `BACKSCAT.txt`
(headers only), so the thin target was changed to produce real backscattering.

Quirk of SRIM's event files: the depth column is written as an integer mantissa with an
exponent (`5020011E-04` is 502.0011 A, and backscattered ions have `-  6202433E-06`, a minus
sign separated from the digits, for -6.202433 A). The parser handles both.

## Derived fixtures (not separate SRIM runs)

`comma/` in each folder: copy of the file with `.` replaced by `,` in the numeric fields only
(bytes and CRLF otherwise unchanged), to test comma-decimal parsing. Parsing a comma file must
give the same data as the original.
