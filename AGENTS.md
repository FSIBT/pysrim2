# AGENTS.md

pysrim2 is a maintained continuation of pysrim (distribution `pysrim2`, import `srim`).

## Commands

- Setup: `uv sync`
- Test: `uv run pytest` (run from the repository root; tests use relative paths such as `test_files/`)
- Lint and format: `uv run pre-commit run --all-files`
- Docs: `uv run --group docs sphinx-build -W -b html docs <output dir>`
- Build: `uv build`
- Real SRIM tests: `uv run pytest -m srim` (in `tests/integration/`; skipped unless SRIM is installed, e.g. `pysrim2 install`; they run real TRIM/SR)
- Notebooks: git can have an external notebook diff driver; use `git diff --no-ext-diff --no-textconv` to see notebook changes as plain JSON.
- Once per clone: `git config blame.ignoreRevsFile .git-blame-ignore-revs`

## Conventions

- Conventional commit prefixes (feat, fix, test, build, docs, chore, style, ci); one concern per commit.
- Every user-visible change adds a line under `## [Unreleased]` in `CHANGELOG.md`.
- Never commit SRIM binaries or large outputs.
- Fixtures in `test_files/` are byte-exact SRIM output with CRLF line endings. Do not reformat them or let an editor change line endings.
- Fork commits: bring them in with `git cherry-pick -x` so author and message are kept. Never squash or amend them. Put our own changes (tests, adjustments, credit for others who fixed the same thing via `Co-authored-by:`) in separate follow-up commits. Record the result in `docs/changes-from-pysrim.md`.
- Python 3.11 or newer; runtime dependencies are numpy, pyyaml and pandas.

## Installer, container and CI

- Installer code lives in `srim/installer/`: `steps.py` holds the `Step` dataclass and the registry (`register(component, factory)`, `plan`, `default_components`); `srim_install.py`, `vb5.py`, `wine.py`, `container.py` and `skill.py` register their components. The CLI is `srim/cli.py`; `srim/paths.py` resolves the data directory and the `PYSRIM2_*` overrides. New components: add a factory and register it, and keep `--dry-run` printing the exact commands.
- Private-prefix rule: wine only ever runs in pysrim2's own prefix (`PYSRIM2_WINEPREFIX`, default `<data dir>/wine`). Never read, write or inherit `~/.wine` or `$WINEPREFIX`; set `WINEPREFIX` only in a child's environment.
- Never run a command that needs admin rights without confirmation (`--yes` or an interactive `y`). Pinned downloads (SRIM, winetricks) are checksum-verified; a mismatch is a hard error.
- Capture wine/SRIM output in temp files, not pipes (the daemonised `wineserver` keeps pipes open).
- Container: `Dockerfile` (linux/amd64, non-root uid 1000, `/data` workdir). Build locally with `docker build --platform linux/amd64 -t pysrim2:dev .`. The container workflow runs the `-m srim` tests inside the image; Docker 29 needs `--security-opt seccomp=srim/data/seccomp-docker.json` (never `unconfined`). Images are tagged `main`, `X.Y.Z` (no `v`), `X.Y`, `latest`.
- CI jobs: `srim-linux` (real SRIM under wine), `srim-windows` (real SRIM, native) and `container` (build, test in the image, publish to GHCR). Windows and Docker problems often only show up there; check the run logs.
- `examples/scripts/` are run by `tests/integration/test_example_scripts.py` (`-m srim`); `boron_in_gold_on_silicon.py` there must stay byte-identical to the skill's copy (`tests/test_examples.py` checks). Notebooks in `examples/notebooks` use the 0.8.0 API and the default SRIM location, are committed without outputs, and are run by `tests/integration/test_run_notebooks.py` (`-m srim`); `tests/test_notebooks.py` checks them statically (no absolute paths, imports exist).
- The agent skill source is `skills/pysrim2/SKILL.md` (shipped in the wheel); its example is run by `tests/integration/test_skill_example.py`. `SKILL.md` is for agents that use pysrim2; this file is for contributors.

## Outputs, run spec and export (0.8.0)

- `srim/output/` is a package: `_common.py` (`SRIM_Output` base, regexes, errors), `tables.py` (`Results` and the depth profiles `Ioniz`, `Vacancy`, `NoVacancy`, `EnergyToRecoils`, `Phonons`, `Range`), `range3d.py`, `events.py` (`EXYZ`, `Backscat`, `Transmit`, `Sputter`), `collision.py` (`Collision`, streaming), `sr.py`, and `_chunks.py` (reusable line streaming and row-chunk helpers: `iter_lines`, `iter_row_chunks`, `concat_chunks`). Each output has `.df` and `.metadata`; the large ones are parsed lazily from `Results`, and nothing may read a whole big file into memory. Import paths such as `srim.output.Ioniz` must keep working.
- `srim/spec.py`: `RunSpec` and friends (`run.yaml`, schema version 1), `SpecError`, `--set` support. `TRIM.IN` from a spec must stay byte-identical to the one from the equivalent `TRIM(...)`; the host name belongs only in `run.yaml` provenance, never in exports.
- `srim/export.py`: Parquet, HDF5 and CSV export with metadata; `collision` is opt-in and always chunked (`chunk_rows`). pyarrow and tables are optional extras and imported lazily.
- `srim/analysis.py`: `implant_summary`.
- Fixtures for the event files are real SRIM output generated by `test_files/outputs-0.8/generate.py` (see the README there: run it in the container from the repository root with the `docker run` command shown). The comma-decimal copies come from `make_comma.py`. Do not hand-edit them.
- Docs: `docs/dataframes.md` (column tables), `docs/run-spec.md`, `docs/export.md`. When a column changes, update the table; the tables were generated from `.df.columns` of the parsers on the fixtures.
