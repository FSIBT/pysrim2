"""Module for automating srim calculations"""

import contextlib
import errno
import os
import random
import re
import shutil
import socket
import subprocess
import sys
import tempfile
import warnings
from datetime import UTC, datetime

from . import _xvfb
from ._lock import FileLock
from .core import Element, Ion, Layer, Target
from .core.utils import (
    check_input,
    is_one_to_eight,
    is_one_to_seven,
    is_positive,
    is_quoteless,
    is_srim_degrees,
    is_zero_or_one,
    is_zero_to_five,
    is_zero_to_two,
)
from .input import AutoTRIM, SRInput, TRIMInput
from .installer.wine import XVFB_ARGS, _kill_wineserver, wine_env
from .output import Results, SRResults
from .paths import srim_dir, wine_prefix
from .rundir import (
    RunDir,
    install_lock_path,
    note_isolated_run,
    prune_runs,
    runs_base,
    warn_if_stale_install,
)
from .spec import (
    ALWAYS_SETTINGS,
    SCHEMA_VERSION,
    SETTINGS_SCHEMA,
    A_to_nm,
    ElementSpec,
    IonSpec,
    LayerSpec,
    RunSpec,
    eV_to_keV,
    keV_to_eV,
    nm_to_A,
)


def _launch_command(executable):
    """Build the command line that starts a SRIM Windows executable.

    Uses ``wine`` when it is on the PATH (needed on linux and osx),
    wrapping it in ``xvfb-run`` when that is also available so the
    graphical SRIM window does not need a display. Otherwise runs the
    executable directly.

    Parameters
    ----------
    executable : str
        Name of the executable, relative to the SRIM directory (e.g. ``TRIM.exe``)

    Returns
    -------
    list[str]
        Argument list suitable for ``subprocess.check_call``
    """
    exe = os.path.join(".", executable)
    if shutil.which("wine"):
        if shutil.which("xvfb-run"):
            return ["xvfb-run", *XVFB_ARGS, "wine", exe]
        return ["wine", exe]
    return [exe]


# Harmless lines from xvfb and Mesa/EGL that real Linux machines print at SRIM exit.
XVFB_NOISE = re.compile(
    r"X connection to :\d+ broken|MESA-EGL|libEGL warning|pci id for fd|MESA: error: ZINK"
)


class SRIMProcessError(subprocess.CalledProcessError):
    """SRIM exited with an error; ``str()`` includes what it wrote to stderr."""

    def __str__(self):
        text = super().__str__()
        return f"{text}\n{self.stderr}" if self.stderr else text


def _run_srim(executable, prefix=None, cwd=None):
    """Run a SRIM executable and wait for it.

    ``prefix`` is the run's private wine prefix (see :class:`srim.rundir.RunDir`); it
    replaces pysrim2's shared prefix, and if the run fails only that prefix's
    wineserver is stopped. ``cwd`` is the directory to run in (the process's own
    working directory is never changed).

    Under ``xvfb-run`` every run claims its own display number (``-n``), the child's
    stderr is captured so xvfb's harmless "X connection to :N broken" and Mesa/EGL
    warning lines are dropped from stdout and stderr; other lines are passed on to
    ``sys.stdout``/``sys.stderr`` after the run (stderr is included in the error if SRIM
    fails). A failure of the X server to start is retried up to three times. Without
    ``xvfb-run`` (Windows) the child is run unchanged.
    """
    # An absolute path: on Windows a relative executable is looked up from the parent's
    # working directory, not from ``cwd``.
    command = _launch_command(os.path.join(os.path.abspath(cwd), executable) if cwd else executable)
    env = _launch_env(prefix)
    try:
        _run_command(command, env, cwd)
    except BaseException:
        if prefix is not None:
            _kill_wineserver(env)
        raise


