"""Run scan units outside the worker processes: a container per unit, and job folders.

Job folders are the unit of work for a container and for a cluster::

    <job>/job.yaml                {version: 1, units: [{name: <unit>, spec: <RunSpec dict>}, ...]}
    <job>/out/<unit>/             the SRIM outputs and run.yaml of the unit
    <job>/out/<unit>/.done        written last, when the unit succeeded
    <job>/out/<unit>/error.log    when it failed: a summary line, then the traceback

:func:`run_job` (``pysrim2 run-job DIR``) runs every unit of a job folder that has no
``.done`` marker, one after the other, and returns the number of failed units. It is
idempotent: a second run only runs what is left, so a job that was killed resumes. Extractors
never run inside a job; the parent process (or :func:`srim.scan.collect`) runs them.

:class:`ContainerRunner` is the runner of ``run_scan(..., backend="container")``. For each
unit it makes a job folder with one unit in a private temporary folder (mode 0o700),
starts one container of the pysrim2 image on it with ``pysrim2 run-job /data/job``, and
returns the unit's outputs; the executor copies them. Podman maps the host user to uid 1000
(``--userns=keep-id``) and relabels the mount (``:Z``); Docker gets the seccomp profile that
wine needs and a world-writable job folder, because the container user is uid 1000 whatever
the host user is. The runtime's output goes to a file, never to a pipe (its daemon keeps
pipes open). A container is removed with ``--rm``. If the worker is told to stop (or its
parent dies) while its container runs, the worker runs ``<runtime> rm -f <name>`` (at most
30 s, detached from the worker's process group so that a SIGKILL after the grace period does
not stop it) and removes the job folder, so no container keeps running. On Windows the stop
ends the worker's process tree at once, which cannot run that cleanup: a container started
then may keep running until its SRIM run ends (``--rm`` removes it then).
"""

import contextlib
import logging
import os
import re
import shutil
import subprocess
import tempfile
import traceback
import uuid
from collections.abc import Callable
from pathlib import Path

import yaml

from ..output import Results
from ..rundir import remove_tree
from ..spec import RunSpec, SpecError
from .executor import _summary, set_unit_abort

log = logging.getLogger(__name__)

JOB_VERSION = 1
JOB_FILE = "job.yaml"
OUT_DIR = "out"
DONE = ".done"
ERROR_LOG = "error.log"
#: where the job folder is mounted in the container
CONTAINER_JOB = "/data/job"
#: the unit of the job folder a container runs
CONTAINER_UNIT = "run"
#: seconds the container removal may take when a worker is stopped
ABORT_SECONDS = 30.0
#: the uid of the user the pysrim2 image runs as (see the Dockerfile)
IMAGE_UID = 1000
_UNIT_NAME = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,127}")


def write_job(job_dir, units) -> Path:
    """Write ``job.yaml`` for ``units``, a list of ``(name, RunSpec)``; returns the folder.

    The folder is created if needed. Unit names are folder names (letters, digits, ``_``,
    ``.`` and ``-``; no leading dot).
    """
    job = Path(job_dir)
    job.mkdir(parents=True, exist_ok=True)
    document = {
        "version": JOB_VERSION,
        "units": [{"name": name, "spec": spec.to_dict()} for name, spec in units],
    }
    text = yaml.safe_dump(document, sort_keys=False, default_flow_style=False)
    (job / JOB_FILE).write_text(text, encoding="utf-8")
    return job


