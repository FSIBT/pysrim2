# Command line

The `pysrim2` command sets up and checks SRIM. It is installed with the
package (also available as `python -m srim`).

```shell
pysrim2 --version
pysrim2 install [COMPONENT ...] [--dry-run] [--yes] [--agent claude|all] [--scope user|project]
pysrim2 doctor [--json]
pysrim2 run run.yaml [--set KEY=VALUE ...] [--output DIR]
pysrim2 run-job DIR
pysrim2 import TRIM.IN [-o run.yaml]
pysrim2 clean runs [--older-than DAYS] [--dry-run]
pysrim2 scan run scan.yaml [--set KEY=VALUE ...] [--workers N] [--backend local|container]
               [--ui plot|dashboard|web|plain|json] [--output DIR] [--cache DIR | --no-cache]
pysrim2 scan prepare scan.yaml -o DIR [--jobs N] [--set KEY=VALUE ...]
pysrim2 scan collect DIR [--cache DIR | --no-cache]
pysrim2 scan clean [DIR] [--cache DIR] [--all] [--dry-run]
```

## `pysrim2 install`

Installs what pysrim2 needs to run SRIM. Without component names it picks a
default set for your platform:

| Platform | Default components |
|---|---|
| Windows | `srim`, `vb5`, `skill` |
| Linux with wine, or without a container runtime | `wine`, `srim`, `skill` |
| Linux or macOS with Podman/Docker | `image`, `skill` |
| macOS without a runtime | `runtime`, `image`, `skill` |

Components: `srim` (download SRIM-2013, verify its checksum, keep
`LEGAL.rtf` next to it), `vb5` (Windows: Visual Basic 5 runtime and OCX
controls), `wine` (Linux: wine packages and the private prefix), `runtime`
(Podman), `image` (pull `ghcr.io/fsibt/pysrim2`, see {doc}`container`) and
`skill` (see {doc}`agents`). Steps already satisfied print `[ok] ...: already
installed`.

Options:

`--dry-run`
: Print every step with its exact commands and change nothing.

`--yes`
: Run steps that need admin rights (package installs, `regsvr32`) without
  asking, and overwrite an agent skill pysrim2 did not write.

`--agent claude|all`, `--scope user|project`
: Where the agent skill goes (default `claude`, `user`).

Without `--yes`, a step that needs admin rights asks `[y/N]` on a terminal
and is skipped (with its commands printed) otherwise.

## `pysrim2 doctor`

Checks Python, SRIM (`TRIM.exe`, `LEGAL.rtf`), on Linux wine, `xvfb-run`,
an archive extractor, a downloader and the private prefix, the optional agent
skill, and (when native SRIM is not possible) the container runtime and
image. Failing checks print a `fix:` line.

`--json` prints:

```json
{
  "checks": [
    {"name": "trim-exe", "ok": true, "detail": "/home/me/.local/share/pysrim2/srim/TRIM.exe", "fix": ""},
    {"name": "wine", "ok": false, "detail": "not found", "fix": "run: pysrim2 install wine"}
  ]
}
```

Each check has `name`, `ok` (boolean), `detail` (what was found) and `fix`
(empty when `ok`).

## `pysrim2 run`

Runs TRIM from a `run.yaml` (see {doc}`run-spec`), using the SRIM install that
`pysrim2 doctor` reports. The outputs, `TRIM.IN` and a `run.yaml` with the
provenance of the run (including the seed that was used) are copied to the
`--output` directory (default `./srim-output-<UTC timestamp>`, e.g.
`srim-output-20261104T101500Z`).

`--set KEY=VALUE ...` changes values before the run (also several after one
`--set`). Keys are dotted paths into the `spec:` section, list items are
addressed by index, and values are converted by the schema:

```shell
pysrim2 run run.yaml --set ion.energy_keV=30 target.0.thickness_nm=50 number_ions=500 settings.ranges=1
```

An invalid file or `--set` is reported with the dotted key at fault and
nothing is run.

## `pysrim2 import`

Reads a `TRIM.IN` (written by SRIM or pysrim) and writes the equivalent
`run.yaml` (default `./run.yaml`; an existing file is only overwritten with an
explicit `-o`). Layer thicknesses are converted from Angstrom to nm; element
masses that differ from pysrim2's element table are kept as `mass_amu`; the
random seed in the file is kept (SRIM's own files often have 0).

## `pysrim2 clean runs`

Removes the run directories of `TRIM.run()` and `pysrim2 run` that no process uses. With
`--older-than DAYS` only those unused for that long; without it, every unused one (folders
younger than a minute are kept). `--dry-run` lists them. See {doc}`concurrency`.

## `pysrim2 run-job`

Runs the units of a job folder that are not done yet (`job.yaml`, outputs in `out/`).
It is what the container back end and a Slurm array task run. See {doc}`scans`.

## `pysrim2 scan`

Parameter scans: `run` runs a `scan.yaml` on this machine (local workers, or one container
per unit), `prepare` writes the job folders and `submit.sbatch` for a Slurm array, `collect`
builds the result once the tasks are done, and `clean` removes cache entries or a whole scan
folder. `--ui` picks the live view (`plot`, `dashboard`, `web`, `plain` or `json`; see
{doc}`scans`). Scan options: `--workers`, `--backend`, `--image` (container), `--output` (the
scan folder, default `./scan-<UTC time>`), `--cache` or `--no-cache`, `--retries` (default 1),
`--host` and `--port` (web view, default `127.0.0.1:8000`), `--plot X,Y[,HUE]` (plot view),
`--no-wait` (the views close when the scan has finished instead of staying open until q or
Ctrl-C), and `--set` (changes the scan's base run). The web view has no authentication: see the note in
{doc}`scans`.

```shell
pysrim2 scan run scan.yaml --ui plain --output my-scan
pysrim2 scan clean my-scan --dry-run
```

## Exit codes

For `run` and `import`: `0` ok, `1` SRIM failed or the output file exists, `2`
the input is invalid (spec, `--set` value or `TRIM.IN`), `130` cancelled.
For `scan` and `run-job` the codes are in {doc}`scans` (`run-job`: `0` all done, `1` some
unit failed, `2` not a job folder).
For `install` and `doctor`:

| Code | Meaning |
|---|---|
| 0 | Everything succeeded (`doctor`: all checks ok) |
| 1 | A step failed, or a `doctor` check failed |
| 2 | `install` finished but skipped a step that needs admin rights (rerun with `--yes`, or run the printed commands) |
| 130 | Cancelled with Ctrl-C (at the admin prompt or during a step) |
