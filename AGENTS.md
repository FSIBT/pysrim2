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
- Python 3.11 or newer; runtime dependencies are numpy, pyyaml and pandas. The desktop app is the `[app]` extra (PySide6, matplotlib), not a runtime dependency.

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

## Scans (0.9.0)

- `srim/scan/spec.py`: `Scan` (base spec, `grid`/`points`, `extract`, `ions` or `Precision`, `seed`), `ScanPoint`, `spec_hash` (the point id), and the `scan:` section of `run.yaml`.
- `srim/scan/extract.py`: the extractor registry (`register`, `get`, `resolve`, `combine`), the predefined extractors and the `python:module:name` references.
- `srim/scan/precision.py`: `Precision` and the pure rules (batch-means error, initial batches, goal, batches to add). `batches.py`: `derive_seed`, `MergedResults`, `run_batches`.
- `srim/scan/executor.py`: `run_scan`. Worker processes, the parent's bookkeeping, retries, pause and stop, the scan folder, scan and cache locks, events. `cache.py`: `RunCache`, `cache_key`, `clear_cache`, the SRIM-build rule.
- `srim/scan/backends.py`: job folders (`job.yaml` with `{version: 1, units: [{name, spec}]}`; `out/<unit>/` with `.done` written last, or `error.log`), `run_job` (`pysrim2 run-job`), `ContainerRunner` (one container per unit, stop removes it). `slurm.py`: `prepare_slurm` (job folders and `submit.sbatch`) and `collect_slurm`.
- `srim/scan/events.py`: the event dataclasses (`kind`, `time`, fields; `to_dict`, `to_json`, `event_from_dict`). `result.py`: `ScanResult` (table, statuses, exports, `plot`).
- `srim/ui/`: `plain.py` (status line), `jsonl.py` (`--ui json`), `notebook.py` (`LiveView`), `web.py` (the server process; `events.jsonl` and `control.jsonl` in the scan folder), `tui/` (Textual app `app.py`; pure `state.py` and `plot.py`; `view.py`, the viewer that `web.py` serves).
- `srim/cli.py`: the `scan` subcommands, `run-job`, `clean runs`; exit codes. `srim/rundir.py`: run directories, retention (`prune_runs`), the run lock.
- Scan folder: `.pysrim2-scan` marker, `.scan.lock`, `scan.yaml`, `points/<id[:16]>/` (batches in `batches/<b>/`), `result/`, `cache/`. Events are emitted from the calling thread only; an `on_event` consumer must be quick. The web view's `events.jsonl` starts with a `run` line.
- Tests: `tests/scan/` (unit and executor tests), `tests/test_cli_scan.py`, `tests/test_ui*.py`, `tests/test_tui_*.py`. Fake runners: `run_scan(runner=...)` and `run_job(runner=...)` take a module-level function that returns `Results` (the fixture `test_files/1`). The CLI tests name one with `PYSRIM2_SCAN_RUNNER=module:function`; worker processes import it too, and the CLI prints a notice. The fake container runtime (`tests/scan/test_backends.py`) is a script put first on `PATH` that checks the podman or docker flags; it is POSIX-only (skipped elsewhere).
- Markers: `srim` (real SRIM, `tests/integration/`, `pytest -m srim`) and `container` (the image, `pytest -m container`, needs `PYSRIM2_IMAGE`). The default run excludes both.
- Workers start with `spawn` on every platform, so a script that runs a scan needs an `if __name__ == "__main__":` guard.
- The docs for this area are `docs/scans.md` and `docs/concurrency.md`; the agent section is in `skills/pysrim2/SKILL.md` (section 7).

## Beam distributions (0.10.0)

- `srim/beam/` holds the distributions and their runs: `shapes.py` (the `Shape` classes, `shape_from_dict`, the tabulated CDF in `nodes()`, truncation, `Histogram`, `Function`), `histfile.py` (histogram text files and their rules), `spec.py` (`BeamOptions`, `PositionSpec`, `check_beam`, `effective` (the physical cuts), `fingerprint` (function sources for the cache key), `MAX_TRIM_DAT_IONS`, the binned limits), `exact.py` (`sample_ions`, `write_trim_dat`, `read_trim_dat`: the TRIM.DAT layout and the random streams), `binned.py` (`make_bins`, `allocate_ions` (Sainte-Laguë), `bin_spec`, `bins_frame`), `run.py` (`run_beam`, `open_results`, `sampled_ions`, `result_folder`), `plot.py` (`plot_beam`; matplotlib imported lazily).
- Hooks in other modules: `srim/spec.py` (distribution forms in `IonSpec`, the `beam:` section, `_SHAPE_PARAMS` for `--set`, `is_distributed`), `srim/srim.py` (exact-mode TRIM.DAT and TRIM.IN, the calculation 4/5 mapping, the copies of histogram files in `beam/`, the 99 999 refusal of `TRIM.from_spec`), `srim/scan/spec.py` (`_check_trim_dat_units`, `is_binned`, distribution keys in grids), `srim/scan/executor.py` (binned points: bin units and beam batches), `srim/scan/batches.py` (`units.csv`, `beam_bins.csv`, `MergedResults` with the `batch` and bin columns).
- Tests: `tests/beam/` (`test_shapes`, `test_histfile`, `test_beam_spec`, `test_exact`, `test_binned`, `test_run_beam`, `test_beam_scan`, `test_beam_cache`, `test_plot_beam`; `beam_funcs.py` holds the Python density functions). Real SRIM: `tests/integration/test_beam_runs.py` (`-m srim`), `test_example_scripts.py::test_beam_energy_spread` and `Beam.ipynb` in `test_run_notebooks.py` (`-m srim`). Static: `tests/test_examples.py`, `tests/test_notebooks.py`.
- Docs: `docs/beam.md` (the user guide), with the beam sections of `docs/run-spec.md`, `docs/scans.md` and `docs/dataframes.md`; the agent section is `skills/pysrim2/SKILL.md` (section 8).
- Rules that must hold: the random streams are `SeedSequence(seed).spawn(6)`, one `PCG64` per parameter in the order of `exact.STREAMS`, so reordering them changes every beam. Physical cuts go through `effective()` only. The cache key and point id use `fingerprint` and the histogram content. A `TRIM.DAT` is CRLF, has 10 header lines and a trailing 0x1A, and its ion names are exactly 5 characters. `TRIM.IN` of a spec without distributions stays byte-identical.
- Python density functions are referred to as `python:<module>:<name>` and must be importable by name in the worker processes (`check_function_ref` refuses `__main__`, lambdas and nested functions).

