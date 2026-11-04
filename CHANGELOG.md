# Changelog

All notable changes to this project are documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Added

- `pysrim2 install [COMPONENT ...]` (with `--dry-run` and `--yes`) and `pysrim2 doctor [--json]` set up and check SRIM.
- `srim.installer.install_srim()` downloads SRIM-2013 from pinned sources and verifies its checksum.
- `srim.installer.wine` sets up a private wine prefix with the VB5 runtime and OCX components for SRIM on Linux; it never touches `~/.wine` or `$WINEPREFIX`.
- `pysrim2 install runtime|image` installs a container runtime (Podman preferred; Docker Desktop is never installed by pysrim2) and pulls the `ghcr.io/fsibt/pysrim2` image; `pysrim2 doctor` checks both.
- Integration tests that run real TRIM/SR (`pytest -m srim`).
- SRIM location defaults to pysrim2's data directory (override with `PYSRIM2_SRIM_DIR`); an existing `/tmp/srim` is still used.
- Container image `ghcr.io/fsibt/pysrim2` (linux/amd64: pysrim2, SRIM-2013 and wine, non-root) built and tested by CI. Run it as its default user (do not pass `-u`).

### Fixed

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
- `TRIM.run` reads its results correctly when `srim_directory` is a relative path.
- SR output ranges in `km` are parsed instead of raising `SRIMOutputParseError`.
- Parsing SRIM output no longer prints debug output to stdout.
- `SR.run` reads its results correctly when `srim_directory` is a relative path.

### Changed

- The source distribution no longer includes `CLAUDE.md`, `docs/superpowers/`, `.github/` or `tests/`.
- The `srim.plot` functions take a `Results` object and read its attributes; passing a dict no longer works.

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

[Unreleased]: https://github.com/FSIBT/pysrim2/compare/v0.6.0...HEAD
[0.6.0]: https://github.com/FSIBT/pysrim2/compare/0421575...v0.6.0
