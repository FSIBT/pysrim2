# Changelog

All notable changes to this project are documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Fixed

- `implant_summary` on an empty frame returns NaN values without emitting RuntimeWarnings.
- `pysrim2 run` no longer leaves an empty output folder behind when the run fails.
- The agent skill names the event-file DataFrames correctly (`results.exyz.df`, not `results.exyz`).

## [0.8.0] - 2026-11-05

All SRIM outputs as pandas DataFrames (incl. per-ion positions, trajectories, backscattered/transmitted/sputtered atoms and full cascades), editable `run.yaml` run specs with `pysrim2 run`, and export to Parquet/HDF5/CSV.

### Added

- CI reports, informationally, the `RANGE_3D` digest of the same spec and seed on Linux (wine), Windows and in the container (the `repro-report` job compares Linux and Windows; the container digest is listed separately); same-platform reruns are asserted identical (`tests/integration/test_reproducibility.py`). Observed: Linux (wine) and the container are bit-identical, native Windows gives the same summary but different per-ion detail (see `docs/run-spec.md`).
- `.df` (a pandas DataFrame, first column `depth_A`, columns named with SRIM units) and `.metadata` on `Ioniz`, `Vacancy`, `NoVacancy`, `EnergyToRecoils`, `Phonons` and `Range`; `Results.to_dataframes(include=None)` returns them as a dict.
- `srim.output.Range3D` reads `RANGE_3D.txt` (per-ion final positions) into a DataFrame with columns `ion`, `depth_A`, `y_A`, `z_A`; `Results.range3d` holds it (`None` if the file is absent) and `Results.to_dataframes()` includes it.
- `Range.summary` returns the range and straggling values SRIM prints in the `RANGE.txt` header (depth, lateral, radial, skewness, kurtosis). `srim.analysis.implant_summary(df)` computes the same quantities from the per-ion positions of `Range3D` (standard deviations with `ddof=1`).
- `srim.output.EXYZ`, `Backscat`, `Transmit` and `Sputter` read `EXYZ.txt`, `BACKSCAT.txt`, `TRANSMIT.txt` and `SPUTTER.txt` into DataFrames (`ion`, `atomic_number`, `energy_eV`, `depth_A`, `y_A`, `z_A`, `cos_x`, `cos_y`, `cos_z`; EXYZ: `ion`, `energy_keV`, `depth_A`, `y_A`, `z_A`, `electronic_stopping_eV_per_A`, `last_recoil_energy_eV`); `Results.exyz`, `.backscat`, `.transmit`, `.sputter` hold them (`None` if absent; parsed on first access, streaming, so large files are cheap to open) and `to_dataframes()` returns them. A zero-byte event file gives an empty frame and a warning. They replace the stubs. EXYZ parser based on work by philis-johan. Fixtures generated with real SRIM in `test_files/outputs-0.8/`. `EXYZ.txt` is part of the output files that a run removes beforehand when stale and that `TRIM.copy_output_files` copies.

- `Collision.iter_events()` streams `COLLISON.txt` line by line as flat event dicts (`ion`, `kind` = `ion` for a collision of the primary ion or `recoil` for a cascade recoil, `energy_keV`, `depth_A`, `y_A`, `z_A`, `atom`, `recoil_energy_eV`, `target_displacements`, `target_vacancies`, `target_replacements`, `target_interstitials`, plus `atomic_number`, `electronic_stopping_eV_per_A`, `vacancy`, `replacement`); `Collision.df` collects them into a DataFrame chunk-wise. `Results.collision` (lazy, `None` if absent) and `Results.to_dataframes(include=["collision"])` expose them; `to_dataframes()` leaves `collision` out by default (opt-in like the exports, since `COLLISON.txt` can be several GB).

- `Results.to_parquet(directory)`, `.to_hdf5(path)` and `.to_csv(directory)` export the tables (optionally selected with `include=`; `collision` only on request) with metadata: the table's `.metadata`, pysrim2 version, export time and an optional run `spec` (`source` is the file name only; Parquet: schema key `pysrim2`; HDF5: node attribute `pysrim2`; CSV: `metadata.yaml`). `Collision.to_parquet(path, chunk_rows=100_000)` (also a `chunk_rows` argument of the `Results.to_*` methods) and `Collision.to_csv` stream the cascade events in chunks, so memory stays small for huge `COLLISON.txt` files. New optional extras `pysrim2[parquet]` (pyarrow), `pysrim2[hdf5]` (PyTables) and `pysrim2[all]` (both); `Collision.metadata`. The `spec` argument of the exports accepts a `RunSpec` (stored as its mapping) or a dict; anything else raises `TypeError`.