## Docs and gallery (0.11.0)

- Sidebar: `docs/index.md` has four sections (Get started, Gallery, Guides, Reference), each a hidden `{toctree}`. A new page goes into one of them; the README links must name existing pages (`docs/<page>.md` or `.rst`).
- Docs build: `uv run --group docs sphinx-build -W -b html docs <output dir>`. The CI `docs` job and Read the Docs both use `-W`, so a warning fails both. The docs build never runs SRIM, wine, Docker or a gallery script.
- The API reference is `docs/api/`: autodoc pages, one per part of the library. Each object is documented on one page only.
- The boron reference case (200 ions, 20 keV, 7 degrees, 100 nm Au on Si, seed 12345; mean depth about 282 Å) is shown in the README, `docs/first-simulation.md` and `docs/agents.md`. Change them together.
- Gallery: `docs/gallery/` holds one script per picture. Each `*.py` there (not `make.py`, not `_`-prefixed) is an entry with `TITLE`, `NEEDS_SRIM`, `OUTPUT` and `make(outdir)`; `docs/gallery/README.md` has the contract. Users read the entry scripts as example code, so keep them short.
- Pictures are committed in `docs/gallery/images/` (under 300 KB each). Rebuild them on Linux with SRIM installed: `uv run --extra plot --extra tui python docs/gallery/make.py` (`--list` shows the entries). Commit the changed images with the change that caused them.
- Tests: `tests/test_gallery.py` (entry contract, committed images, gallery page), `tests/integration/test_gallery_srim.py` (`-m srim`, builds the SRIM entries), `tests/test_readme.py` (README images exist, README docs links resolve), `tests/test_citation.py` (`CITATION.cff` and `pyproject.toml` authors, every git author listed).
- Ruff also formats the Python code fences inside the Markdown docs, so `uv run ruff format` covers them.
- Authors: a new contributor goes into both `pyproject.toml` (`authors`) and `CITATION.cff` (`authors`), in the same order. A contributor with a second git identity goes into `.mailmap` too, so their commits count as one author.

## Desktop app (0.12.0)

- Layout: `srim/app/` holds `pysrim2 app`. Qt-free modules: `__init__.py` (`DOCS_URL`), `routes.py` (`ROUTES`, `ROUTE_LABELS`, `available_routes`, `recommended_route`, `route_components`), `settings.py` (`settings_path()` = `<data dir>/app/settings.json`, `THEMES`, `load_settings`, `save_settings`) and `guidance.py` (`setup_guidance`). Qt modules: `theme.py`, `workers.py`, `statusbar.py`, `window.py`, `setup.py` and `main.py` (`main` is the entry point that `_app` in `srim/cli.py` imports only once `app_available()` is true). `srim/installer/app.py` (the `app` install component) is Qt-free too.
- No-Qt rule: only the Qt modules above import PySide6. `tests/test_no_qt.py` runs a fresh interpreter that imports `srim`, `srim.cli` and the Qt-free app modules (`routes`, `settings`, `guidance`), runs `install --dry-run app` and `doctor`, and fails if PySide6 gets imported. Keep Qt out of anything the library or the CLI imports.
- One runner: the app and `pysrim2 install` both run steps through `run_steps` in `srim/installer/runner.py`. The app calls it with `yes=False, confirm=None` and its own `say`, `warn` and `admin_hint`, so an admin step is never run by the app: its commands are shown ("Copy commands") for the user to run in a terminal, then "Check again" plans the steps once more. Do not change that without a decision from the maintainer.
- Threads: Qt work that may block (checks, installs) runs through `srim/app/workers.py` `run_in_thread`; results reach the GUI thread through queued signals. A window that closes while a thread runs ignores its late result.
- Placeholders: in 0.12.0 the Geometry, Beam, Run settings, Progress and Results tabs only say that they arrive in a later release.
- Tests: `tests/app/` runs the Qt tests on the offscreen platform (`tests/app/conftest.py` sets `QT_QPA_PLATFORM=offscreen` before Qt loads and gives every test its own `PYSRIM2_HOME`). The Qt tests use fake checks and fake steps: no install, network, wine or container. Tests that do not import PySide6 (`test_routes`, `test_settings`, `test_guidance`) run without the extra; the others skip when PySide6 or `pytest-qt` is missing. `uv sync` installs the extra and `pytest-qt` (dev group); CI installs the libraries the offscreen platform needs on Linux.
