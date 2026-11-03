# pysrim2 — revival and modernization of pysrim

Status: draft for review · Author: Arun Persaud (with Claude) · 2026-11-03

## Why

`pysrim` (Chris Ostrouchov, MIT) is the standard Python wrapper for running
SRIM/TRIM and parsing its output, but it is unmaintained: the GitHub repo is
archived (2022), the GitLab original (gitlab.com/costrouc/pysrim) has open
MRs that never got merged, the last PyPI release is 0.5.10, and the packaging,
Docker images and CI target Python 2.6–3.7 / Debian buster. Repeated attempts
to get PyPI maintainer access were not acted on. ~40 forks each carry the same
handful of fixes. Our group (FSIBT) depends on it and wants a maintained,
modern version that is also easy for AI agents to drive.

## Goals

1. A maintained package **`pysrim2`** on PyPI, docs on Read the Docs as
   `pysrim2`, code at **github.com/FSIBT/pysrim2**, default branch `main`.
2. Consolidate the worthwhile fixes from upstream MRs and forks, with
   original authorship preserved.
3. Modern tooling: `pyproject.toml`, **uv** for env/build/test (locally, in
   CI and in the container); Python **3.11–3.15**.
4. A container image on **ghcr.io** in which *everything* runs (Python +
   wine + SRIM), with a mounted volume for inputs/outputs.
5. New high-level features:
   - results as **pandas DataFrames** (per-ion final positions, depth profiles);
   - **parameter scans** (energy, angle, ion, layer thickness, …) executed as
     **parallel** TRIM runs, returning one tidy DataFrame;
   - **full cascade export** (COLLISON.txt) to an easily readable format;
   - **easy local use without containers**: native Windows, Linux + wine
     (and macOS + wine if feasible), including parallel runs.
6. **Agent support**: a `SKILL.md` teaching agents to use pysrim2, and an
   `AGENTS.md` (plus `CLAUDE.md` pointing to it) for agents developing it.
7. Good documentation, updated from the original and extended for the
   container workflow, scans, and agent use.
8. GitHub Actions automating tests, container builds and PyPI releases.

## Non-goals

- Re-implementing SRIM physics or replacing SRIM.
- conda packages, Docker Hub images, Python < 3.11, arm64-native images
  (SRIM needs 32-bit wine; arm hosts use amd64 emulation).
- Merging the FilipovicLado parallel fork wholesale.

## Anti-goals (would count as failure)

- Losing contributor attribution, or dropping Chris's MIT copyright notice.
- Shipping SRIM without Ziegler's copyright/permission notice, or anything
  that implies commercial use or endorsement by Ziegler/SRIM.org.
- Breaking `import srim` for existing scripts without a clear changelog entry.
- Large monolithic commits; unreviewable history.
- Committing SRIM binaries or large output files to git.

## Constraints

### Licensing
- pysrim is MIT and pysrim2 stays MIT as a derived work: keep
  `Copyright (c) 2018 Chris Ostrouchov` and add a line for the pysrim2
  contributors. No institutional sign-off needed for now (decided).
- SRIM (© James F. Ziegler) may be copied and distributed for
  **non-commercial purposes without fee**, provided the copyright and
  permission notice accompanies every copy; Ziegler's name may not be used
  in advertising; "SRIM" may not be used commercially. Therefore:
  the public image is free, includes the notice (e.g.
  `/opt/srim/SRIM-LICENSE.txt` and an OCI label), README/docs state the
  non-commercial restriction, and SRIM is **downloaded at image build time
  from srim.org with a pinned SHA-256** — never stored in git.
- No "lbl"/institutional branding in names.

### Naming
- Distribution name `pysrim2`; **import name stays `srim`** (drop-in for
  existing code). Docs note that `pysrim` and `pysrim2` cannot be installed
  together.

### Process
- Small, logical commits (one fix / one concern each); fork fixes are
  cherry-picked or re-applied with `git commit --author=` /
  `Co-authored-by:` to preserve credit.
- `CHANGELOG.md` in Keep a Changelog format; Semantic Versioning.
- Each bug fix gets a regression test using fixtures in `test_files/`
  where feasible.

## Decisions made

### Repository
- Start from the GitLab original's full history (local clone), plus the
  4 GitHub-only upstream commits only if useful (they are Travis/GitHub URL
  changes — likely dropped).
- Delete the existing FSIBT/pysrim fork (it has no unique content) and create
  FSIBT/pysrim2 as a standalone repo. **Deletion requires explicit
  confirmation at the time it is done.**
- Rename `master` → `main`.

### Fixes to integrate ("easy fixes" — all of them)
Pick one implementation where several forks fixed the same thing:

