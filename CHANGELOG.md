# Changelog

All notable changes to this project are documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Added

- `srim.notebook.load_run(folder)` reads a run or scan folder, finished or not, for notebooks.
- The `app` extra now includes the notebook tools (marimo, JupyterLab), QtWebEngine (`PySide6-WebEngine`, PySide6 6.12 or newer) for the embedded notebook and the PDF export; `pysrim2 install app` also downloads the Chromium the export prints with.
- The desktop app's Notebook menu starts a marimo or Jupyter notebook of the open run folder in a Notebook tab, with Reload, Open in browser and Stop. A banner says when the run has results the notebook has not loaded yet.
- The desktop app's Notebook menu (and the notebook tab) has Export PDF…, which writes the open notebook to a PDF file that you choose: marimo runs the notebook to print it, Jupyter prints it with Chromium. The export runs in the background, one at a time, and a missing browser says to reinstall the app first.

## [0.12.0] - 2026-11-13

### Added

- The desktop app's File > Open recent lists the last ten setups and run folders (kept in the app's data folder), and File > Open run folder… opens a run folder: its setup, results and progress come back as after the run, with the points that have results when the run did not finish. An entry whose file or folder was moved or deleted is greyed out with the reason.
- The desktop app's File > Save results… writes the last run's results in the library's formats (Parquet, HDF5, CSV, or the SRIM output files copied unchanged, with the collision details on request): a plain run's tables, or a scan's table and its chosen points. It asks before it replaces anything, and a format whose extra is not installed is greyed out with the install hint.
- `Range3D` and the event parsers (`EXYZ`, `Backscat`, `Transmit`, `Sputter`) take `max_rows`: they read at most that many rows (the first ones) and their `truncated` attribute says whether rows were left out. The default reads every row, as before.
- `srim.beam.exact.trim_dat_frame(table)` gives the `read_trim_dat` frame of a table of TRIM.DAT fields.
- `pysrim2 install app` installs the desktop app: the new `app` extra (PySide6 and matplotlib) goes into the running pysrim2 (`uv tool`, pip or a source checkout, which is told the command). The library and the command line still never import Qt.
- `pysrim2 app` opens the desktop window: a welcome screen and a guided setup (it opens by itself on the first check if no route is chosen yet or a check is not ready, and Setup > Run setup… reopens it) that chooses native, wine (Linux only) or container (the recommended one is preselected; macOS offers only the container), installs what is missing and checks the result. The app never runs a command that needs admin rights; it shows those for you to run in a terminal and then press Check again.
- The app's status bar says what to do next, with a click through to the setup, and View > Theme switches between the system, light and dark colours.
- The desktop app opens and saves a run setup (File > New, Open, Save and Save as, asking before unsaved edits are discarded) with Edit > Undo and Redo, and its status bar lists what the setup is still missing, with a click that jumps to the field.
- The desktop app needs Python 3.14 or older until PySide6 supports Python 3.15: there the `app` extra is empty, and `pysrim2 install app` and `pysrim2 app` say so.
- The library has tables of the natural isotopes (`srim.core.isotopes`) and of common materials (`srim.core.compounds`).
- The desktop app's Geometry tab edits the layers: a table with name, elements, density, thickness (in Å, nm, µm or mm), gas or not, and Add, Remove, Up and Down (or drag a row); each layer's elements are edited in a dialog, a material menu fills a layer from the common materials or from "My materials", and "Save as material…" keeps a layer's composition for later. A cell the setup cannot use is shown in red.
- The desktop app's Geometry tab shows the target to scale beside the layer table: each layer's width follows its thickness (a thin layer is drawn wider than to scale and marked "≠"), the beam comes in at its angle, and a click on a layer selects its row and the other way round.
- The desktop app's Beam tab sets the ion (from the periodic table, with its isotopes), the number of ions, and the energy, angle, azimuth and position of the ion, each as a fixed value or a distribution (Gaussian, uniform, triangular, log-normal, a Python function or a histogram file, with an optional "Limit to" range); a histogram file is previewed before it is used, and the setup keeps a copy of it when it is saved.
- The desktop app's Beam tab chooses the beam mode (Automatic, Exact with TRIM.DAT, or Binned, with the number of energy and angle bins, Automatic by default), says which one Automatic picked and why, and draws the density of each beam quantity under its editor, with the bin edges of a binned beam; a single energy and angle is one SRIM run and shows no mode.
- The desktop app's Run settings tab sets the calculation, the random seed, the description, the output files (ranges, backscattered and transmitted ions, sputtered atoms), collision details, the EXYZ threshold, the Bragg correction and autosave; a setting left at SRIM's default is not written to the file.
- The desktop app keeps a broken `materials.json` (My materials) as `materials.json.bak` before a save rewrites it, so no saved material is lost without a copy.
- `Scan(..., keep_seed=True)` (`keep_seed: true` in a scan section) runs a one-point scan with the scan's own seed, so a single run has the `TRIM.IN` of its run spec. `srim.beam.beam_scan(spec)` is the scan that `run_beam` runs for a spec, and `srim.scan.read_scan_result(folder)` reads a finished scan folder back.
- The desktop app's run setup can hold a scan: the `scan:` section of a `run.yaml` is opened, kept with undo and saved with the setup (a file without one is written as before), and the setup says what the scan still needs (what to vary, the ions per point).
- The desktop app has a Scan tab (after Run settings): "Run as a scan" turns a setup into a scan, the parameters to vary take typed values or a range (linear or logarithmic), the ions per point are a fixed number or a precision target, the values to extract are ticked, and the seed and the batch size are set; explicit points and Python extractors of a file are shown and kept.
- The desktop app's Run menu (and toolbar) starts, pauses and stops a run of the setup: Start (Ctrl+R) saves an unsaved setup first (a setup opened from a run folder is saved as a file of its own, so the run folder is never changed), a long run is offered parallel workers, batches of ions or containers, and the setup is locked while it goes. Quit asks before it stops a run, and a stopped run keeps the points it finished.
- The desktop app's Progress tab shows a run as it goes: the time elapsed and the time left, how many points are done, running, queued or failed, the workers with their ions per second, a table of the points (sorted by a click on a column heading, with "Open log" for the selected or the last failed point) and the warnings and errors, newest first. It keeps the final state until the next run starts.
- The desktop app's Results tab draws the plots of a run's results (depth profiles, ion and damage maps, energies and the sampled beam) for one point at a time, grey out the plots a run cannot give, and redraws the shown plot while a run goes; a plot can be copied or saved as CSV, opened in a window of its own, and the Beam tab shows the sampled ions of a finished exact run. A setup that changed since the run says "From the previous setup".
- The documentation of the desktop app (`docs/app.md`) covers the Scan tab, running a setup (parallel workers, pause and stop, the run folder), the Progress tab, the Results tab, saving results and the recent setups and runs, with two pictures of the window.
- The desktop app's Results tab has a Scan group for a scan: a Summary (an extracted value against a parameter, coloured by another parameter, with error bars, live during the run) and an overlay per depth profile (one curve per checked point, at most 12, chosen with a check list and a filter such as `` `ion.energy_keV` > 10 ``).

