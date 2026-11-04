# Changelog

All notable changes to this project are documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Added

- `.df` (a pandas DataFrame, first column `depth_A`, columns named with SRIM units) and `.metadata` on `Ioniz`, `Vacancy`, `NoVacancy`, `EnergyToRecoils`, `Phonons` and `Range`; `Results.to_dataframes()` returns them as a dict.
- `srim.output.Range3D` reads `RANGE_3D.txt` (per-ion final positions) into a DataFrame with columns `ion`, `depth_A`, `y_A`, `z_A`; `Results.range3d` holds it (`None` if the file is absent) and `Results.to_dataframes()` includes it.
- `srim.analysis.implant_summary(df)` returns `n_ions`, mean and sample-std (`ddof=1`) depth, mean lateral distance and RMS lateral straggle of per-ion positions.

### Fixed

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

[Unreleased]: https://github.com/FSIBT/pysrim2/compare/v0.7.0...HEAD
[0.7.0]: https://github.com/FSIBT/pysrim2/compare/v0.6.0...v0.7.0
[0.6.0]: https://github.com/FSIBT/pysrim2/compare/0421575...v0.6.0