def _run_command(command, env, cwd=None):
    if command[0] != "xvfb-run":
        subprocess.check_call(command, env=env, cwd=cwd)
        return
    held = []
    try:
        number = _xvfb.FIRST_DISPLAY
        for attempt in range(_xvfb.MAX_START_ATTEMPTS):
            if attempt:
                _xvfb.backoff(attempt)
            number, lock = _xvfb.claim_display(number)
            held.append(lock)  # keep failed numbers claimed until we are done
            try:
                _run_xvfb(_xvfb.with_display(command, number), env, cwd)
                return
            except SRIMProcessError as exc:
                if attempt == _xvfb.MAX_START_ATTEMPTS - 1 or not _xvfb.is_startup_failure(
                    exc.stderr
                ):
                    raise
            number += 1
    finally:
        for lock in held:
            lock.release()


def _run_xvfb(command, env, cwd):
    # Output goes to temp files, not pipes: wine's daemonised wineserver would
    # keep a pipe open and block us. It is shown only after the run ends, not live.
    with tempfile.TemporaryFile() as out_log, tempfile.TemporaryFile() as err_log:
        done = subprocess.run(command, env=env, cwd=cwd, stdout=out_log, stderr=err_log)
        out_log.seek(0)
        err_log.seek(0)
        out_text = out_log.read().decode(errors="replace")
        err_text = err_log.read().decode(errors="replace")
    kept_out = _drop_noise(out_text)
    kept_err = _drop_noise(err_text)
    if done.returncode:
        raise SRIMProcessError(done.returncode, command, stderr=kept_err.strip() or None)
    if kept_out:
        sys.stdout.write(kept_out)
    if kept_err:
        sys.stderr.write(kept_err)


def _drop_noise(text):
    return "".join(line for line in text.splitlines(keepends=True) if not XVFB_NOISE.search(line))


def _launch_env(prefix=None):
    """Return the environment for the SRIM child process.

    With ``wine`` on the PATH this points wine at ``prefix`` (a run's private
    clone) or else at pysrim2's private prefix (never the user's ``$WINEPREFIX``
    or ``~/.wine``), in the child's environment only. Otherwise it is a plain
    copy of ``os.environ``.
    """
    if shutil.which("wine"):
        env = wine_env(prefix if prefix is not None else wine_prefix())
        env.setdefault("EGL_LOG_LEVEL", "fatal")
        env.setdefault("LIBGL_DEBUG", "quiet")
        return env
    return dict(os.environ)