def read_job(job_dir) -> list[tuple[str, RunSpec]]:
    """The units of a job folder, as ``(name, RunSpec)`` in order. Raises :class:`SpecError`
    for a folder that is not a job (no ``job.yaml``, another version, a bad unit or spec)."""
    job = Path(job_dir)
    path = job / JOB_FILE
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        raise SpecError(f"{job}: no {JOB_FILE}; this is not a job folder") from None
    try:
        document = yaml.safe_load(text)
    except yaml.YAMLError as exc:
        raise SpecError(f"{path}: not valid YAML: {exc}") from None
    if not isinstance(document, dict) or document.get("version") != JOB_VERSION:
        raise SpecError(f"{path}: expected a mapping with version: {JOB_VERSION}")
    units = document.get("units")
    if not isinstance(units, list) or not units:
        raise SpecError(f"{path}: units must be a non-empty list")
    out: list[tuple[str, RunSpec]] = []
    seen: set[str] = set()
    for i, unit in enumerate(units):
        where = f"{path}: units[{i}]"
        if not isinstance(unit, dict) or set(unit) != {"name", "spec"}:
            raise SpecError(f"{where} needs exactly the keys name and spec")
        name = unit["name"]
        if not isinstance(name, str) or not _UNIT_NAME.fullmatch(name):
            raise SpecError(f"{where}: the name {name!r} is not a plain folder name")
        if name in seen:
            raise SpecError(f"{where}: the unit {name!r} is listed twice")
        if not isinstance(unit["spec"], dict):
            raise SpecError(f"{where}: spec must be a mapping")
        seen.add(name)
        out.append((name, RunSpec.from_dict(unit["spec"])))
    return out


# -- running a job ------------------------------------------------------------------------------


def run_job(job_dir, *, runner: Callable | None = None, report: Callable | None = None) -> int:
    """Run the units of a job folder that are not done; returns the number of failed units.

    Parameters
    ----------
    job_dir : path-like
        the job folder (its ``job.yaml`` lists the units)
    runner : callable, optional
        ``runner(spec) -> Results`` in place of SRIM, as for :func:`srim.scan.run_scan`
        (for tests). Default: ``TRIM.from_spec(spec).run(output_dir=...)``.
    report : callable, optional
        ``report(line)`` for one line per unit (``print`` in the command line)

    Raises
    ------
    SpecError
        when ``job_dir`` is not a valid job folder (nothing is run then)
    """
    job = Path(job_dir)
    units = read_job(job)
    failed = 0
    for name, spec in units:
        out = job / OUT_DIR / name
        if (out / DONE).is_file():
            _tell(report, f"{name}: done before")
            continue
        shutil.rmtree(out, ignore_errors=True)
        summary = None
        try:
            _run_unit(spec, out, runner)
        except Exception as exc:
            summary = _summary(exc)
            _write_error(out, summary, traceback.format_exc())
            failed += 1
        _open_to_host(job, out)
        if summary is None:
            (out / DONE).write_text("done\n", encoding="utf-8")  # the last thing written
            _tell(report, f"{name}: done")
        else:
            _tell(report, f"{name}: failed: {summary}")
    return failed


def _tell(report, line):
    if report is not None:
        report(line)


def _run_unit(spec: RunSpec, out: Path, runner):
    """Run one unit; its outputs end up in ``out`` (created by the run)."""
    if runner is None:
        from ..srim import TRIM

        TRIM.from_spec(spec).run(output_dir=out).close()
        return
    results = runner(spec)
    try:
        shutil.copytree(results.directory, out, dirs_exist_ok=True)
    finally:
        results.close()


def _write_error(out: Path, summary: str, tb: str):
    out.mkdir(parents=True, exist_ok=True)
    with open(out / ERROR_LOG, "w", encoding="utf-8") as f:
        f.write(f"{summary}\n{tb}")


def _open_to_host(job: Path, out: Path):
    """When the job folder is world-writable (Docker: the container user is not the host
    user), make the unit's folders writable too, so the host can remove what the container
    made. Folder modes only; nothing is made readable or writable that was not before."""
    if not (os.stat(job).st_mode & 0o002) or not out.is_dir():
        return
    for dirpath, dirnames, _filenames in os.walk(out):
        for name in [dirpath, *(os.path.join(dirpath, d) for d in dirnames)]:
            with contextlib.suppress(OSError):
                os.chmod(name, os.stat(name).st_mode | 0o777)


