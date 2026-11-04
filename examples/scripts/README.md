# Example scripts

Small, runnable scripts for the current pysrim2 API. Each one writes its results
to a local `output` folder, so it works the same natively and in the container.

| Script | What it does |
|---|---|
| `boron_in_gold_on_silicon.py [ions]` | The reference case: 20 keV boron at 7 degrees into 100 nm Au on Si. Prints mean depth (about 282 angstrom for 200 ions, seed 12345), lateral straggle and the damage peak depth. |
| `multilayer_damage.py [ions] [folder]` | Full-cascade run through a three-layer target (Au / Si / Al); writes the vacancy profile to `vacancy_profile.csv`. |
| `stopping_table.py [folder]` | SRIM's SR module: stopping power and range of He in Si from 10 keV to 1 MeV, as `stopping_table.csv`. Fast. |

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
```

## Run in the container

Run from this folder. With Podman (`:Z` relabels the mount on SELinux hosts):

```shell
podman run --rm -v "$PWD:/data:Z" ghcr.io/fsibt/pysrim2:main python /data/stopping_table.py /data/output
```

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