class TRIMSettings:
    """TRIM Settings

    This object can construct all options available when running a TRIM calculation.

    Parameters
    ----------
    description : :obj:`str`, optional
       A name to give calculation. Has no effect on the actual
       calculation.
    reminders : :obj:`str`, optional
       TODO: could not find description. default 0
    autosave : :obj:`int`, optional
       save calculations after every `autosave` steps. default 0 will
       not autosave except at end
    plot_mode : :obj:`int`, optional
       Default 5.
       (0) ion distribution with recoils projected on y-plane
       (1) ion distribution with recoils projected on z-plane
       (2) ion distribution without recoils projected on y-plane
       (3) transverse plot of ions + recoil cascades, yz-plane
       (4) all four (0-3) on one screen
       (5) no graphics (default and at least 5X faster than others)
    plot_xmin : :obj:`float`, optional
       minimum x depth to plot only really matters if ``plot_mode``
       between 0-4. Default 0.0.
    plot_xmax : :obj:`float`, optional
       maximum x depth to plot only really matters if ``plot_mode``
       between 0-4. Default 0.0.
    ranges : :obj:`bool`, optional
       whether include ``RANGES.txt``, ``RANGE_3D.txt`` to output
       files. Default (0) False
    backscattered : :obj:`bool`, optional
       whether include ``BACKSCAT.txt`` to output files. Default (0)
       False
    transmit : :obj:`bool`, optional
       whether include ``TRANSMIT.txt`` to output files. Default (0)
       False
    sputtered : :obj:`bool`, optional
       whether include ``SPUTTER.txt`` to output files. Default (0)
       False
    collisions : :obj:`bool`, optional
       whether include ``COLLISON.txt`` to output files. Yes they did
       mispell collisions. Default (0) False
    exyz : int
       increment in eV to use for ``EXYZ.txt`` file. Default (0)
    angle_ions : :obj:`float`, optional
       angle of incidence of the ion with respect to the target
       surface. Default (0) perpendicular to the target surface along
       x-axis. Values 0 - 89.9.
    bragg_correction : :obj:`float`, optional
       bragg correction to stopping. Default (0) no correction
    random_seed : :obj:`int`, optional
       a random seed to start calculation with. Default random integer
       between 0 and 100,000. Thus all calculations by default are random.
    version : :obj:`int`, optional
       SRIM-2008 or SRIM-2008 so not really much choice. Default (0)

    Notes
    -----
        This class should never explicitely created. Instead set as
        kwargs in :class:`srim.srim.TRIM`
    """

    def __init__(self, **kwargs):
        """Initialize settings for a TRIM running"""
        self._settings = {
            "description": check_input(str, is_quoteless, kwargs.get("description", "pysrim run")),
            "reminders": check_input(int, is_zero_or_one, kwargs.get("reminders", 0)),
            "autosave": check_input(int, is_positive, kwargs.get("autosave", 0)),
            "plot_mode": check_input(int, is_zero_to_five, kwargs.get("plot_mode", 5)),
            "plot_xmin": check_input(float, is_positive, kwargs.get("plot_xmin", 0.0)),
            "plot_xmax": check_input(float, is_positive, kwargs.get("plot_xmax", 0.0)),
            "ranges": check_input(int, is_zero_or_one, kwargs.get("ranges", 0)),
            "backscattered": check_input(int, is_zero_or_one, kwargs.get("backscattered", 0)),
            "transmit": check_input(int, is_zero_or_one, kwargs.get("transmit", 0)),
            "sputtered": check_input(int, is_zero_or_one, kwargs.get("sputtered", 0)),
            "collisions": check_input(int, is_zero_to_two, kwargs.get("collisions", 0)),
            "exyz": check_input(int, is_positive, kwargs.get("exyz", 0)),
            "angle_ions": check_input(float, is_srim_degrees, kwargs.get("angle_ions", 0.0)),
            "bragg_correction": float(
                kwargs.get("bragg_correction", 1.0)
            ),  # TODO: Not sure what correct values are
            "random_seed": check_input(
                int, is_positive, kwargs.get("random_seed", random.randint(0, 100000))
            ),
            "version": check_input(int, is_zero_or_one, kwargs.get("version", 0)),
        }

        if self.plot_xmin > self.plot_xmax:
            raise ValueError("xmin must be <= xmax")

    def __getattr__(self, attr):
        return self._settings[attr]


TRIM_OUTPUT_FILES = (
    "TRIM.IN",
    "PHONON.txt",
    "E2RECOIL.txt",
    "IONIZ.txt",
    "LATERAL.txt",
    "NOVAC.txt",
    "RANGE.txt",
    "VACANCY.txt",
    "COLLISON.txt",
    "BACKSCAT.txt",
    "SPUTTER.txt",
    "RANGE_3D.txt",
    "TRANSMIT.txt",
    "TRIMOUT.txt",
    "TDATA.txt",
    "EXYZ.txt",
)


class SRIMRunError(RuntimeError):
    """SRIM exited without writing its main output file."""


def _remove_stale(directory, names, subdirectory=None):
    """Delete output files of an earlier run so a crash cannot return old results."""
    for name in names:
        paths = [os.path.join(directory, name)]
        if subdirectory:
            paths.append(os.path.join(directory, subdirectory, name))
        for path in paths:
            if os.path.isfile(path):
                os.remove(path)


def _require_output(directory, name):
    if not os.path.isfile(os.path.join(directory, name)):
        raise SRIMRunError(
            f"SRIM finished but did not write {name} in {directory}; the calculation probably crashed"
        )


def _copy_outputs(src_directory, dest_directory, names, subdirectory=None):
    """Copy the output files ``names`` (from ``src_directory`` or its ``subdirectory``) to
    ``dest_directory``, flat; files of an earlier run there are removed first."""
    os.makedirs(dest_directory, exist_ok=True)
    _remove_stale(dest_directory, names, subdirectory)
    for name in names:
        for candidate in (
            os.path.join(src_directory, name),
            os.path.join(src_directory, subdirectory, name) if subdirectory else None,
        ):
            if candidate and os.path.isfile(candidate):
                shutil.copy2(candidate, os.path.join(dest_directory, name))
                break


