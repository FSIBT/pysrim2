1. (SRIM) Ni 5MeV -> Ni 2.5um           Full      1000
2. (SRIM) Au 3MeV -> SiC .8um           Full      1000
3. (SRIM) B .2MeV -> [W, SiO, Si] .3um  Full       381
4. (SRIM) Pb 5MeV -> Ti3SiC2 .2um       KP       20000
5. (SR)   

Derived fixtures (not separate SRIM runs):

- `comma/`: copy of `1/` with `.` replaced by `,` in the numeric fields, to test comma-decimal parsing. Parsing it must give the same arrays as `1/`.
- `SRIM_m/SR_OUTPUT.txt`: copy of `SRIM/SR_OUTPUT.txt` with the 1.20 keV range changed from `29 A` to `1.20 m` (same byte length, CRLF kept), to test the `m` unit.
