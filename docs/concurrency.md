# Concurrent runs

Since pysrim2 0.9.0 several runs can happen at once, from one script, from several
scripts, from `pysrim2 run`, from a scan, or from several users on one machine. This page
explains how that works, what is on disk, and what changed for code written for 0.8.

The rule behind it: a run never writes into the installed SRIM directory. Each run works
in its own run directory, and the locks below protect the few things that are shared.

## Isolated run directories

`TRIM.run()` and `SR.run()` (and `pysrim2 run`, and each worker of a scan) make a
**run directory** for each run. It is a copy of the SRIM install, arranged so that it
costs little disk space:

- read-only files (`TRIM.exe`, the large data tables, the documentation) are **hard
  links** to the installed files;
- what SRIM writes or what differs per run is a **real copy**: `TRIM.IN`, `TRIMAUTO`,
  `TRIM.dat`, `TRIM 2.dat`, `SR.IN`, `Data/TRIM.cfg`, `Data/SRIMRun.dat`, the whole
  `SR Module/` folder, and every other file below 16 KiB;
- SRIM's outputs (`RANGE.txt`, `VACANCY.txt`, ...) are created by the run. Output files
  of the installed directory are never copied.

A hard link needs the run directory and the install on the same file system. Where that is
not possible, each run gets a full copy and pysrim2 warns once (a `RuntimeWarning`); set
`work_dir=` to a folder on the install's file system to avoid it.

Where the run directories are:

- the default is `runs/` in pysrim2's data directory. The data directory is `$PYSRIM2_HOME`
  if set; otherwise `~/Library/Application Support/pysrim2` on macOS,
  `%LOCALAPPDATA%\pysrim2` on Windows, and `${XDG_DATA_HOME:-~/.local/share}/pysrim2` on
  Linux;
- `TRIM.run(work_dir=...)` chooses another folder for one run.