| Topic | Sources |
|---|---|
| `yaml.load` Loader / `safe_load` | lievcm, gapost, Allentro, peterhys |
| ion-energy "double" regex (E<1 keV, leading decimal like `.3`) | GitLab MR !6 (gapost), ir2-lab, SimonF24 (in ericpre fork), Mirochill |
| locale / "," decimal parsing | GitLab MR !5 (felipebihaiek), jphilis "european fixes" |
| "m" (milli) unit prefix | GitLab MR !3 / agoose77 |
| remove `distutils` | lievcm |
| raw-string regexes | lievcm |
| prefer `xvfb-run` | janmayer |
| `shutil.which` for wine detection | Mirochill |
| allow positive autosave interval | Mirochill |
| honour sputtered setting independent of ranges | Mirochill |
| example notebook fixes | already in history (agoose77) |

Reviewed but not merged as-is: FilipovicLado (parallel runs, MMonCa,
COLLISON merge — used as reference for our own implementation), elmitsi
(HDF5 store — superseded by Parquet/DataFrame export), jesse.carter
(simple run script — superseded by the new high-level API).

### Packaging & tooling
- `pyproject.toml` with hatchling backend (as in agoose77's fork); `uv`
  for lock, sync, build, test; `uv.lock` committed.
- Dependencies: numpy, pyyaml, **pandas**; optional extras `[parquet]`
  (pyarrow), `[hdf5]` (pytables) and `[plot]` (matplotlib). Dev group:
  pytest, pytest-mock, pytest-cov, **ruff** (lint + format, enforced in CI).
- Local install: `uv tool install pysrim2` (or `uv add pysrim2` in a
  project), then **`pysrim2 install`**, which detects the OS and what is
  present (Windows / Linux+wine / container runtime / agents) and sets up
  what is missing:
  - SRIM: download from our GitHub Release asset (srim.org as fallback),
    verify SHA-256, unpack (incl. Ziegler's LEGAL notice);
  - Linux: wine prefix + VB5 runtime + OCX components; Windows: nothing
    beyond SRIM; macOS: container route only (see spike results);
  - optionally pull the container image;
  - agent skill into the right locations (see Agent support).
  `--dry-run` prints every step without doing it; it can be re-run later
  and take components (`pysrim2 install skill|srim|image|runtime`).
  Installing a container runtime itself (Podman preferred) prints the
  exact OS commands and runs them only after confirmation (needs
  sudo/brew). `pysrim2 doctor` reports status. SRIM is never shipped
  inside the PyPI package.
- Remove: `setup.py`, `setup.cfg`, `.conda/`, `install.sh` (logic moves to
  the Dockerfile), `.gitlab-ci.yml`, old Dockerfiles, `pysrim.egg-info`,
  committed `__pycache__`, LFS config/asset pointer, `default.nix` (no Nix
  support for now — decided).

### Container
- Single image `ghcr.io/fsibt/pysrim2`, `linux/amd64`, Debian trixie base;
  wine32 + winetricks VB runtime components + xvfb; SRIM-2013 fetched and
  checksum-verified at build; Python 3.15 installed by uv; pysrim2 installed
  with uv into a venv. Spike (2026-11-03) proved this works on Apple
  silicon under QEMU emulation (10 Ni ions in 11 s) with these specifics:
  Debian's wine 10.0 + `winetricks vb5run` is broken (wine bug 56209,
  needs ≥ 10.18), so `MSVBVM50.DLL`/`COMCAT.DLL` are extracted from
  Microsoft's `msvbvm50.exe` with 7z into system32; winetricks is not in
  Debian main (fetch pinned upstream release); WineHQ's wine 11 crashed
  under QEMU. Non-root user in the image.
- Usage contract: user mounts a host directory at `/data`; scripts and
  inputs are read from there, outputs (raw SRIM files, Parquet/CSV) written
  there. E.g. `docker run --rm -v "$PWD:/data" ghcr.io/fsibt/pysrim2 python /data/run.py`.
- The container's default SRIM directory is configured via env var so
  `TRIM(...).run()` works with no arguments inside it.
- Tags: `vX.Y.Z`, `X.Y`, `latest` on release; `main` on pushes to main.

### New API (shape, not final signatures)
- `results.to_dataframe()` / per-output `.df` properties:
  - `Range3D` → one row per ion: `ion, x, y, z` (depth = x, lateral y/z),
    enabling mean depth and xy straggle directly.
  - depth profiles (ionization, vacancy, range, phonons, E2recoil) → rows
    per depth bin with named, unit-suffixed columns.
- **Run spec file (`run.yaml`)**: every run writes a human-editable YAML
  next to its TRIM.IN and outputs, and the same file can drive a run:
  - `spec:` section, everything needed to regenerate TRIM.IN: schema
    version; ion (element, mass, energy, angle), number of ions, calc
    mode, **seed**, all TRIM settings; target layers (name, thickness,
    density, elements with stoichiometry and displacement/lattice/surface
    energies, phase).
  - `provenance:` section, informational only: pysrim2 version, SRIM
    version + SHA-256, platform/backend, start/end time, host.
  - Round-trip API: `TRIM.from_yaml(path)` / `trim.to_yaml(path)`; CLI
    `pysrim2 run run.yaml [--set ion.energy_keV=30 ...]` to rerun with
    changes; `pysrim2 import TRIM.IN` turns legacy inputs into a spec.
  - Explicit units in key names (`energy_keV`, `thickness_nm`,
    `density_g_cm3`, `angle_deg`) so the file is unambiguous.
  - If no seed is given, pysrim2 picks one and records it, so every run
    is reproducible. An integration test checks that the same spec + seed
    gives identical output.
  - The cache key for scans is the hash of the `spec:` section, and the
    same spec is embedded in Parquet/HDF5 metadata.
  - Scans: the Python API is primary; a scan YAML (a base spec + a
    `scan:` grid) is supported because it reuses the same schema, and
    `Scan.to_yaml()` records any Python-defined scan for reruns. Each scan
    point's job directory gets its own complete `run.yaml`.
- Simple-target convenience: building a layered target (e.g. 100 nm Au on
  Si substrate) should be a few lines; agents should not need to know TRIM.IN.
- `Scan`: base TRIM setup + parameter grid (energy, angle, ion, number of
  ions, layer thickness, …) → runs each point in its own working directory
  and **own wine prefix / xvfb display**, in parallel (process pool,
  `max_workers` default = CPU count), returning one tidy DataFrame with
  scanned parameters as columns (per-point summary and/or per-ion rows).
  Results are cached by a hash of the inputs so re-running a scan only
  computes missing points. Failed points are reported, not fatal.
- Execution backends behind the same API: `local` (process pool, native
  Windows or wine) and `container` (**one job per container**, launched by
  the host via docker/podman). Underneath, a scan is
  *prepare → run jobs → collect*, with each job a self-contained directory,
  so jobs can also be run by any external scheduler (Slurm array, GNU
  parallel, Kubernetes) with `pysrim2 run-job <dir>`.
- Large single runs can be split into chunks across workers with distinct
  random seeds and merged afterwards.
- Every result records its inputs, SRIM version and seed (Parquet/HDF5
  metadata) for reproducibility.
- Long runs: timeouts, resume via TRIM autosave, and progress reporting.
- Progress front ends, chosen with `--ui`: **TUI (default, Textual)**
  showing per-job/chunk status, ions done, ETA, failures; `--ui web`
  (same Textual app served in the browser via textual-serve, or a
  Streamlit page as an optional extra — builder to pick after a spike);
  `--ui plain` (log lines) and `--ui json` (JSON-lines events for agents
  and CI). Auto-falls back to plain when not on a TTY.
- Cascade export: parse COLLISON.txt (full-cascade mode) into a flat
  table (ion id, collision/recoil id, atom, x, y, z, energy, …) written to
  Parquet (CSV fallback), streaming to avoid loading huge files at once.

### Platform findings
- macOS native is **not supported**: Homebrew disabled all wine casks
  (Gatekeeper, 2026-09-01) and wine needs Rosetta, which Apple is phasing
  out after macOS 27. Macs use the container (QEMU works).
- srim.org is flaky (403 without a browser User-Agent, connection resets
  on large files) → our GitHub Release asset is the primary source.
  Original pysrim asset `srim.2013.zip` SHA-256
  `f425c4715d5a849c1f521a8fc14aff58d41066fa4ca773546690a220ff3acecf`.
- Docker Desktop's licence requires a paid plan for larger organisations;
  docs and installer default to Podman where we install anything.
- Images run unchanged on Docker, Podman, Apptainer/Singularity (HPC),
  OrbStack/Colima, Apple `container`.
- Linux + wine native path to be verified on a real Linux machine
  (Arun has one available).

### Agent support
- Skill installation: `pysrim2 install skill` detects agents and writes
  to their skill directories (Claude Code: `~/.claude/skills/pysrim2/`
  or project `.claude/skills/`; other agents' standard locations,
  `--agent`/`--scope` to override, `--dry-run`); the repo layout also
  works with generic skill installers (e.g. `npx skills add FSIBT/pysrim2`)
  and as a Claude Code plugin.
- `skills/pysrim2/SKILL.md` (agentskills format) covering: when to use;
  running everything through the container with a mounted `/data`; building
  targets; single runs → DataFrames; scans; cascade export; typical
  pitfalls (run time vs. number of ions, quick vs. full-cascade mode,
  amd64 emulation on Apple Silicon). Worked example from the reference task:
  *"simulate 100 nm Au on Si, implant N ions at 7° and 20 keV, give me a
  DataFrame of implantation depth and xy straggle"*, plus scan examples
  (mean depth vs. angle/energy; vacancy density at a depth vs. energy).
- `AGENTS.md`: how to set up (uv), test, lint, build the container, commit
  conventions, changelog, release process. `CLAUDE.md` = `@AGENTS.md`.
- Docs page "Using pysrim2 with AI agents" with install instructions for
  the skill.

### Project hygiene
- pre-commit for development: ruff (lint + format), end-of-file/trailing
  whitespace, check-yaml/toml, large-file guard (blocks SRIM binaries and
  big outputs), uv lock check.
- `CITATION.cff` (cite pysrim2, the original pysrim JOSS paper, and SRIM),
  Zenodo DOI per release, CONTRIBUTING, issue/PR templates, pre-commit
  (ruff), Dependabot for Actions and dependencies.
- Announce the new home on the archived upstream's open PRs/issues and to
  active fork authors (e.g. Mirochill, lievcm, FilipovicLado).

### Documentation
- Prominent notice at the top of README and the docs home page:
  pysrim2 is based on Chris Ostrouchov's pysrim
  (gitlab.com/costrouc/pysrim), with a link to a
  "What changed from pysrim" page (fixes integrated + credited authors,
  new features, breaking changes, migration).
- Sphinx on Read the Docs as `pysrim2`, `.readthedocs.yaml` v2 using uv.
- Update installation (container-first), tutorial, API reference; new pages:
  container workflow, DataFrames, scans & parallel runs, cascades, agents,
  SRIM licensing note, migration from pysrim, changelog.
- README rewritten: GitHub as home, credits to Chris and fork contributors.

### Test suite
Current state (measured 2026-11-03): 77 unit tests in `tests/` (core
element/ion/layer/material/target, output parsers against `test_files/`).
They **fail to import on Python ≥ 3.12** (`distutils`); on 3.11 all pass
with 68 % line coverage (input.py 31 %, srim.py 52 %, output.py 68 %,
plot.py 0 %). `test_integration.py` is an empty stub.

Target:
- **Unit tests** (no SRIM, fast, run everywhere): input-file generation
  (golden-file comparison of generated TRIM.IN / SR.IN), every output
  parser incl. edge cases from the fixed bugs (sub-keV energies, leading
  decimals, "," locales, "m" prefix), DataFrame conversion, scan grid
  expansion/caching/failure handling with SRIM execution mocked, cascade
  export on a small COLLISON fixture. Coverage target ≥ 85 % on non-plot
  code, enforced in CI.
- **Integration tests** (marker `srim`, run only inside the container):
  small real TRIM runs (few ions) for a single layer, a multilayer
  (Au on Si), quick vs. full-cascade mode, an SR stopping table, and a
  2-worker parallel scan; assertions on physical sanity (e.g. mean range
  within tolerance of a stored reference) rather than exact numbers.
- Fixtures: reuse `test_files/`, add new ones generated by the container.
- Docs examples / SKILL.md example code are executed in the container
  integration job so they cannot rot.

### CI/CD (GitHub Actions)
- `ci.yml`: ruff + pytest (parser tests, no SRIM) on 3.11–3.15, via uv;
  plus a **Windows runner** job running real (small) SRIM natively.
- `container.yml`: build image; run an integration test (small TRIM run
  inside the container); push to ghcr on main and tags.
- `release.yml`: on tag `v*` → `uv build` → publish to PyPI via **trusted
  publishing** (no tokens) → GitHub Release with changelog section.

## Release roadmap
- **0.6.0**: foundation (fork fixes integrated, packaging, CI, docs, changelog).
- **0.7.0**: SRIM GitHub Release asset; `uv tool install pysrim2` followed by
  `pysrim2 install` (`--dry-run`, per-component install, optional container
  runtime) and `pysrim2 doctor`; container image `ghcr.io/fsibt/pysrim2`;
  real-SRIM integration tests; first version of the agent skill and its
  installation.
- **0.8.0**: DataFrames, `run.yaml`, EXYZ parser, cascade export.
- **0.9.0**: scans, chunked runs, caching, backends, progress UIs.
- Later: docs overhaul, CITATION.cff, Zenodo, announcements.

## Left to the builder (Claude's calls, adjustable)
- First version number: `0.6.0` (continues pysrim lineage, signals pre-1.0
  API); `1.0.0` once DataFrame/scan APIs settle.
- Exact phase ordering, module layout for new APIs, DataFrame column names,
  cache location/format, test strategy details.
- Whether to keep `examples/images` binaries or move them to docs.
- How parallel workers isolate wine (per-worker `WINEPREFIX` copy vs.
  shared prefix with separate SRIM directories) — decided after reviewing
  FilipovicLado's approach and measuring.

## Open questions
(none yet — to be filled from review)