def _managed_run(
    execute,
    collect,
    subdirectory,
    srim_directory,
    work_dir,
    output_dir,
    keep,
    lock_timeout,
    run_dir,
    finish,
):
    """Run ``execute(srim_root, wine_prefix)`` isolated (default) or in place (locked).

    Returns ``finish(value, directory)``: ``value`` is what ``execute`` returned and
    ``directory`` is where the results are to be read from (``subdirectory`` of the run
    directory, or ``output_dir`` after ``collect(that_directory, output_dir)``). In an
    in-place run ``finish`` is called while the lock is still held.
    """
    if output_dir is not None:
        output_dir = os.path.abspath(output_dir)

    def locate(root):
        return os.path.join(root, subdirectory) if subdirectory else root

    if srim_directory is not None:
        if work_dir is not None or run_dir is not None:
            raise ValueError("work_dir= and run_dir= cannot be combined with srim_directory=")
        root = os.path.abspath(srim_directory)
        # Legacy in-place run: take turns with other runs in this directory.
        lock = FileLock(install_lock_path(root), timeout=lock_timeout)
        try:
            install_lock_path(root).parent.mkdir(parents=True, exist_ok=True)
            lock.acquire()
        except OSError as exc:
            if isinstance(exc, TimeoutError) or not (
                isinstance(exc, PermissionError) or exc.errno == errno.EROFS
            ):
                raise
            raise PermissionError(
                f"cannot create the lock file {lock.path}: pysrim2's data directory is not "
                "writable (set PYSRIM2_HOME to a writable folder). Omit srim_directory= to run "
                "in an isolated copy (the default)."
            ) from exc
        try:
            value = execute(root, None)
            where = locate(root)
            if output_dir is not None:
                collect(where, output_dir)
                where = output_dir
            return finish(value, where)
        finally:
            lock.release()

    if run_dir is not None and work_dir is not None:
        raise ValueError("work_dir= cannot be combined with run_dir=")
    owned = run_dir is None
    if owned:
        base = work_dir if work_dir is not None else runs_base()
        with contextlib.suppress(Exception):  # tidying must never break a run
            prune_runs(base)
        rd = RunDir.create(work_dir, source=srim_dir())
    else:
        rd = run_dir
    try:
        if not owned:
            rd.reset()
        value = execute(str(rd.path), rd.wine_prefix)
        where = locate(str(rd.path))
        if output_dir is not None:
            collect(where, output_dir)
            where = output_dir
    except BaseException as exc:
        rd.stop_wine()  # only this run's wineserver
        if owned:
            rd.release_lock()  # a failed run's directory is pruned after the retention time
        exc.add_note(f"the run directory was kept for debugging: {rd.path}")
        raise
    if owned:
        note_isolated_run()
        if output_dir is not None and not keep:
            rd.cleanup()
            return finish(value, where)
        rd.release_wine()
        rd.release_lock()
        results = finish(value, where)
        if output_dir is None:
            results._own_run_dir(rd.root)  # kept alive until results.close() deletes it
        return results
    return finish(value, where)