A run directory is named `run-<random>`. It holds `srim/` (the SRIM copy) and, on Linux with
wine, `wine/` (the run's own wine prefix, see below).

### What happens to a run directory

- `TRIM.run(output_dir=DIR)` copies the outputs and `run.yaml` to `DIR`, reads the results
  from there, and removes the run directory (unless `keep=True`).
- Without `output_dir`, the run directory stays, and `Results.directory` is its path. Read
  the outputs from there or from the returned `Results`.
- `Results.close()`, or leaving a `with trim.run() as results:` block, removes the run
  directory at once. A `Results` that is still open keeps its directory alive.
- A failed run keeps its directory for debugging, and its error message names the path.
- A run directory that nobody uses is deleted after `PYSRIM2_RUN_RETENTION_DAYS` days
  (default 7; `0` keeps them forever). Each `TRIM.run()` removes the expired ones before
  it starts, and so does a scan that runs on local workers. Folders younger than a minute are never removed.
- `pysrim2 clean runs` removes every run directory that is not in use, at once:

```shell
pysrim2 clean runs --dry-run          # list what would be removed
pysrim2 clean runs                    # remove unused run directories
pysrim2 clean runs --older-than 2     # only those unused for 2 days or more
```

- `pysrim2 doctor` reports how many run directories there are and how much space they use.

Scans use one run directory per worker, reused for all its units, and removed when the
worker exits (see {doc}`scans`).

## Wine on Linux: one prefix per run

Wine keeps its state in a prefix. pysrim2 keeps its own prefix, `data_dir()/wine` by
default (`$PYSRIM2_WINEPREFIX` changes it). It never reads or writes your `~/.wine` or
`$WINEPREFIX`.

When wine is installed, each isolated run gets a **copy of that prefix** (`wine/` in its run directory), so a run
that fails is cleaned up with `wineserver -k` for that run only. Other runs keep going.
The prefix copy is done with hard links for the large binaries and real copies for what
wine may write (registry, user profile, temporary files).

## Locks

Every lock is a file lock: two processes that take the same lock wait for each other (or
fail at once, when they ask not to wait). A lock is released when its process ends, so a
killed process does not leave a lock behind.

| Lock file | Taken by | Effect |
|---|---|---|
| `<data dir>/locks/<hash>.lock` for an install | an in-place run (`run(srim_directory=...)`), exclusive; an isolated copy, shared | in-place runs in one SRIM directory take turns; a copy waits while an in-place run is writing |
| `run-*/.pysrim2-run.lock` | the run that makes a run directory (exclusive), and each open `Results` that reads it (shared) | `clean runs` and the retention never remove a run directory in use |
| `<scan folder>/.scan.lock` | a running scan, and `pysrim2 scan clean` | a second scan in the same folder fails at once (exit 2, or `srim.scan.ScanBusy`), before it writes anything; `scan clean` refuses to remove what a scan is writing |
| `<cache>/locks/<key>.lock` | the run that computes a cache key | two scans that need the same run compute it once: the second waits for the first, then uses the result |

Each scan folder has a marker file, `.pysrim2-scan`. `pysrim2 scan clean DIR --all`
removes a folder only if it has that marker, so it cannot remove a folder that is not a
scan.

## Legacy in-place runs

`TRIM.run(srim_directory=...)` (and `SR.run(srim_directory=...)`) still runs in the given
SRIM directory, as 0.8 did. Such runs take the exclusive lock for that directory, so two of
them take turns instead of overwriting each other's files. `lock_timeout=` limits the wait
(default: wait; a message appears after 5 seconds). The lock file lives in pysrim2's data
directory: nothing is written to the install.

## Migration from 0.8

**Behaviour change.** `TRIM.run()` and `SR.run()` now run in a private run directory, so the
outputs are no longer in the installed SRIM directory. Code that read the outputs from
there after a run gets old or missing files. pysrim2 0.9.0 warns once (a `UserWarning`) when
it sees this.

Old code:

```python
from srim import TRIM
from srim.paths import srim_dir
from srim.output import Results

trim = TRIM(target, ion, number_ions=200, calculation=1, ranges=1)
trim.run()
results = Results(srim_dir())  # the installed directory: not written any more
TRIM.copy_output_files(srim_dir(), "out")
```

New code, reading the returned results:

```python
results = trim.run()  # results.directory is the run directory
TRIM.copy_output_files(results.directory, "out")
```

New code, with the outputs in a folder of your choice (the run directory is removed):

```python
results = trim.run(output_dir="out")  # outputs and run.yaml in ./out
print(results.range3d.df.head())
```

If the code must keep writing into the installed directory, say so explicitly. This is the
old behaviour, now under the lock:

```python
results = trim.run(srim_directory=srim_dir())
```

Things that changed with it:

- Run directories are deleted after 7 days when unused. A path you keep (such as
  `results.directory`) can disappear later. Copy what you need, or use `output_dir=` or
  `keep=True`.
- `run(work_dir=...)` and `run(run_dir=...)` choose where the run directory is and let a
  worker reuse one.
- `pysrim2 run run.yaml` already writes its outputs to `--output` (default
  `./srim-output-<UTC time>`), so the command line needs no change.

## Several scans and one cache

By default each scan keeps its cache in its own folder, so two scans in two folders reuse
nothing. To share the runs between scans, use one cache folder:

```shell
export PYSRIM2_CACHE=~/pysrim-cache          # every scan that does not name a cache
pysrim2 scan run scan-a.yaml --ui plain
pysrim2 scan run scan-b.yaml --ui plain      # reuses the runs of scan-a it has in common
```

or name it per command with `--cache DIR` (or `cache=` in Python). The cache is safe to share
at the same time: each run is computed under its key's lock, so two scans never compute the
same run at once, and an entry is written atomically (a reader never sees half of it).

Clear a shared cache with `pysrim2 scan clean --cache DIR`. Entries that a running scan is
using are kept. The cache checks the SRIM build of each entry: an entry made with another
build is not used.

See {doc}`scans` for the cache's key and layout, and {doc}`cli` for the commands.