### Fixed

- The scan dashboard (`--ui dashboard`) no longer crashes with "More values provided than there are columns" when it is redrawn before it has finished opening.

## [0.11.0] - 2026-11-07

### Added

- `CITATION.cff`: cite the original pysrim paper (Ostrouchov, Zhang, Weber 2018, JOSS); every git author and co-author is listed in `CITATION.cff` and the package metadata.
- New documentation front page (`docs/index.md`) with two routes: a Python route (install, the boron reference case) and an agent route (install the skill, give it a task), and four sidebar sections (Get started, Gallery, Guides, Reference).
- Example gallery (`docs/gallery.md`) with a picture and its code for every entry; the pictures are rebuilt by `docs/gallery/make.py`, and the entries that run SRIM need an installed SRIM.
- First-simulation page (`docs/first-simulation.md`) replaces the 2018 tutorial.
- Agent guide (`docs/agents.md`): how an AI coding agent uses the pysrim2 skill, with example prompts.
- Examples page (`docs/examples.md`) lists the example scripts and notebooks.
- Regenerated API reference (`docs/api/`), one page per part of the library.
- Citing page (`docs/citing.md`) with the BibTeX entry and the SRIM citation, and a citing section in the README.
- The documentation is built in CI (`docs` job, `sphinx-build -W`), so a warning fails the build as it does on Read the Docs.

## [0.10.0] - 2026-11-06

### Added