class TRIM:
    """Automate TRIM Calculations

    Parameters
    ----------
    target : :class:`srim.core.target.Target`
        constructed target for TRIM calculation
    ion : :class:`srim.core.ion.Ion`
        constructed ion for TRIM calculation
    calculation : :obj:`int`, optional
        Default 1 quick KP calculation
        (1) Ion Distribution and Quick Calculation of Damage (quick KP)
        (2) Detailed Calculation with full Damage Cascades (full cascades)
        (3) Monolayer Collision Steps / Surface Sputtering
        (4) Ions with specific energy/angle/depth (quick KP damage) using TRIM.DAT
        (5) Ions with specific energy/angle/depth (full cascades) using TRIM.DAT
        (6) Recoil cascades from neutrons, etc. (full cascades) using TRIM.DAT
        (7) Recoil cascades and monolayer steps (full cascades) using TRIM.DAT
        (8) Recoil cascades from neutrons, etc. (quick KP damage) using TRIM.DAT
    number_ions : :obj:`int`, optional
        number of ions that you want to simulate. Default 1000. A lot
        better than the 99999 default in TRIM...
    kwargs :
        See :class:`srim.srim.TRIMSettings` for available TRIM
        options. There are many and none are required defaults are
        appropriate for most cases.

    Notes
    -----
        If you are doing a simulation with over 1,000 ions it is
        recomended to split the calculaion into several smaller
        calculations. TRIM has been known to unexpectedly crash mainly
        due to memory usage.
    """

    def __init__(self, target, ion, calculation=1, number_ions=1000, **kwargs):
        """Initialize TRIM calcualtion"""
        self.settings = TRIMSettings(**kwargs)
        self.calculation = check_input(int, is_one_to_seven, calculation)
        self.number_ions = check_input(int, is_positive, number_ions)
        self.target = target
        self.ion = ion

    def _write_input_files(self, directory="."):
        """Write necissary TRIM input files for calculation into ``directory``"""
        AutoTRIM().write(directory)
        TRIMInput(self).write(directory)

    def to_spec(self):
        """Describe this calculation as a :class:`srim.spec.RunSpec`.

        The random seed is always recorded (when none was given, the one
        :class:`TRIMSettings` picked), so running the spec again gives the same
        ``TRIM.IN``. ``settings`` only holds values that differ from the defaults, plus
        ``description`` and ``ranges``.
        """
        st = self.settings
        settings = {}
        for key, (_, default) in SETTINGS_SCHEMA.items():
            value = getattr(st, key)
            if value != default or key in ALWAYS_SETTINGS:
                settings[key] = value
        layers = []
        for layer in self.target.layers:
            elements = []
            for element, props in layer.elements.items():
                db_mass = Element(element.symbol).mass
                elements.append(
                    ElementSpec(
                        element.symbol,
                        props["stoich"],
                        props["E_d"],
                        props["lattice"],
                        props["surface"],
                        None if element.mass == db_mass else element.mass,
                    )
                )
            layers.append(
                LayerSpec(layer.name, A_to_nm(layer.width), layer.density, layer.phase, elements)
            )
        ion = IonSpec(self.ion.symbol, self.ion.mass, eV_to_keV(self.ion.energy), st.angle_ions)
        return RunSpec(
            SCHEMA_VERSION,
            ion,
            layers,
            settings,
            self.number_ions,
            self.calculation,
            st.random_seed,
        )

    @classmethod
    def from_spec(cls, spec):
        """Build a :class:`TRIM` from a :class:`srim.spec.RunSpec` or its ``dict`` form.

        ``to_spec`` followed by ``from_spec`` writes a byte-identical ``TRIM.IN``.
        """
        if not isinstance(spec, RunSpec):
            spec = RunSpec.from_dict(spec)
        else:
            spec.validate()
        layers = []
        for layer_spec in spec.target:
            elements = {}
            for e in layer_spec.elements:
                elements[Element(e.symbol, e.mass_amu)] = {
                    "stoich": e.stoich,
                    "E_d": e.displacement_eV,
                    "lattice": e.lattice_eV,
                    "surface": e.surface_eV,
                }
            layer = Layer(
                elements,
                layer_spec.density_g_cm3,
                nm_to_A(layer_spec.thickness_nm),
                layer_spec.phase,
                layer_spec.name,
            )
            # Layer normalizes the stoichiometry; keep exactly what the spec says so the
            # TRIM.IN text does not change by a rounding step.
            for element, e in zip(layer.elements, layer_spec.elements, strict=True):
                layer.elements[element]["stoich"] = e.stoich
            layers.append(layer)
        ion = Ion(spec.ion.symbol, keV_to_eV(spec.ion.energy_keV), spec.ion.mass_amu)
        return cls(
            Target(layers),
            ion,
            calculation=spec.calculation,
            number_ions=spec.number_ions,
            angle_ions=spec.ion.angle_deg,
            random_seed=spec.seed,
            **spec.settings,
        )

    def to_yaml(self, path, provenance=None):
        """Write this calculation as a ``run.yaml`` (see :meth:`srim.spec.RunSpec.to_yaml`)."""
        self.to_spec().to_yaml(path, provenance)

    @classmethod
    def from_yaml(cls, path):
        """Build a :class:`TRIM` from a ``run.yaml``."""
        return cls.from_spec(RunSpec.from_yaml(path))

    @staticmethod
    def copy_output_files(src_directory, dest_directory, check_srim_output=True):
        """Copies known TRIM files in directory to destination directory

        Parameters
        ----------
        src_directory : :obj:`str`
            source directory to look for TRIM output files
        dest_directory : :obj:`str`
            destination directory to copy TRIM output files to
        check_srim_output : :obj:`bool`, optional
            also copy the files that SRIM writes to its ``SRIM Outputs`` subfolder
            (``RANGE_3D.txt``, ``COLLISON.txt`` ...). Files are copied, never moved: the
            source is left as it is, so a :class:`~srim.output.Results` of it keeps working.
        """
        known_files = {
            "run.yaml",
            "TRIM.IN",
            "PHONON.txt",
            "E2RECOIL.txt",
            "IONIZ.txt",
            "LATERAL.txt",
            "NOVAC.txt",
            "RANGE.txt",
            "VACANCY.txt",
            "COLLISON.txt",
            "BACKSCAT.txt",
            "SPUTTER.txt",
            "RANGE_3D.txt",
            "TRANSMIT.txt",
            "TRIMOUT.txt",
            "TDATA.txt",
            "EXYZ.txt",
        }

        warn_if_stale_install(src_directory)

        if not os.path.isdir(src_directory):
            raise ValueError("src_directory must be directory")

        if not os.path.isdir(dest_directory):
            raise ValueError("dest_directory must be directory")

        for known_file in known_files:
            if os.path.isfile(os.path.join(src_directory, known_file)):
                shutil.copy(os.path.join(src_directory, known_file), dest_directory)
            elif (
                os.path.isfile(os.path.join(src_directory, "SRIM Outputs", known_file))
                and check_srim_output
            ):
                shutil.copy(os.path.join(src_directory, "SRIM Outputs", known_file), dest_directory)

    def run(
        self,
        srim_directory=None,
        write_spec=True,
        *,
        work_dir=None,
        output_dir=None,
        keep=False,
        lock_timeout=None,
        run_dir=None,
    ):
        """Run configured srim calculation

        By default the calculation runs in its own **private run directory**, a
        lightweight copy of the installed SRIM (see :class:`srim.rundir.RunDir`), so
        any number of runs can happen at the same time and the installed SRIM
        directory is never written. This method:

         - writes the input file to ``<run directory>/TRIM.IN``
         - launches ``<run directory>/TRIM.exe``. Uses ``wine`` if available (needed for linux and osx),
           in a private clone of the wine prefix
         - after a successful run writes ``<run directory>/run.yaml`` (see below)

        The returned :class:`~srim.output.Results` reads from the run directory, which
        is kept (below ``work_dir``, default ``<pysrim2 data dir>/runs``); pass
        ``output_dir`` to get the outputs in a folder of your choice instead, in which
        case the run directory is removed. A run that fails keeps its run directory
        for debugging; the error mentions its path.

        Parameters
        ----------
        srim_directory : :obj:`str`, optional
            Run **in place** in this SRIM directory (the pre-0.9 behaviour: outputs are
            written into it) instead of in a private copy. Concurrent in-place runs on
            the same directory take turns (exclusive file lock). Default ``None``: run
            isolated, copying ``srim_dir()`` (resolved when this method is called:
            ``$PYSRIM2_SRIM_DIR``, then pysrim2's data directory, then ``/tmp/srim``
            if it holds an install).
        write_spec : :obj:`bool`, optional
            write ``run.yaml`` next to the outputs (in the run directory, where the
            results are read from; :meth:`copy_output_files` copies it along with them).
            It holds the :class:`~srim.spec.RunSpec` (including the seed) and the
            provenance: pysrim2 version, SRIM SHA-256 (from the ``.pysrim2-installed``
            marker, if present), platform, backend (``native`` or ``wine``), start and end
            time (UTC) and host name. Default True.
        work_dir : :obj:`str`, optional
            folder to create the run directory in (default ``<pysrim2 data dir>/runs``).
        output_dir : :obj:`str`, optional
            copy the output files (and ``run.yaml``) here and read the results from
            here; the run directory is then removed unless ``keep``.
        keep : :obj:`bool`, optional
            keep the run directory even when ``output_dir`` is given. Default False.
        lock_timeout : :obj:`float`, optional
            in-place runs only: seconds to wait for the directory lock before raising
            :class:`TimeoutError` (default: wait; a message appears after 5 s).
        run_dir : :class:`srim.rundir.RunDir`, optional
            reuse this run directory (it is reset first, and left in place afterwards).
            For workers that run many calculations.

        Returns
        -------
        :class:`srim.output.Results`
            its ``spec`` attribute is the run spec as a dict (without provenance, so
            exports carry no host name), used by ``Results.to_parquet`` etc.
        """

        def execute(directory, prefix):
            return self._execute(directory, prefix, write_spec)

        def collect(where, destination):
            _copy_outputs(where, destination, TRIM_OUTPUT_FILES + ("run.yaml",), "SRIM Outputs")

        def finish(spec, where):
            results = Results(where)
            results.spec = spec.to_dict()
            return results

        return _managed_run(
            execute,
            collect,
            None,
            srim_directory,
            work_dir,
            output_dir,
            keep,
            lock_timeout,
            run_dir,
            finish,
        )

    def _execute(self, srim_directory, prefix, write_spec):
        """Write the inputs, run ``TRIM.exe`` in ``srim_directory`` and return the spec."""
        srim_directory = os.path.abspath(srim_directory)
        # The process's working directory is never changed (runs may happen in threads).
        _remove_stale(srim_directory, TRIM_OUTPUT_FILES + ("run.yaml",), "SRIM Outputs")
        self._write_input_files(srim_directory)
        started = datetime.now(UTC)
        # Make sure compatible with Windows, OSX, and Linux
        _run_srim("TRIM.exe", prefix, srim_directory)
        finished = datetime.now(UTC)
        _require_output(srim_directory, "RANGE.txt")
        spec = self.to_spec()
        if write_spec:
            try:
                spec.to_yaml(
                    os.path.join(srim_directory, "run.yaml"),
                    _provenance(srim_directory, started, finished, spec.seed),
                )
            except Exception as exc:  # the run itself succeeded; keep its results
                warnings.warn(f"could not write run.yaml: {exc}", RuntimeWarning, stacklevel=2)
        return spec


