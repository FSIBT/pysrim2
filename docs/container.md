# Container

The image `ghcr.io/fsibt/pysrim2` contains pysrim2, SRIM-2013 and wine, so
SRIM runs the same on macOS, Windows and Linux. It is `linux/amd64` only
(Apple silicon runs it under emulation, which is slower). SRIM © SRIM.com
(James F. Ziegler) — free for non-commercial use only; `LEGAL.rtf` is in the
image at `/opt/srim/Data/LEGAL.rtf`.

`pysrim2 install image` pulls it (and `pysrim2 install runtime` installs
Podman first if you have no runtime); it then prints the run command for your
runtime.

## Run a script

Put `run.py` in a folder and run from that folder. Podman is the default
runtime:

```shell
podman run --rm --userns=keep-id:uid=1000,gid=1000 -v "$PWD:/data:Z" \
  ghcr.io/fsibt/pysrim2:main python /data/run.py
```

`/data` is the working directory. The `:Z` relabels the mount for SELinux
(Fedora, openSUSE, ...); it is harmless elsewhere. `--userns=keep-id:uid=1000,gid=1000`
makes rootless Podman map the container user (uid 1000) to your own user, so the
container can write to the mounted folder and the results in `/data` belong to
you. It needs Podman 4.3 or newer.

### Docker 29 and newer

Docker 29's built-in seccomp profile blocks the 32-bit socket system calls
that wine makes, so wine fails with `socket: Function not implemented`.
Pass the profile that ships with pysrim2, which is Docker's current default
profile with those calls allowed. `pysrim2 install image` copies it to
`<data directory>/seccomp-docker.json` and prints the line to use (or take
`srim/data/seccomp-docker.json` from the repository):

```shell
docker run --rm --security-opt seccomp=/path/to/seccomp-docker.json \
  -v "$PWD:/data" ghcr.io/fsibt/pysrim2:main python /data/run.py
```

Do not use `seccomp=unconfined`.

## Users and files

Run the image as its default user (uid 1000): do not pass `-u`, because the
wine prefix and SRIM files belong to that user. With rootless Podman use
`--userns=keep-id:uid=1000,gid=1000` (shown above), which maps uid 1000 to
your own user. With Docker on Linux the mounted folder must be writable by
uid 1000 (use a dedicated folder and `chmod a+rwx` it, or `chown 1000`).

Without that flag, rootless Podman fails with a permission error when the
script writes to `/data`.

## Outputs

SRIM writes its output files inside the container, in `/opt/srim`, which
is gone when the container exits. Copy the files you need into `/data`
before your script ends (`TRIM.copy_output_files(srim_dir(), "/data/out")`);
the bundled skill example does exactly this, see {doc}`agents`.

The scripts in [examples/scripts](https://github.com/FSIBT/pysrim2/tree/main/examples/scripts)
write their results to a local `output` folder and are meant to be run this way.

## Tags

| Tag | Meaning |
|---|---|
| `main` | the latest commit on `main` |
| `X.Y.Z` | a release, for example `0.7.0` (no leading `v`) |
| `X.Y` | the latest release of that minor series |
| `latest` | the latest release |

Pin `X.Y.Z` for reproducible runs. `pysrim2 install image` pulls the tag that
matches your installed pysrim2 version (`main` for development versions).