- Beam distributions in `run.yaml` and `RunSpec`: `ion.energy_keV`, `ion.angle_deg` and `ion.azimuth_deg` take a number or a distribution (Gaussian, uniform, triangular, log-normal, histogram file or inline, or a Python function), `ion.position` sets the start position, and `beam:` chooses exact or binned mode; `--set` and scan keys reach into the distributions. Runs without distributions are unchanged.
- Exact mode of a distributed run (`beam.mode: exact`, the default) samples every ion from the distributions and writes SRIM's `TRIM.DAT`, so one `TRIM.run()` runs exactly that beam (calculation 1 and 2 become SRIM 4 and 5); the histogram files it used are copied to the run folder's `beam/` folder. Up to 99 999 ions per run. An in-place run (`srim_directory=`) never deletes a `TRIM.DAT` or `beam/` file that pysrim2 did not write, and an exact run there is refused if such a `TRIM.DAT` would be overwritten.
- Binned beams in scans (`beam.mode: binned`): each scan point with a binned beam runs as one SRIM run per bin, the ions split over the bins by apportionment, in beam batches for precision targets and `batch_ions`; the bins' results are merged back into one result per point. Exact distributed points with more than 99 999 ions need `batch_ions: 99999`.
- Point folders of batched scan points record their units (`run.yaml`, `units.csv`, and `beam_bins.csv` for binned beams), and `srim.beam.open_results(point_dir)` re-opens a finished batched point as one merged result.
- Documentation for beam distributions: `docs/beam.md` (both modes, every shape, histogram files, Python functions, seeds, results and limits), an example script (`examples/scripts/beam_energy_spread.py`), a notebook (`examples/notebooks/Beam.ipynb`), beam sections in `docs/run-spec.md`, `docs/scans.md` and `docs/dataframes.md`, and an agent-skill section (`skills/pysrim2/SKILL.md`, section 8).
- `srim.beam.run_beam(spec)` runs a distributed run spec (one TRIM run for an exact beam of up to 99 999 ions, else a one-point scan that splits exact beams into units and runs binned beams per bin) and returns the results; `srim.beam.sampled_ions(results)` reads the sampled ions back from TRIM.DAT, and `srim.beam.plot_beam(results, parameter)` draws the defined density over the sampled beam or the bins (matplotlib, the `plot` extra). `pysrim2 run` accepts distributed specs and prints where the results are.

### Changed

- The scan cache key and point id cover beam distributions: their definitions, histogram content (not only the file path) and the source of Python-function distributions, so a changed distribution never reuses an old run. Specs without distributions keep their keys.

## [0.9.0] - 2026-11-06

### Changed