# -- the container back end ---------------------------------------------------------------------


class ContainerRunner:
    """The runner of the container back end: ``runner(spec) -> Results``, one container per
    call (see the module docstring). Picklable, so the worker processes can have it.

    Parameters
    ----------
    runtime : :obj:`str`
        ``"podman"`` or ``"docker"`` (the command name, or a path to it)
    image : :obj:`str`
        the image reference, e.g. ``ghcr.io/fsibt/pysrim2:main``
    """

    def __init__(self, runtime: str, image: str):
        self.runtime = runtime
        self.image = image

    def __call__(self, spec: RunSpec) -> Results:
        parent = Path(tempfile.mkdtemp(prefix="pysrim2-job-"))  # mode 0o700
        name = f"pysrim2-{uuid.uuid4().hex}"
        try:
            job = parent / "job"
            write_job(job, [(CONTAINER_UNIT, spec)])
            if self._is_docker():
                os.chmod(job, 0o777)
            set_unit_abort(lambda: self._abort(name, parent))
            try:
                code = self._run_container(name, job, parent)
            finally:
                set_unit_abort(None)
            unit = job / OUT_DIR / CONTAINER_UNIT
            if not (unit / DONE).is_file():
                raise RuntimeError(self._failure(code, unit, parent))
            results = Results(str(unit))
            results._own_run_dir(parent)  # results.close() removes the job folder
            return results
        except BaseException:
            remove_tree(parent)
            raise

    def _is_docker(self) -> bool:
        return Path(self.runtime).name.lower().startswith("docker")

    def command(self, name: str, job: Path) -> list[str]:
        """The runtime's command line for one unit (``name`` names the container)."""
        mount = f"{job}:{CONTAINER_JOB}"
        run = [self.runtime, "run", "--rm", "--name", name]
        if self._is_docker():
            from ..installer.container import seccomp_profile_path

            profile = seccomp_profile_path()
            run += ["--security-opt", f"seccomp={profile}", "-v", mount]
        else:
            run += [f"--userns=keep-id:uid={IMAGE_UID},gid={IMAGE_UID}", "-v", f"{mount}:Z"]
        return [*run, self.image, "pysrim2", "run-job", CONTAINER_JOB]

    def _run_container(self, name: str, job: Path, parent: Path) -> int:
        output = parent / "runtime.log"
        with open(output, "w", encoding="utf-8") as out_file:
            done = subprocess.run(
                self.command(name, job),
                stdin=subprocess.DEVNULL,
                stdout=out_file,
                stderr=subprocess.STDOUT,
                check=False,
            )
        return done.returncode

    def _failure(self, code: int, unit: Path, parent: Path) -> str:
        error = unit / ERROR_LOG
        if error.is_file():
            first = error.read_text(encoding="utf-8").splitlines()
            return f"the unit failed in its container ({first[0] if first else 'no message'})"
        tail = _tail(parent / "runtime.log")
        return f"container exited with code {code} and left no outputs" + (
            f"; its last output: {tail}" if tail else ""
        )

    def _abort(self, name: str, parent: Path):
        """The worker is stopped: remove the container and the job folder."""
        self._remove(name)
        remove_tree(parent)

    def _remove(self, name: str):
        with tempfile.TemporaryFile() as output:
            try:
                subprocess.run(
                    [self.runtime, "rm", "-f", name],
                    stdin=subprocess.DEVNULL,
                    stdout=output,
                    stderr=subprocess.STDOUT,
                    timeout=ABORT_SECONDS,
                    check=False,
                    start_new_session=os.name == "posix",
                )
            except (OSError, subprocess.SubprocessError) as exc:
                log.warning("could not remove container %s: %s", name, exc)


def _tail(path: Path, lines: int = 3) -> str:
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return ""
    return " | ".join(line for line in text.splitlines()[-lines:] if line.strip())