def _provenance(srim_directory, started, finished, seed):
    """Informational record of a run, stored under ``provenance:`` in ``run.yaml``"""
    from . import __version__
    from .installer.srim_install import MARKER_NAME

    marker = os.path.join(srim_directory, MARKER_NAME)
    sha = None
    if os.path.isfile(marker):
        with open(marker, encoding="utf-8") as f:
            sha = f.read().strip() or None

    def stamp(moment):
        return moment.strftime("%Y-%m-%dT%H:%M:%SZ")

    return {
        "pysrim2_version": __version__,
        "srim_sha256": sha,
        "platform": sys.platform,
        "backend": "wine" if sys.platform != "win32" and shutil.which("wine") else "native",
        "started_utc": stamp(started),
        "finished_utc": stamp(finished),
        "host": socket.gethostname(),
        "seed": seed,
    }


def _check_output_filename(name):
    """Return ``name`` if it is a plain file name (no directories), else raise ValueError."""
    if not isinstance(name, str) or name in ("", ".", "..") or os.path.basename(name) != name:
        raise ValueError(f"output_filename must be a plain file name, got {name!r}")
    if "\\" in name or "/" in name or os.path.isabs(name):
        raise ValueError(f"output_filename must be a plain file name, got {name!r}")
    return name


class SRSettings:
    """SR Settings

    Parameters
    ----------
    energy_min : :obj:`float`, optional
       lowest energy in [eV] to calculation range
    output_type : :obj:`int`, optional
       specify units for output table
       (1) eV/Angstrom
       (2) keV/micron
       (3) MeV/mm
       (4) keV / (ug/cm2)
       (5) MeV / (mg/cm2)
       (6) keV / (mg/cm2)
       (7) eV / (1E15 atoms/cm2)
       (8) L.S.S reduced units
    output_filename : :obj:`str`, optional
       filename to give for SR output from calcualtion
    correction : :obj:`float`, optional
       Bragg rule correction. Usually no correction needed for heavy
       elements. Default 1.0 implies 100% of value (no change). 1.1
       will increase by 10%.

    Notes
    -----
        This class should never explicitely created. Instead set as
        kwargs in :class:`srim.srim.SR`
    """

    def __init__(self, **args):
        self._settings = {
            "energy_min": check_input(float, is_positive, args.get("energy_min", 1.0e3)),
            "output_type": check_input(int, is_one_to_eight, args.get("output_type", 1)),
            "output_filename": _check_output_filename(args.get("output_filename", "SR_OUTPUT.txt")),
            "correction": check_input(float, is_positive, args.get("correction", 1.0)),
        }

    def __getattr__(self, attr):
        return self._settings[attr]