- **`TRIM.run()` and `SR.run()` now run in an isolated directory by default.** Every run works in its own private copy of the SRIM install (hard links for the read-only files, so a copy adds well under 15 % of SRIM's size; real copies of what SRIM writes) under `<data dir>/runs` (or `work_dir=`); the installed SRIM directory is never written to, so any number of runs, scripts, scans or users can run at the same time. On Linux each run also gets its own clone of the wine prefix, so cleaning up after a failure only stops that run's wine processes. The returned `Results` points at the run directory (`results.directory`); pass `output_dir=` to get the output files and `run.yaml` in a folder of your choice (the run directory is then removed unless `keep=True`); `run_dir=` reuses a `srim.rundir.RunDir`. A failed run keeps its directory for debugging and names it in the error.
  **Migration:** code that read outputs from the SRIM directory after `run()` (`TRIM.copy_output_files(srim_dir(), dest)`, `Results(srim_dir())`) must use the returned results instead: `TRIM.copy_output_files(results.directory, dest)` or `run(output_dir=dest)`. Passing `srim_directory=` explicitly keeps the old in-place behaviour, now under an exclusive file lock (a second run waits and prints a message after 5 s; `lock_timeout=` limits the wait).

- `TRIM.copy_output_files` copies the files that SRIM writes to its `SRIM Outputs` subfolder instead of moving them, so a `Results` of the source (the one `run()` returned) keeps working afterwards.

- `pysrim2 run` uses an isolated run directory too, so several can run at once.

- Runs no longer change the process working directory (the input writers take a `directory=` argument and SRIM is started with `cwd=`), so `run()` is safe in threads. Under `xvfb-run` every run claims its own display number (`-n`, via lock files `<tmp>/.pysrim2-xvfb-<N>.lock`) instead of racing with `-a`; an X server that fails to start is retried up to three times.

- Run directories without `output_dir=` are deleted automatically once unused for 7 days (`PYSRIM2_RUN_RETENTION_DAYS`, 0 keeps them; failed runs too); `Results.close()` / `with trim.run() as results:` delete one at once; `pysrim2 clean runs [--older-than DAYS] [--dry-run]` and `pysrim2 doctor` (size of the run directories) manage them. Open `Results` keep their run directory alive (shared lock) and pruning skips folders younger than a minute; leftovers that cannot be deleted are reported with a warning, and files shared with the SRIM install (hard links) never have their mode changed.

- Reading outputs from the installed SRIM directory (`Results(srim_dir())`, `TRIM.copy_output_files(srim_dir(), ...)`) after isolated runs gives a one-time `UserWarning` explaining the 0.9.0 change.

- Nothing is ever created in the installed SRIM directory: the lock for in-place runs lives in `<data dir>/locks/`, and copying the install waits for an in-place run that is writing; an in-place run in a read-only SRIM directory fails with a clear message. Relative `work_dir=` is made absolute.

### Added

#### Documentation

- New pages `docs/scans.md` (scan concepts, extractors and their columns and units, precision, the result table, cache, backends, live views, exit codes, tmux over SSH) and `docs/concurrency.md` (isolated run directories, locks, migration from 0.8, one cache for several scans). `docs/cli.md` lists the new commands.
- A scan notebook (`examples/notebooks/Scan.ipynb`), an example scan (`examples/scripts/angle_energy_scan.py` and `scan.yaml`, run by the tests with real SRIM) and a scans section in the agent skill (`skills/pysrim2/SKILL.md`, section 7).

#### Scan definition and extractors

- `srim.scan.Scan(base, grid=, points=, extract=, ions=, seed=, batch_ions=)` defines a parameter scan: a cartesian `grid` and/or zipped `points` over dotted keys (`ion.energy_keV`, `target[0].thickness_nm`), `points()` with a stable id per point (`spec_hash`), and a `scan:` section in `run.yaml` read by `Scan.from_yaml` / written by `Scan.to_yaml`. `srim.scan.Precision` describes a precision goal for `ions=`. Errors name the dotted key; `number_ions` and `seed` are not scan parameters.
- `srim.scan.extract`: extractors turn a run's outputs into scan columns (`register`, `get`, `available`, `resolve`, `combine`) with predefined `implant_summary`, `vacancy_at(depth_nm)`, `peak_vacancy_depth`, `sputter_yield`, `backscattered_fraction`, `transmitted_fraction`, `ions_in_layer(layer_index)` and `energy_to_recoils`.
- `srim.scan` precision targets: `Scan(..., ions=Precision(target=, rel=|abs=, min_ions=, max_ions=, batch_ions=))` runs each point in batches of ions (batch seeds derived from the point seed) until the target's standard error (`<value>_err`, the batch-means estimator) reaches the goal; the value is the extractors applied to all batches merged. A point that reaches `max_ions` first has status `max_ions` and a warning; a stopped point keeps the estimate of its finished batches. `batch_finished` events report each decision.
- `srim.scan.ScanResult`: one row per scan point (parameters, `id`, values with `<value>_err`, `n_ions`, `seeds`, `status`, `error`, `path` relative to `base_dir=`, so exports hold no host paths) with `to_parquet`, `to_csv` (a folder with `scan.csv` and `metadata.yaml`), `to_hdf5` and `plot(x, y, hue)` (matplotlib, error bars from `<value>_err`).
- New optional extra `pysrim2[plot]` (matplotlib) for `ScanResult.plot`; `pysrim2[all]` includes it.

#### Running a scan

- A scan folder holds a `.pysrim2-scan` marker (written by `run_scan` and `prepare_slurm`) and a running scan holds `.scan.lock`: a second `run_scan` in the same folder raises `srim.scan.ScanBusy` before it writes anything.
- `srim.scan.run_scan(scan, workers=, scan_dir=, on_event=, stop_event=, retries=)` runs the points of a scan in worker processes (`workers` defaults to the CPU count minus one; each worker reuses one private run directory). It writes a scan folder (`scan.yaml`, `points/<id>/` with the outputs and `run.yaml`, `result/`) and returns the `ScanResult`. A failed point is retried once with the same seed, then reported with its error (`points/<id>/error.log`) while the scan goes on. Progress and results arrive as JSON-safe events (`srim.scan.events`: `scan_started`, `point_started`, `point_finished`, `point_failed`, `worker_stats`, ...) from one thread, in order. Ctrl-C (or `stop_event`) stops scheduling, kills the running SRIM runs (also children that ignore SIGTERM) and keeps the finished points; the others get status `stopped` and a `point_stopped` event. Workers stop with their parent (POSIX: a killed scan leaves no SRIM running; its run directories are pruned by later scans). Workers that keep dying stop the scan with an error. Python extractors may be passed as callables: `scan.yaml` stores them as `python:<module>:<name>` references, which read back for module-level functions (lambdas raise a clear error when the file is read). `backend="container"` runs each unit in its own container (the next item).
- `srim.scan.Scan(..., batch_ions=N)` (YAML `scan.batch_ions`) splits a point with a fixed ion count into batches of `N` ions, each an independent run with its own derived seed, spread over the workers; the point's values and `<value>_err` are the batch-means estimate, as for a precision target, and each batch is cached. The default (`None`) keeps one run per point. Not allowed with a precision target.
- `srim.scan.run_scan(..., pause_event=)`: while the event is set no new unit starts (running units finish, a stop still works); each change is reported as a `scan_paused` event (`ScanPaused`, `paused`).
- `srim.scan.run_batches(spec, n_ions, batch_ions, seed)` runs a calculation as batches with deterministic recorded seeds (`derive_seed`) and returns `MergedResults` (per-ion tables concatenated with global `ion` numbers and a `batch` column, depth profiles merged as ion-count-weighted means, collisions streamed), usable like `Results` by the extractors and exports.

#### Result cache

- `srim.scan` result cache: `run_scan(..., cache=)` keeps every finished SRIM run (its output folder with `run.yaml`) in `<scan folder>/cache` (or `$PYSRIM2_CACHE`, or the folder given; `cache=False` turns it off), keyed by `srim.scan.cache_key(spec, seed)`: a SHA-256 of the run spec that writes `TRIM.IN` (`number_ions` included) and the seed, never of provenance. A rerun, or another scan that needs the same runs, reuses them without computing them again (`point_cached` events; status `cached`); a precision rerun with a tighter goal runs only its new batches; two scans that need the same run compute it once (per-key locks, so the second waits and reuses it). Entries are written atomically, failed runs are never stored, and an entry made with another SRIM build is computed again and replaced by the new run. A point all of whose runs come from the cache has status `cached` (a precision point that stops at `max_ions` keeps that status). `srim.scan.clear_cache(path)` empties a pysrim2 cache folder: it removes only the cache's own entries and keeps its lock files, and it warns about anything else it finds. `pysrim2 scan clean` is the command-line form.

#### Backends

- `srim.scan.run_scan(..., backend="container", image=, runtime=)` runs every unit in its own container of the pysrim2 image (Podman or Docker, found like `pysrim2 install`; `PYSRIM2_IMAGE` or the release image). Events, results and cache are the same as for the local back end. The worker that runs a unit removes its container when the scan is stopped, and a Docker run gets the seccomp profile that wine needs. Without a container runtime the scan fails before any worker starts. A custom `runner=` is not allowed with this back end. Containers run with `--platform linux/amd64` (the image is amd64-only) and `--network=none`.
- Job folders: `srim.scan.backends.run_job(dir)` and `pysrim2 run-job DIR` run the units of a folder (`job.yaml`, outputs in `out/<unit>/`, `.done` written last, `error.log` for a failed unit); a second run only runs what is left, so an interrupted job resumes. Exit codes: 0 all done, 1 some failed, 2 not a job folder.
- Slurm: `srim.scan.prepare_slurm(scan, scan_dir, jobs=None)` writes the scan folder for a cluster: `scan.yaml` with the base seed, one job folder per point (or `jobs=N` round-robin groups) and `submit.sbatch`, a job array that runs `pysrim2 run-job` per folder. `srim.scan.collect_slurm(scan_dir, cache=None)` builds the same `ScanResult` as a local run once the tasks are done (failed points carry their `error.log` summary; points not run yet get status `missing` with the error "not run yet"), and stores the finished runs in the cache. Fixed ion counts only (precision targets need a local or container run).

#### Command line

- `pysrim2 scan run SCAN.yaml [--workers N] [--backend local|container] [--image REF] [--ui plot|dashboard|web|plain|json] [--output DIR] [--cache DIR | --no-cache] [--retries N] [--set KEY=VALUE ...]` runs a scan and prints its result path. `--ui plain` prints a status line when the state changes; `--ui json` writes every event as one JSON line on stdout. When stdout is closed early (e.g. piped into `head`), both stop writing quietly and the scan finishes. Exit codes: 0 every point done or cached (a point that reached `max_ions` gives a warning), 1 some point failed or is missing, 2 bad input or nothing could start, 130 stopped by Ctrl-C (the partial result is kept). `plot` (the default) and `dashboard` are the live terminal views (see the `[tui]` extra below); `web` is the same live view in a browser (see below).
- `pysrim2 scan prepare SCAN.yaml -o DIR [--jobs N] [--set ...]` writes the job folders and `submit.sbatch` for a cluster; `pysrim2 scan collect DIR [--cache DIR | --no-cache]` builds the result of the finished tasks and prints a summary per status.
- `pysrim2 scan clean DIR [--dry-run]` removes the cache entries of a scan folder that no scan is using; `--all` removes the whole folder, and only one with the `.pysrim2-scan` marker: it refuses a symbolic link, the working directory or a folder above it, the home folder, and a folder that a running scan holds (a removal that fails exits 1). `pysrim2 scan clean --cache PATH` clears a shared cache.
- `pysrim2 scan run` reuses a scan folder whose `scan.yaml` describes another scan with a warning on stderr, and a rerun without `seed` in the same folder keeps the folder's recorded seed. A scan that fails after it started exits 1 (`scan failed`); only an error before the start exits 2. `pysrim2 scan run` and `prepare` take the path after `--set` in either order. `PYSRIM2_SCAN_RUNNER` prints a notice on stderr when it replaces SRIM.

#### Live views

- `srim.ui.PlainProgress` (a status line when a scan's state changes, at most every 2 s, no escape codes) and `srim.ui.JsonLines` (every event as one line of JSON) are event consumers for `run_scan(on_event=...)`; `pysrim2 scan run --ui plain|json` uses them.
- The plain view's final line counts the points that came from the result cache as done and says how many: `finished 12/12 done (12 cached), 0 failed, 0 stopped  ions 2400 …` (a point of which a worker ran a unit is `done`, as in the result table).
- **Live terminal views of a scan** (optional extra `pysrim2[tui]`: `textual`, `textual-plotext` and `textual-serve`; `pip install pysrim2[tui]`). `pysrim2 scan run` opens the live plot in a terminal by default: the chosen result fills in as points finish, its error bars shrink as a precision point accumulates ions, and a status strip shows done, running, queued, failed, ions, elapsed and ETA. `--ui dashboard` shows the overall counts and gauge, the workers, a throughput sparkline, a sortable scan-points table (click a header) and the latest warnings. `--plot X,Y[,HUE]` picks the axes and series (defaults: the first and second scanned parameters, the first extracted value; `X,Y,` for no series). Keys: `p` pause or resume (no new point starts; running ones finish), `q` (or Ctrl-C) stop cleanly (pressed again, Ctrl-C quits at once, and the scan stops in the background for at most the grace period; SIGTERM stops the scan too), `v` switch plot and dashboard, `l` the `error.log` of the selected or last failed point. The views redraw incrementally, work over SSH and in tmux, and never hold up the workers. Without a terminal, or without the extra, `plain` runs with a notice, as before. **Migration:** scripts that run `pysrim2 scan run` in a terminal and expect plain text should pass `--ui plain` (or `--ui json` for events).
- **The terminal views stay open after the scan ends.** The status strip says `finished — press q to quit` (the dashboard says it in its overall panel); every key still works, `p` does nothing, and `q` or Ctrl-C quits with the scan's exit code. `pysrim2 scan run --no-wait` closes the view as soon as the scan has finished, as before; `ScanApp(wait=False)` is the Python-side switch. A scan stopped by the user, or one that fails, still closes the view at once.
- `srim.ui.tui`: `ScanState` (the counts, workers, points, throughput and ETA of a scan, from its events) and `plot` (the series and error bars of a result) are pure Python, usable without Textual; `srim.ui.tui.app.ScanApp` is the Textual app.
- **Live view in a browser:** `pysrim2 scan run --ui web` runs the scan as `--ui plain` does and serves the same app as the terminal views at `http://127.0.0.1:8000` (`--host`, `--port`; the URL is printed; needs the `[tui]` extra, which now includes `textual-serve`). Several tabs can watch one scan, and a tab opened late shows what has happened so far. `p` and `q` in the browser pause, resume and stop the scan (after the end they do nothing); every event is also written to `<scan folder>/events.jsonl`, and the requests to `control.jsonl`. After the scan ends the server stays up, with the final state on screen, until Ctrl-C (the notice `scan finished — the view stays at <URL>; press Ctrl-C to stop it` says so); `pysrim2 scan run --no-wait` stops it a few seconds after the end instead. Ctrl-C after a finished scan keeps the scan's exit code (not 130). Without `textual-serve`, or when the port is in use, `plain` runs with a notice. A `--host` other than the loopback address warns that anyone who can reach the port can pause and stop the scan: the web view has no authentication. The view also has no Origin check, so a web page open in a browser on the same machine can pause and stop the scan through the local port: use `--ui web` on trusted machines only.
- The browser view is robust: a server that exits at start (port in use) is a notice after one second and the scan goes on; a failing events file or control file is one notice (the view is turned off, or polling goes on).
- A SIGTERM (a plain `kill`) to `pysrim2 scan run --ui web` stops the scan like `q` and then ends the server and its viewers.
- Each run of the browser view starts its events file with a `run` line carrying a run id; a viewer that sees a new run (or a truncated file) starts its state again, so a tab left open never mixes two runs.
- The browser view's served command is quoted for cmd.exe on Windows and for sh elsewhere.
- The browser view stops completely at Ctrl-C (or after the grace period with `--no-wait`, or when the scan was stopped by the user): the server runs in its own process group, so an open tab's viewer is ended with it (a tab that stays open no longer leaves a server and a viewer behind), and the server and each viewer exit when the process that started them is gone.
- `srim.ui.LiveView(plot=None, every=1.0)`: `run_scan(scan, on_event=LiveView())` shows the status line and the plot of a scan in a Jupyter cell (the plot of `ScanResult.plot`: same axes and error bars; `plot="X,Y[,HUE]"`), updated at most every second and once more when the scan finishes. Each redraw runs in the scan loop: raise `every` for a big scan. It needs IPython and matplotlib (`pip install "pysrim2[plot]" ipython`; IPython is there in Jupyter already), a Jupyter session (it refuses to start in a plain script) and no widgets; a failing view warns and stops, and the scan goes on.

#### Events and other

- `srim.scan.events.event_from_dict(data)` reads an event back from its `to_dict()` or one line of `to_json()` (fields it does not know are ignored); the browser and notebook views build their state from the events this way.
- `srim.rundir.RunDir` (private working copy of SRIM, `reset()`, disk usage) and `Results.directory`.

### Fixed

- `pysrim2 run --set KEY=VALUE run.yaml` works: the path after `--set` was read as one of its values, so the command failed with a missing `run.yaml`. The path also comes in either order, including a path with `=` in its folder name (`runs/x=1/run.yaml`).

- `implant_summary` on an empty frame returns NaN values without emitting RuntimeWarnings.

- `pysrim2 run` no longer leaves an empty output folder behind when the run fails.

- The agent skill names the event-file DataFrames correctly (`results.exyz.df`, not `results.exyz`).

- `srim.plot.plot_vacancies` no longer adds the primary knock-on atoms to the vacancy total: knock-ons are not vacancies, and the plotted curve is the sum of the element columns (matching "Total Target Vacancies" in VACANCY.txt).

- `num_ions` of the output tables is SRIM's ion count rounded to the nearest integer. It was truncated, so a run of 60 ions ("Total Ions calculated = 59.93") read as 59. The raw value is kept as `metadata["ions_calculated"]`. Values normalised by it (e.g. `plot_phonons`) change slightly.

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

[Unreleased]: https://github.com/FSIBT/pysrim2/compare/v0.12.0...HEAD
[0.12.0]: https://github.com/FSIBT/pysrim2/compare/v0.11.0...v0.12.0
[0.11.0]: https://github.com/FSIBT/pysrim2/compare/v0.10.0...v0.11.0
[0.10.0]: https://github.com/FSIBT/pysrim2/compare/v0.9.0...v0.10.0
[0.9.0]: https://github.com/FSIBT/pysrim2/compare/v0.8.0...v0.9.0
[0.8.0]: https://github.com/FSIBT/pysrim2/compare/v0.7.0...v0.8.0
[0.7.0]: https://github.com/FSIBT/pysrim2/compare/v0.6.0...v0.7.0
[0.6.0]: https://github.com/FSIBT/pysrim2/compare/0421575...v0.6.0