- `run.yaml`: `srim.spec.RunSpec` (and `IonSpec`, `LayerSpec`, `ElementSpec`) describes a run with units in the key names (`energy_keV`, `thickness_nm`, `density_g_cm3`, `angle_deg`, ...), validates it with errors naming the dotted key (`SpecError`), and round-trips to a byte-identical `TRIM.IN`. `TRIM.to_spec()`, `TRIM.from_spec()`, `TRIM.to_yaml()`, `TRIM.from_yaml()`; the seed is always recorded. `TRIM.run()` writes `run.yaml` with provenance (pysrim2 version, SRIM SHA-256, platform, backend, start/end time, host) next to the outputs (`write_spec=False` to skip), `TRIM.copy_output_files` copies it, and `Results.spec` is the default `spec` of the `to_parquet`/`to_hdf5`/`to_csv` exports (no host name). See `docs/run-spec.md`.

- Documentation: new pages `docs/dataframes.md` (every column, `Range.summary`, `implant_summary`, `iter_events`), `docs/export.md`, and a full schema reference in `docs/run-spec.md`; the README shows `.df` and `implant_summary`.

- `pysrim2 run run.yaml [--set dotted.key=value ...] [--output DIR]` runs TRIM from a run spec (values converted by the schema; outputs and `run.yaml` copied to `DIR`, default `./srim-output-<UTC timestamp>`), and `pysrim2 import TRIM.IN [-o run.yaml]` turns an existing `TRIM.IN` into a run spec. Exit codes in `docs/cli.md`.

- Example notebooks rewritten on the 0.8.0 API (default SRIM location, `.df` tables, `implant_summary`, `Collision.iter_events()` / `.df`, Parquet export, `run.yaml` with `TRIM.from_spec` and `pysrim2 run`), with small ion counts and no committed outputs; `Regular SRIM Calculation.ipynb` is now `Regular_SRIM_Calculation.ipynb`, and `KTaO3.ipynb` is merged into `SiC.ipynb`. A `srim`-marked test (`tests/integration/test_run_notebooks.py`, nbclient) executes them; a static test checks that they are valid and import existing names.
- `examples/scripts/run_yaml_example/` (a `run.yaml` and README for `pysrim2 run run.yaml --set ion.energy_keV=30`); the agent skill and `examples/agents/README.md` teach `.df`, `implant_summary`, event tables, cascades with `iter_events()` and chunked export, and `run.yaml`.

### Changed

- A `Layer` without a name is now named after its elements (`Si+C`, or the formula for `Layer.from_formula("SiC", ...)`; the same default for a layer without `name:` in `run.yaml`) instead of the text `None`. This changes the layer name written to `TRIM.IN` (and `run.yaml`) for unnamed layers compared with 0.7.0.

### Fixed

- The `.df` element columns of Kinchin-Pease tables (`vacancies_<element>_...`, `absorbed_<element>_...`) are named after the element for single-element targets instead of `1`; the header of those tables lists no symbols, so they are read from the target section. Multi-element targets keep the numbers.

- A zero-byte `RANGE_3D.txt` gives an empty `Range3D` frame (same columns) and a warning, like the event files, instead of raising. `Range3D` also warns when a line in the data section cannot be parsed (a truncated final line is still ignored silently).
- The reference example's "lateral straggle" (341.6 A for the boron case) was the root mean square of the radial distance, not what SRIM calls lateral straggling; the example and skill now report SRIM's definitions (lateral straggle 241.8 A, radial straggle 154.9 A).
- `Results.range3d` also finds `RANGE_3D.txt` in the `SRIM Outputs` subfolder.
- A table header whose element names cannot be read now warns before the element columns fall back to `1`, `2`, ...; the `Vacancy.knock_ons` docs give the correct unit (knock-ons per Angstrom per ion).
- `Collision` (`COLLISON.txt`) now parses every ion: the file is decoded as cp437 and split on SRIM's box-drawing column separators, so numbers no longer fail with glued separator characters (508 of 1000 ions of the SiC-C fixture raised `ValueError`/`IndexError`). Cascades without a summary line and stray recoil lines are handled; the per-ion dict API is unchanged.
- `pysrim2 install` exits 130 with a `[skip] ... cancelled` line on Ctrl-C during a step instead of printing a traceback.

## [0.7.0] - 2026-11-04

