# Running pysrim in docker container

> **Legacy:** this example uses the `costrouc/pysrim` Docker Hub image and
> `python3.6`. Neither is maintained, and the Dockerfiles were removed in 0.6.0.
> pysrim2 0.7.0 ships its own container image, `ghcr.io/fsibt/pysrim2`; use that
> instead. The example below is kept for reference only.

This is an example of running pysrim within the [provided docker
container](https://hub.docker.com/r/costrouc/pysrim/tags/) `costrouc/pysrim`

``` bash
cd examples/docker
docker run -v $PWD:/opt/pysrim/ \
           -v /tmp/output:/tmp/output \
           -it costrouc/pysrim sh -c "xvfb-run -a python3.6 /opt/pysrim/ni.py"
ls /tmp/output
```

This will start a docker container with a volume at `/opt/pysrim`
(with `ni.py` directory) and `/tmp/output` mounted in both the host
and src container for all srim output.

There is no loss in performance.
