# Changes from pysrim

pysrim2 is a maintained continuation of
[pysrim](https://gitlab.com/costrouc/pysrim) by Chris Ostrouchov. This page
lists what was taken from the many forks of pysrim, who wrote it, and how it
landed here. Commits that were cherry-picked keep their original author and
message; the `(cherry picked from commit ...)` line in the commit message
names the fork commit.

## Integrated changes

| Topic | Original author(s) | Source (fork + commit) | How it landed |
|---|---|---|---|
| Remove the deprecated `distutils` import (needed for Python 3.12+) | lievcm | [lievcm/pysrim `368157d`](https://github.com/lievcm/pysrim/commit/368157d) | Cherry-picked |
| Detect wine with `shutil.which` through one shared launch helper used by `TRIM.run` and `SR.run` | Mirochill | [Mirochill/pysrim `91d2c1a`](https://github.com/Mirochill/pysrim/commit/91d2c1a) | Reimplemented with credit (on top of the `distutils` removal) |
| Run SRIM under `xvfb-run -a` when available, so no display is needed | Jan Mayer | [janmayer/pysrim `0dd019e`](https://github.com/janmayer/pysrim/commit/0dd019e) | Reimplemented with credit (original relied on `distutils`) |
| Safe YAML loading of the element database (no `yaml.load` warning) | Arun Persaud (upstream `73205cf`); also lievcm, George Apostolopoulos, Allentro, Peter Sun | [lievcm `5ebf984`](https://github.com/lievcm/pysrim/commit/5ebf984), [gapost `12a9c06`](https://github.com/gapost/pysrim/commit/12a9c06), [Allentro `0cabff1`](https://github.com/Allentro/pysrim/commit/0cabff1), [peterhys `cb4c90d`](https://github.com/peterhys/pysrim/commit/cb4c90d) | Already present; regression test added, forks credited as co-authors |
| Raw strings for regular expressions (fixes invalid escape warnings) | lievcm | [lievcm/pysrim `53e5bd2`](https://github.com/lievcm/pysrim/commit/53e5bd2) | Cherry-picked; remaining literals fixed and tested in a follow-up |
| Parse ion energies with a leading decimal point (`.3 keV`) | Mirochill; also George Apostolopoulos (`fa239a1`), SimonF24 | [Mirochill/pysrim `67d32f4`](https://github.com/Mirochill/pysrim/commit/67d32f4) | Cherry-picked; parametrised tests added, others credited |
| Parse SRIM output written with comma decimal separators (e.g. `1,234E+02`) | philis-johan, MrRobot2211 (locale-based proposals) | philis-johan (GitHub jphilis fork) and MrRobot2211 ([felipebihaiek/pysrim `ed61a8f`](https://gitlab.com/felipebihaiek/pysrim/-/commit/ed61a8f)) | Reimplemented with credit (explicit `_to_float` instead of changing the process locale) |
| Support the `m` (metre) length unit in SR output | Angus Hollands | [agoose77/pysrim `f67b0c2`](https://github.com/agoose77/pysrim/commit/f67b0c2) | Cherry-picked; unknown units now raise `SRIMOutputParseError` in a follow-up |
| Allow positive `autosave` intervals | Mirochill | [Mirochill/pysrim `cce778b`](https://github.com/Mirochill/pysrim/tree/fix-10-autosave-positive) | Cherry-picked |
| Honour the sputtered-output setting independently of ranges | Mirochill | [Mirochill/pysrim `eb8263d`](https://github.com/Mirochill/pysrim/tree/fix-3-sputtered-setting) | Cherry-picked |
| Close the element database file after loading | Arun Persaud | pysrim2 | New in pysrim2 (found while testing the YAML change) |
| Fix undefined names: `NotImpementedError` typo in `SRIM_Output._read_target`, missing `numpy` import and `folder` argument in `srim.plot` | Arun Persaud | pysrim2 | New in pysrim2 (found by ruff) |
| Plot functions read `Results` attributes instead of subscripting it | Arun Persaud | pysrim2 | New in pysrim2; smoke tests added |
| Packaging with hatchling and uv, `pyproject.toml` | Angus Hollands (earlier hatchling setup); Arun Persaud | [agoose77/pysrim `main`](https://github.com/agoose77/pysrim) | Reimplemented with credit (`Co-authored-by`) |

## Reviewed but deferred

| Topic | Author | Reason |
|---|---|---|
| EXYZ parser | philis-johan | Planned for a later release with the new data model. |
| Parallel runs, MMonCa and COLLISON merge | FilipovicLado | Larger design change; kept as a reference for the planned run API. |
| HDF5 store | elmitsi | To be superseded by Parquet/HDF5 export in a later release. |
| Simple run script | jesse.carter | Superseded by the planned run API. |

## Dropped

- Upstream commits that only added Travis CI configuration, a Git LFS asset or
  GitHub URLs for the project: not applicable to this repository.
- Angus Hollands' (agoose77) earlier hatchling and CI setup: thank you. It is
  superseded by the packaging and GitHub Actions workflows here, and he is
  credited as co-author on the packaging commit.

## Breaking changes

- Python 3.11 or newer is required.
- The distribution is renamed to `pysrim2`. The import name `srim` is
  unchanged, so `pysrim2` cannot be installed alongside `pysrim`.
- The conda package, Docker images, `install.sh` and Nix files are gone. A
  container image and a SRIM installer for wine are planned.

## Migration

```bash
pip uninstall pysrim && pip install pysrim2
# or
uv add pysrim2
```

Imports (`from srim import TRIM, Ion, Layer, Target`) are unchanged.