pysrim2 can now set up SRIM itself: `uv tool install pysrim2 && pysrim2 install` on Windows and Linux, or the `ghcr.io/fsibt/pysrim2` container (Podman/Docker).

### Added

- Documentation for the installer, the container, the command line and the agent skill (`docs/installation.rst`, `container.md`, `cli.md`, `agents.md`); the README and `AGENTS.md` describe the 0.7.0 setup. Example scripts for the current API in `examples/scripts/` (reference case, multilayer damage profile, stopping table) and an agent example in `examples/agents/`.
- `pysrim2 install [COMPONENT ...]` (with `--dry-run` and `--yes`) and `pysrim2 doctor [--json]` set up and check SRIM.
- `srim.installer.install_srim()` downloads SRIM-2013 from pinned sources and verifies its checksum.
- `srim.installer.wine` sets up a private wine prefix with the VB5 runtime and OCX components for SRIM on Linux; it never touches `~/.wine` or `$WINEPREFIX`.
- `pysrim2 install runtime|image` installs a container runtime (Podman preferred; Docker Desktop is never installed by pysrim2) and pulls the `ghcr.io/fsibt/pysrim2` image; `pysrim2 doctor` checks both.
- Integration tests that run real TRIM/SR (`pytest -m srim`).
- SRIM location defaults to pysrim2's data directory (override with `PYSRIM2_SRIM_DIR`); an existing `/tmp/srim` is still used.
- Container image `ghcr.io/fsibt/pysrim2` (linux/amd64: pysrim2, SRIM-2013 and wine, non-root) built and tested by CI. Run it as its default user (do not pass `-u`).
- Agent skill `pysrim2` (`skills/pysrim2/SKILL.md` with a runnable example, shipped inside the wheel): `pysrim2 install skill [--agent claude|all] [--scope user|project]` copies it to `~/.claude/skills/`, `<project>/.claude/skills/` (and `~/.agents/skills/` for `--agent all`); it never overwrites a differing skill it did not write unless `--yes`. `skill` is part of the default `pysrim2 install` set and `pysrim2 doctor` checks an installed copy is current.

### Changed

- Linux: pysrim2 always runs SRIM in its own private wine prefix. If you used pysrim with `/tmp/srim` and `~/.wine`, run `pysrim2 install wine` once (an existing `/tmp/srim` is still found).
- The source distribution no longer includes `CLAUDE.md`, `docs/superpowers/`, `.github/` or `tests/`.
- The `srim.plot` functions take a `Results` object and read its attributes; passing a dict no longer works.

### Fixed

- `TRIM.run()`/`SR.run()` no longer print Mesa/EGL ("MESA-EGL", "pci id for fd") warnings; xvfb/EGL noise is dropped from both stdout and stderr and EGL/Mesa logging is turned down in wine's environment.
- `pysrim2 install runtime` shows the Windows "open a new terminal" hint only when no Podman machine exists; `pysrim2 install --dry-run` no longer names podman in the pull command when no runtime is installed.
- Wine setup always runs its own pinned, checksum-verified winetricks instead of one found on `PATH` (old copies do not know the OCX verbs).
- `pysrim2 install wine` no longer hangs when a wine driver host crashes (openSUSE, wine 11): the USB/controller/Bluetooth drivers and Mono/Gecko prompts are disabled through `WINEDLLOVERRIDES`, wine's crash debugger is emptied, and a wineserver that does not exit within 60 s is stopped.
- `pysrim2 install wine` no longer hangs after `wineboot` finishes: wine output is captured in a temporary file, so the background `wineserver` cannot keep a pipe open (same for `TRIM.run()`/`SR.run()` stderr).
- `TRIM.run()` and `SR.run()` no longer print xvfb's "X connection to :N broken" line when SRIM exits under wine; other stderr output is kept and shown after the run. A failing run raises `srim.srim.SRIMProcessError` (a `subprocess.CalledProcessError`) whose message includes that stderr.
- `pysrim2 install` prints `[ok] <step>: installed` (with the SRIM directory for `srim`) after each step succeeds.
- A failed or interrupted (Ctrl-C) `pysrim2 install wine` stops the private prefix's wineserver instead of leaving wine processes running.
- `pysrim2 install` prints a line as each long sub-step starts (wine prefix, winetricks, VB5 runtime, SRIM download/verify/unpack) instead of staying silent for minutes.
- `pysrim2 doctor` and `pysrim2 install` no longer suggest "pysrim2 install wine" on distributions without known package commands; they point to the WineHQ download page instead.
- `pysrim2 install wine` knows the package commands for openSUSE (zypper) and Arch (pacman, untested).
- SRIM and the wine setup run under a 1024x768x24 virtual screen; the default 8-bit `xvfb-run` screen made `wineboot` hang on wine 11.
- Container image: Docker 29's built-in seccomp profile makes 32-bit wine fail ("socket: Function not implemented"); pysrim2 ships Docker's current default profile as `srim/data/seccomp-docker.json` (`pysrim2 install image` copies it to the data directory and prints the `docker run --security-opt seccomp=...` line). Podman is the default and needs no profile (its hint uses `:Z` on the `/data` mount).
- `pysrim2 install vb5` (Windows) puts the Visual Basic 5 runtime and OCX controls next to SRIM so TRIM starts on native Windows.
- Wine setup finds `wineserver` where Ubuntu installs it.
- `TRIM.run()` and `SR.run()` delete old output first and raise `SRIMRunError` if SRIM wrote no result, so a crash never returns a previous run's data.
- Winetricks downloads go to pysrim2's own cache instead of `~/.cache/winetricks`.
- `TRIM.run` and `SR.run` read their results correctly when `srim_directory` is a relative path.
- SR output ranges in `km` are parsed instead of raising `SRIMOutputParseError`.
- Parsing SRIM output no longer prints debug output to stdout.

