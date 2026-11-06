# Example scripts

Small, runnable scripts for the current pysrim2 API. Each one writes its results
to a local `output` folder, so it works the same natively and in the container.

| Script | What it does |
|---|---|
| `boron_in_gold_on_silicon.py [ions]` | The reference case: 20 keV boron at 7 degrees into 100 nm Au on Si. Prints mean depth (about 282 angstrom for 200 ions, seed 12345), depth, lateral and radial straggling (about 146, 242 and 155 angstrom) and the damage peak depth. |
| `multilayer_damage.py [ions] [folder]` | Full-cascade run through a three-layer target (Au / Si / Al); writes the vacancy profile (`results.vacancy.df` plus a total) to `vacancy_profile.csv`. |
| `stopping_table.py [folder]` | SRIM's SR module: stopping power and range of He in Si from 10 keV to 1 MeV, as `stopping_table.csv`. Fast. |
| `run_yaml_example/` | The boron case as a `run.yaml` for `pysrim2 run run.yaml --set ion.energy_keV=30`; see its README. |
| `angle_energy_scan.py [folder]` | A scan (see the [scans page](https://pysrim2.readthedocs.io/en/latest/scans.html)): B into 1000 nm of Si at 20 and 40 keV, each at 0 and 30 degrees, 50 ions per point, two worker processes. Prints one row per point and writes `result/scan.csv` in the folder (default `output/scan`). |
| `scan.yaml` | The same scan as a file: `pysrim2 scan run scan.yaml --ui plain` (a new `scan-<UTC time>` folder, or `--output DIR`). |

`boron_in_gold_on_silicon.py` is also the example of the
[agent skill](../../skills/pysrim2/SKILL.md); a test keeps the two copies identical.

## Run natively

Install pysrim2 and SRIM once (see the
[installation guide](https://pysrim2.readthedocs.io/en/latest/installation.html)),
then run a script from any folder:

```shell
pysrim2 install
pysrim2 doctor
python boron_in_gold_on_silicon.py
python multilayer_damage.py 20
python stopping_table.py
python angle_energy_scan.py
pysrim2 scan run scan.yaml --ui plain
```

## Run in the container

Run from this folder. With Podman (`:Z` relabels the mount on SELinux hosts):

```shell
podman run --rm --userns=keep-id:uid=1000,gid=1000 -v "$PWD:/data:Z" ghcr.io/fsibt/pysrim2:main python /data/stopping_table.py /data/output
```

`--userns=keep-id:uid=1000,gid=1000` (Podman 4.3 or newer) maps the container
user to your own user, so the script can write `output/` and the results belong
to you.

With Docker 29 or newer, add the seccomp profile that `pysrim2 install image`
copies to pysrim2's data directory (or use `srim/data/seccomp-docker.json`):

```shell
docker run --rm --security-opt seccomp=/path/to/seccomp-docker.json \
  -v "$PWD:/data" ghcr.io/fsibt/pysrim2:main python /data/stopping_table.py /data/output
```

`boron_in_gold_on_silicon.py` writes to `./output` in the working directory, which
is `/data` in the container. On Linux with Docker, `/data` must be writable by
uid 1000. See the
[container page](https://pysrim2.readthedocs.io/en/latest/container.html).

SRIM © SRIM.com (James F. Ziegler) — free for non-commercial use only; see `LEGAL.rtf`.