class SR:
    """Automate SR Calculations

    Parameters
    ----------
    leyer : :class:`srim.core.layer.Layer`
        constructed layer for SR calculation
    ion : :class:`srim.core.ion.Ion`
        constructed ion for SR calculation
    kwargs :
        See :class:`srim.srim.SRSettings` for available SR
        options. There are a few and none are required. Defaults are
        appropriate for most cases.
    """

    def __init__(self, layer, ion, **kwargs):
        self.settings = SRSettings(**kwargs)
        self.layer = layer
        self.ion = ion

    def _write_input_file(self, directory="."):
        """Write necissary SR input file for calculation into ``directory``"""
        SRInput(self).write(directory)

    def run(
        self,
        srim_directory=None,
        *,
        work_dir=None,
        output_dir=None,
        keep=False,
        lock_timeout=None,
        run_dir=None,
    ):
        """Run configured srim calculation

        Like :meth:`TRIM.run`, this runs in a private run directory by default (the
        installed SRIM is never written); ``srim_directory=`` runs in place under a
        file lock. This method:

         - writes the input file to ``<run directory>/SR Module/TRIM.IN``
         - launches ``<run directory>/SR Module/SRModule.exe``. Uses ``wine`` if available (needed for linux and osx)

        Parameters
        ----------
        srim_directory : :obj:`str`, optional
            run in place in this SRIM directory (``TRIM.exe`` should be located in
            it), the pre-0.9 behaviour, under an exclusive file lock. Default
            ``None``: run in a private copy of ``srim_dir()`` (resolved when this
            method is called: ``$PYSRIM2_SRIM_DIR``, then pysrim2's data directory,
            then ``/tmp/srim`` if it holds an install).
        work_dir, output_dir, keep, lock_timeout, run_dir :
            see :meth:`TRIM.run`; ``output_dir`` receives the SR output file.
        """

        def execute(directory, prefix):
            sr_directory = os.path.abspath(os.path.join(directory, "SR Module"))
            _remove_stale(sr_directory, [self.settings.output_filename])
            self._write_input_file(sr_directory)
            # Make sure compatible with Windows, OSX, and Linux
            _run_srim("SRModule.exe", prefix, sr_directory)
            _require_output(sr_directory, self.settings.output_filename)

        def collect(where, destination):
            _copy_outputs(where, destination, (self.settings.output_filename,))

        return _managed_run(
            execute,
            collect,
            "SR Module",
            srim_directory,
            work_dir,
            output_dir,
            keep,
            lock_timeout,
            run_dir,
            lambda _value, where: SRResults(where),
        )