### Removed

- The obsolete `costrouc/pysrim` Docker example (`examples/docker/`), the historical Docker benchmark page (`docs/benchmarks.rst`), `examples/input/` and unused generated files in `examples/images/`.

## [0.6.0] - 2026-11-03

Changes since pysrim 0.5.10. See
[changes from pysrim](https://pysrim2.readthedocs.io/en/latest/changes-from-pysrim.html) for credits and details.

### Added

- Run SRIM under `xvfb-run -a` when it is available, so no display is needed (Jan Mayer).
- Parse SRIM output written with comma decimal separators (philis-johan, MrRobot2211).
- Support the `m` (metre) length unit in SR output (Angus Hollands).
- `SRIMOutputParseError` for unknown units in SR output.
- GitHub Actions: lint, test matrix (Python 3.11 to 3.15 on Linux, Windows and macOS), build, and a trusted-publishing release workflow.
- Ruff configuration and pre-commit hooks.
- Documentation: change list relative to pysrim, changelog page, `AGENTS.md`.

### Changed

- The distribution is renamed to `pysrim2`; the import name `srim` is unchanged. It cannot be installed alongside `pysrim`.
- Requires Python 3.11 or newer.
- Packaging uses `pyproject.toml`, hatchling and uv (Angus Hollands).
- Wine is detected with `shutil.which` through a shared launch helper (Mirochill).
- Code formatted with ruff.
- Documentation uses the furo theme, is built with uv, and is hosted at <https://pysrim2.readthedocs.io>.
- README and installation instructions rewritten for pysrim2.

### Fixed

- Removed the `distutils` import so the package imports on Python 3.12 and later (lievcm).
- Regular expressions use raw strings, removing invalid escape sequence warnings (lievcm).
- Ion energies with a leading or trailing decimal point (such as `.3 keV`) are parsed (Mirochill, George Apostolopoulos, SimonF24).
- Positive `autosave` intervals are accepted (Mirochill).
- The sputtered output setting is honoured independently of ranges (Mirochill).
- The element database file is closed after loading.
- Undefined names: the `NotImpementedError` typo in `SRIM_Output._read_target`, and missing `numpy` import and `folder` argument in `srim.plot`.
- Plot functions read `Results` attributes instead of subscripting `Results`.
- Docstring markup errors that produced Sphinx warnings.

### Removed

- Support for Python versions before 3.11.
- `setup.py`, `setup.cfg`, the conda recipe, `install.sh`, `default.nix`, GitLab CI and the old Dockerfiles. A container image and a SRIM installer for wine are planned.

Versions up to and including 0.5.10 were released as `pysrim` by Chris Ostrouchov.

[Unreleased]: https://github.com/FSIBT/pysrim2/compare/v0.8.0...HEAD
[0.8.0]: https://github.com/FSIBT/pysrim2/compare/v0.7.0...v0.8.0
[0.7.0]: https://github.com/FSIBT/pysrim2/compare/v0.6.0...v0.7.0
[0.6.0]: https://github.com/FSIBT/pysrim2/compare/0421575...v0.6.0
