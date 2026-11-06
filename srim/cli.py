"""Command line interface: ``pysrim2 install``, ``doctor``, ``run``, ``run-job``, ``import``,
``clean`` and ``scan``.

Exit codes of the ``scan`` commands (also in their help):

- 0: every point done or cached (a point that reached ``max_ions`` prints a warning)
- 1: some point failed or is missing
- 2: bad input: an invalid spec, ``--set`` or scan folder, or nothing could start (no SRIM,
  no container runtime)
- 130: stopped by Ctrl-C; the partial result is written

``PYSRIM2_SCAN_RUNNER=module:function`` makes ``scan run`` use that function in place of SRIM.
It is for the tests of the command line only.
"""

import argparse
import importlib
import importlib.util
import json
import os
import re
import shutil
import subprocess
import sys
from datetime import UTC, datetime
from pathlib import Path

import srim
from srim.installer import container, skill
from srim.installer import steps as steps_module
from srim.installer.srim_install import NOTICE, SrimInstallError
from srim.installer.steps import COMPONENTS, ComponentUnavailable
from srim.installer.vb5 import Vb5SetupError, vb5_ready
from srim.installer.wine import WINE_MANUAL_HINT, WineSetupError, distro_wine_commands, wine_status
from srim.paths import srim_dir
from srim.ui import JsonLines, PlainProgress

EXIT_OK = 0
EXIT_FAILED = 1
EXIT_ADMIN_SKIPPED = 2
EXIT_BAD_INPUT = 2  # run / import: invalid run.yaml, unreadable TRIM.IN or --set
EXIT_INTERRUPTED = 130
INSTALL_ERRORS = (
    SrimInstallError,
    WineSetupError,
    container.ContainerError,
    Vb5SetupError,
    skill.SkillInstallError,
    ValueError,
    subprocess.SubprocessError,
    OSError,
)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="pysrim2", description="pysrim2 setup tools")
    parser.add_argument("--version", action="version", version=f"pysrim2 {srim.__version__}")
    sub = parser.add_subparsers(dest="command", required=True)

    install = sub.add_parser("install", help="install what pysrim2 needs to run SRIM")
    install.add_argument("components", nargs="*", metavar="COMPONENT", help=", ".join(COMPONENTS))
    install.add_argument("--dry-run", action="store_true", help="show every step, change nothing")
    install.add_argument(
        "--yes",
        action="store_true",
        help="run steps that need admin rights; overwrite an unmanaged skill",
    )
    install.add_argument("--agent", choices=("claude", "all"), default="claude")
    install.add_argument("--scope", choices=("user", "project"), default="user")

    doctor = sub.add_parser("doctor", help="check the installation")
    doctor.add_argument("--json", action="store_true", help="machine-readable output")

    run = sub.add_parser("run", help="run TRIM from a run.yaml")
    run.add_argument(
        "spec",
        metavar="run.yaml",
        nargs="?",
        help="run specification (see docs/run-spec.md); may also follow --set",
    )
    run.add_argument(
        "--set",
        dest="overrides",
        nargs="+",
        action="extend",
        default=[],
        metavar="KEY=VALUE",
        help="change a value before running, e.g. ion.energy_keV=30 target.0.thickness_nm=50",
    )
    run.add_argument(
        "--output",
        "-o",
        metavar="DIR",
        help="copy outputs and run.yaml here (default ./srim-output-<UTC timestamp>)",
    )

    clean = sub.add_parser("clean", help="delete files pysrim2 left behind")
    clean.add_argument("what", choices=("runs",), help="runs: private run directories")
    clean.add_argument(
        "--older-than",
        type=float,
        default=0.0,
        metavar="DAYS",
        help="only those unused for this many days (default 0: every one that is not in use; "
        "folders of running processes and of open Results objects are always skipped)",
    )
    clean.add_argument("--dry-run", action="store_true", help="list what would be deleted")

    job = sub.add_parser(
        "run-job",
        help="run the units of a job folder that are not done (exit 0: all done, "
        "1: some failed, 2: not a job folder)",
    )
    job.add_argument("job_dir", metavar="DIR", help="job folder with job.yaml (see srim.scan)")

    scan = sub.add_parser(
        "scan",
        help="parameter scans: run, prepare for a cluster, collect, clean",
        description="Parameter scans described by a scan.yaml (see srim.scan).",
    )
    scan_sub = scan.add_subparsers(dest="scan_command", required=True, metavar="COMMAND")
    scan_run = scan_sub.add_parser(
        "run",
        help="run every point of a scan.yaml on this machine",
        description="Run every point of a scan.yaml with local worker processes, or one "
        "container of the pysrim2 image per unit (--backend container).",
        epilog=SCAN_EXIT_CODES,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    scan_run.add_argument(
        "spec", metavar="scan.yaml", nargs="?", help="scan file; may also follow --set"
    )
    scan_run.add_argument(
        "--workers", type=int, metavar="N", help="worker processes (default: CPU count minus one)"
    )
    scan_run.add_argument(
        "--backend",
        choices=("local", "container"),
        default="local",
        help="local: SRIM on this machine; container: one container of the pysrim2 image per unit",
    )
    scan_run.add_argument(
        "--image",
        metavar="REF",
        help="container image for --backend container (default: this release's image)",
    )
    scan_run.add_argument(
        "--ui",
        choices=("plot", "dashboard", "web", "plain", "json"),
        default="plot",
        help="plain: a status line when it changes; json: one JSON event per line on stdout. "
        "plot, dashboard and web need the [tui] extra and a terminal, else plain is used",
    )
    scan_run.add_argument(
        "--output",
        "-o",
        metavar="DIR",
        help="scan folder (default ./scan-<UTC timestamp>); an existing scan folder is reused",
    )
    _add_cache_options(scan_run)
    scan_run.add_argument(
        "--retries",
        type=int,
        default=1,
        metavar="N",
        help="run a failed point again this many times before reporting it (default 1)",
    )
    _add_set(scan_run)

    scan_prepare = scan_sub.add_parser(
        "prepare",
        help="write the job folders and submit.sbatch of a scan for a Slurm cluster",
        description="Write a scan folder that a cluster runs with a job array (see "
        "srim.scan.prepare). Fixed ion counts only.",
        epilog="exit codes: 0 written; 2 bad input (spec, --set, jobs, or not a scan folder)",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    scan_prepare.add_argument(
        "spec", metavar="scan.yaml", nargs="?", help="scan file; may also follow --set"
    )
    scan_prepare.add_argument(
        "--output",
        "-o",
        metavar="DIR",
        required=True,
        help="scan folder to write (it must not hold a prepared scan already)",
    )
    scan_prepare.add_argument(
        "--jobs", type=int, metavar="N", help="job folders (default: one per point, at most one)"
    )
    _add_set(scan_prepare)

    scan_collect = scan_sub.add_parser(
        "collect",
        help="build the result of a prepared scan once its tasks are done",
        description="Collect the outputs of a prepared scan's job folders into its result "
        "(see srim.scan.collect).",
        epilog=SCAN_EXIT_CODES,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    scan_collect.add_argument("dir", metavar="DIR", help="prepared scan folder")
    _add_cache_options(scan_collect)

    scan_clean = scan_sub.add_parser(
        "clean",
        help="delete the result cache of a scan, or a whole scan folder",
        description="Without --all, remove the cache entries of scan folder DIR (DIR/cache), "
        "or of the shared cache given with --cache. Entries that a running scan uses are "
        "kept. --all removes the whole folder DIR, but only one with a scan.yaml.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    scan_clean.add_argument("dir", metavar="DIR", nargs="?", help="scan folder")
    scan_clean.add_argument(
        "--cache", metavar="PATH", help="clear this cache folder instead of DIR/cache"
    )
    scan_clean.add_argument(
        "--all", action="store_true", help="remove the whole scan folder DIR (not with --cache)"
    )
    scan_clean.add_argument(
        "--dry-run", action="store_true", help="list what would be removed, remove nothing"
    )

    imp = sub.add_parser("import", help="turn a TRIM.IN into a run.yaml")
    imp.add_argument("trim_in", metavar="TRIM.IN")
    imp.add_argument("-o", "--output", metavar="run.yaml", help="file to write (default run.yaml)")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    container.reset_cache()
    if args.command == "install":
        skill.configure(args.agent, args.scope, args.yes)
    if args.command == "install":
        return _install(args)
    if args.command == "run":
        return _run(args)
    if args.command == "import":
        return _import(args)
    if args.command == "run-job":
        return _run_job(args)
    if args.command == "clean":
        return _clean(args)
    if args.command == "scan":
        return _scan(args)
    return _doctor(args)


# --- install ----------------------------------------------------------------


def _print_commands(commands: list[str]) -> None:
    for command in commands:
        print(f"    {command}")


def _confirm() -> bool:
    try:
        return input("> ").strip().lower() == "y"
    except EOFError:
        return False


def _install(args) -> int:
    components = list(args.components)
    explicit = bool(components)
    if not explicit:
        components = steps_module.default_components()
    try:
        plan = steps_module.plan(components)
    except ComponentUnavailable as exc:
        print(exc.reason, file=sys.stderr)
        return EXIT_FAILED
    if not plan:
        print("nothing to install")
        return EXIT_OK

    notice_shown = False
    failed = False
    admin_skipped = False
    unmet: set[str] = set()
    for step in plan:
        if step.check():
            print(f"[ok] {step.name}: already installed")
            continue
        if step.name == "srim" and not notice_shown:
            print(NOTICE)
            notice_shown = True
        if any(dep in unmet for dep in step.requires):
            print(f"[skip] {step.name}: waiting for {', '.join(step.requires)}")
            unmet.add(step.name)
            continue
        commands = step.commands()
        if args.dry_run:
            print(f"[do] {step.name}: {step.summary}")
            _print_commands(commands)
            continue
        if commands and all(c.lstrip().startswith("#") for c in commands):
            print(f"[skip] {step.name}: cannot be installed automatically here:")
            _print_commands(commands)
            admin_skipped = True
            unmet.add(step.name)
            continue
        if step.needs_admin and not args.yes:
            confirmed = False
            if sys.stdin.isatty():
                print(f"[ask] {step.name}: needs admin rights — run these commands? [y/N]")
                _print_commands(commands)
                try:
                    confirmed = _confirm()
                except KeyboardInterrupt:
                    print(f"\n[skip] {step.name}: cancelled")
                    return EXIT_INTERRUPTED
            if not confirmed:
                print(f"[skip] {step.name}: needs admin rights — rerun with --yes or run:")
                _print_commands(commands)
                admin_skipped = True
                unmet.add(step.name)
                continue
            print(f"[do] {step.name}: {step.summary}")
        else:
            print(f"[do] {step.name}: {step.summary}")
            _print_commands(commands)
        try:
            if step.wants_progress:
                step.run(lambda line: print(line, flush=True))
            else:
                step.run()
            container.reset_cache()
            where = f" in {srim_dir()}" if step.name == "srim" else ""
            print(f"[ok] {step.name}: installed{where}")
        except INSTALL_ERRORS as exc:
            print(f"[fail] {step.name}: {exc}", file=sys.stderr)
            failed = True
            break
        except KeyboardInterrupt:
            print(f"\n[skip] {step.name}: cancelled")
            return EXIT_INTERRUPTED
    if failed:
        return EXIT_FAILED
    return EXIT_ADMIN_SKIPPED if admin_skipped else EXIT_OK


# --- run / import -----------------------------------------------------------


def _spec_path(value, overrides):
    """The ``run.yaml`` / ``scan.yaml`` path and the ``--set`` items.

    ``--set`` takes several values, so ``run --set a=1 run.yaml`` reads the path as a value.
    The path is then the first item with no "=", or else the last item that is an existing
    file or ends in .yaml/.yml (a folder name may hold "=": ``runs/x=1/run.yaml``). None
    when neither is there.
    """
    if value is not None:
        return value, overrides
    for i, item in enumerate(overrides):
        if "=" not in item:
            return item, overrides[:i] + overrides[i + 1 :]
    for i in reversed(range(len(overrides))):
        item = overrides[i]
        if Path(item).is_file() or item.endswith((".yaml", ".yml")):
            return item, overrides[:i] + overrides[i + 1 :]
    return None, overrides


def _run(args) -> int:
    from srim.spec import RunSpec, SpecError
    from srim.srim import TRIM

    path, overrides = _spec_path(args.spec, args.overrides)
    if path is None:
        print(
            "no run.yaml given (usage: pysrim2 run run.yaml [--set KEY=VALUE ...]); "
            "put the path before --set",
            file=sys.stderr,
        )
        return EXIT_BAD_INPUT
    try:
        spec = RunSpec.from_yaml(path).with_overrides(overrides)
    except (SpecError, OSError) as exc:
        print(f"{path}: {exc}", file=sys.stderr)
        return EXIT_BAD_INPUT
    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    output = Path(args.output or f"srim-output-{stamp}")
    try:
        # an isolated run directory, so several `pysrim2 run` can be active at once
        TRIM.from_spec(spec).run(output_dir=output)
    except KeyboardInterrupt:
        print("cancelled", file=sys.stderr)
        return EXIT_INTERRUPTED
    except (RuntimeError, subprocess.SubprocessError, OSError, ValueError) as exc:
        print(f"run failed: {exc}", file=sys.stderr)
        return EXIT_FAILED
    print(f"outputs and run.yaml: {output}")
    return EXIT_OK


def _run_job(args) -> int:
    from srim.scan.backends import run_job
    from srim.spec import SpecError

    try:
        failed = run_job(args.job_dir, report=print)
    except SpecError as exc:
        print(f"{args.job_dir}: {exc}", file=sys.stderr)
        return EXIT_BAD_INPUT
    if failed:
        print(f"{failed} unit(s) failed; see their error.log files", file=sys.stderr)
        return EXIT_FAILED
    return EXIT_OK


def _import(args) -> int:
    from srim.spec import SpecError, parse_trim_in

    target = Path(args.output or "run.yaml")
    if args.output is None and target.exists():
        print(f"{target} exists; choose another file with -o", file=sys.stderr)
        return EXIT_FAILED
    try:
        spec = parse_trim_in(args.trim_in)
        spec.to_yaml(
            target,
            {"imported_from": Path(args.trim_in).name, "pysrim2_version": srim.__version__},
        )
    except (SpecError, OSError) as exc:
        print(f"{args.trim_in}: {exc}", file=sys.stderr)
        return EXIT_BAD_INPUT
    print(f"wrote {target}")
    return EXIT_OK


def _clean(args) -> int:
    from srim.rundir import disk_usage, prune_runs, run_folders, runs_base

    base = runs_base()
    # measure first (pruning deletes), then report
    sizes = {folder: disk_usage(folder) for folder in run_folders(base)}
    removed = prune_runs(base, older_than_days=args.older_than, dry_run=args.dry_run)
    verb = "would delete" if args.dry_run else "deleted"
    for folder in removed:
        print(f"{verb} {folder} ({sizes.get(folder, 0) / 1e6:.1f} MB)")
    total = sum(sizes.get(f, 0) for f in removed)
    print(
        f"{verb} {len(removed)} run director{'y' if len(removed) == 1 else 'ies'}, "
        f"{total / 1e6:.1f} MB (in use or newer ones are kept)"
    )
    return EXIT_OK


# --- scan -------------------------------------------------------------------

SCAN_EXIT_CODES = """exit codes:
  0    every point done or cached (a point that reached max_ions prints a warning)
  1    some point failed or is missing
  2    bad input: spec, --set or scan folder invalid, or nothing could start
  130  stopped by Ctrl-C; the partial result is written
"""
#: the environment variable that replaces SRIM by a runner (``module:function``), tests only
SCAN_RUNNER_VAR = "PYSRIM2_SCAN_RUNNER"
GRAPHICAL_UIS = ("plot", "dashboard", "web")
_SHARD_NAME = re.compile(r"[0-9a-f]{2}")
_KEY_NAME = re.compile(r"[0-9a-f]{64}")


def _add_set(parser) -> None:
    parser.add_argument(
        "--set",
        dest="overrides",
        nargs="+",
        action="extend",
        default=[],
        metavar="KEY=VALUE",
        help="change a value of the scan's base spec, e.g. ion.energy_keV=30 number_ions=500",
    )


def _add_cache_options(parser) -> None:
    group = parser.add_mutually_exclusive_group()
    group.add_argument(
        "--cache",
        metavar="DIR",
        help="result cache folder (default: DIR/cache, or $PYSRIM2_CACHE)",
    )
    group.add_argument("--no-cache", action="store_true", help="do not keep a result cache")


def _cache_arg(args):
    """The ``cache=`` argument of :func:`srim.scan.run_scan` for the command's options."""
    if args.no_cache:
        return False
    return args.cache


def _read_scan(value, overrides):
    """The scan of ``scan.yaml`` with the ``--set`` items applied to its base spec.

    Raises SpecError for a bad file, path or override, and OSError for a missing file.
    """
    from srim.scan import Scan
    from srim.spec import SpecError

    path, overrides = _spec_path(value, overrides)
    if path is None:
        raise SpecError(
            "no scan.yaml given (usage: pysrim2 scan run scan.yaml [--set KEY=VALUE]); "
            "put the path before --set"
        )
    scan = Scan.from_yaml(path)
    if not overrides:
        return scan
    base = scan.base.with_overrides(overrides)
    ions = scan.ions
    keys = {item.partition("=")[0].strip() for item in overrides}
    if "number_ions" in keys and ions == scan.base.number_ions:
        ions = None  # the ion count came from the base spec, so it follows the override
    return Scan(
        base, grid=scan.grid, points=scan.rows, extract=scan.extract, ions=ions, seed=scan.seed
    )


def _describes_other_scan(scan_file: Path, scan) -> bool:
    """True if the scan file in a reused folder describes another scan than ``scan``."""
    from srim.scan import Scan
    from srim.spec import SpecError

    try:
        existing = Scan.from_yaml(scan_file)
    except (OSError, ValueError, SpecError):
        return True  # unreadable: it is replaced, so say so
    wanted = {"base": scan.base.to_dict(), **scan.to_dict()}
    current = {"base": existing.base.to_dict(), **existing.to_dict()}
    return current != wanted


def _scan_folder_problem(folder: Path) -> str | None:
    """Why ``folder`` cannot hold a scan (None if it is new, empty or a scan folder)."""
    from srim.scan.executor import LOCK_NAME

    if not folder.exists():
        return None
    if not folder.is_dir():
        return f"{folder} is not a folder"
    if (folder / "scan.yaml").is_file():
        return None
    if not [item for item in folder.iterdir() if item.name != LOCK_NAME]:
        return None  # empty, or holds only the lock of a scan that is starting
    return f"{folder} is not a scan folder (no scan.yaml) and is not empty; choose another folder"


def _runner_from_env():
    """The runner named by ``$PYSRIM2_SCAN_RUNNER`` (``module:function``), or None."""
    ref = os.environ.get(SCAN_RUNNER_VAR)
    if not ref:
        return None
    module_name, _, name = ref.partition(":")
    if not module_name or not name:
        raise ValueError(f"{SCAN_RUNNER_VAR} must look like module:function, got {ref!r}")
    try:
        runner = getattr(importlib.import_module(module_name), name)
    except (ImportError, AttributeError) as exc:
        raise ValueError(f"{SCAN_RUNNER_VAR}={ref}: {exc}") from None
    print(f"pysrim2: using test runner {ref} ({SCAN_RUNNER_VAR})", file=sys.stderr)
    return runner


def _view_for(ui: str) -> str:
    """The view that runs for ``--ui``: ``plain`` or ``json``. A graphical view is not
    available yet, so ``plain`` runs instead, with a notice on stderr."""
    if ui not in GRAPHICAL_UIS:
        return ui
    if importlib.util.find_spec("textual") is None:
        reason = "the optional textual package is not installed"
    elif not sys.stdout.isatty():
        reason = "stdout is not a terminal"
    else:
        reason = "not available yet"
    print(f"--ui {ui}: {reason}; using --ui plain", file=sys.stderr)
    return "plain"


def _report_outcome(result, folder: Path, view: str, stopped: bool) -> int:
    """Print the result path (stderr for json, which owns stdout) and return the exit code."""
    say = sys.stderr if view == "json" else sys.stdout
    print(f"result: {folder / 'result' / 'scan.csv'}", file=say)
    if stopped:
        print("stopped by Ctrl-C: the partial result above is kept", file=sys.stderr)
        return EXIT_INTERRUPTED
    counts = result.table["status"].value_counts()
    if counts.get("max_ions", 0):
        print(
            f"warning: {counts['max_ions']} point(s) reached max_ions before the precision "
            "target: their error is above the goal",
            file=sys.stderr,
        )
    bad = counts.get("failed", 0) + counts.get("missing", 0) + counts.get("stopped", 0)
    if bad:
        print(f"{bad} point(s) failed or are missing; see the result", file=sys.stderr)
        return EXIT_FAILED
    return EXIT_OK


def _scan(args) -> int:
    handlers = {
        "run": _scan_run,
        "prepare": _scan_prepare,
        "collect": _scan_collect,
        "clean": _scan_clean,
    }
    return handlers[args.scan_command](args)


def _scan_run(args) -> int:
    from srim.scan import run_scan
    from srim.scan.executor import ScanBusy
    from srim.spec import SpecError

    label = args.spec or "scan.yaml"
    try:
        scan = _read_scan(args.spec, args.overrides)
    except (SpecError, OSError) as exc:
        print(f"{label}: {exc}", file=sys.stderr)
        return EXIT_BAD_INPUT
    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    folder = Path(args.output or f"scan-{stamp}")
    problem = _scan_folder_problem(folder)
    if problem:
        print(problem, file=sys.stderr)
        return EXIT_BAD_INPUT
    if (folder / "scan.yaml").is_file():
        print(f"reusing the scan folder {folder}: scan.yaml is rewritten", file=sys.stderr)
        if _describes_other_scan(folder / "scan.yaml", scan):
            print(
                f"warning: {folder / 'scan.yaml'} describes another scan; it is replaced "
                "(points run again unless the cache has them)",
                file=sys.stderr,
            )
    view = _view_for(args.ui)
    consumer = JsonLines(sys.stdout) if view == "json" else PlainProgress(sys.stdout)
    state = {}  # "started" once the scan has begun, "stopped" from its last event

    def on_event(event):
        if event.kind == "scan_started":
            state["started"] = True
        if event.kind == "scan_finished":
            state["stopped"] = event.stopped
        consumer(event)

    try:
        result = run_scan(
            scan,
            workers=args.workers,
            backend=args.backend,
            scan_dir=folder,
            on_event=on_event,
            cache=_cache_arg(args),
            retries=args.retries,
            image=args.image,
            runner=_runner_from_env(),
        )
    except KeyboardInterrupt:
        print("cancelled", file=sys.stderr)
        return EXIT_INTERRUPTED
    except ScanBusy as exc:
        print(f"{exc}; wait for it or choose another folder", file=sys.stderr)
        return EXIT_BAD_INPUT
    except (SpecError, ValueError, FileNotFoundError, container.ContainerError, RuntimeError,
            subprocess.SubprocessError, OSError) as exc:  # fmt: skip
        if not state.get("started"):  # nothing ran: bad input, or no SRIM / container runtime
            print(f"scan did not start: {exc}", file=sys.stderr)
            return EXIT_BAD_INPUT
        print(f"scan failed: {exc}", file=sys.stderr)  # after scan_started: the run failed
        return EXIT_FAILED
    return _report_outcome(result, folder, view, state.get("stopped", False))


def _scan_prepare(args) -> int:
    from srim.scan import prepare
    from srim.scan.slurm import JOBS_DIR, SUBMIT_FILE
    from srim.spec import SpecError

    label = args.spec or "scan.yaml"
    try:
        scan = _read_scan(args.spec, args.overrides)
    except (SpecError, OSError) as exc:
        print(f"{label}: {exc}", file=sys.stderr)
        return EXIT_BAD_INPUT
    folder = Path(os.path.abspath(args.output))
    problem = _scan_folder_problem(folder)
    if problem:
        print(problem, file=sys.stderr)
        return EXIT_BAD_INPUT
    try:
        prepare(scan, folder, jobs=args.jobs)
    except (SpecError, ValueError, FileExistsError) as exc:
        print(f"{folder}: {exc}", file=sys.stderr)
        return EXIT_BAD_INPUT
    except OSError as exc:
        print(f"{folder}: prepare failed: {exc}", file=sys.stderr)
        return EXIT_FAILED
    jobs = sorted((folder / JOBS_DIR).iterdir())
    print(f"prepared {len(jobs)} job folder(s) in {folder}")
    print(f"submit from the scan folder: cd {folder} && sbatch {SUBMIT_FILE}")
    return EXIT_OK


def _scan_collect(args) -> int:
    from srim.scan import collect
    from srim.scan.result import STATUSES
    from srim.spec import SpecError

    folder = Path(os.path.abspath(args.dir))
    try:
        result = collect(folder, cache=_cache_arg(args))
    except (FileNotFoundError, SpecError, ValueError) as exc:
        print(f"{folder}: {exc}", file=sys.stderr)
        return EXIT_BAD_INPUT
    counts = result.table["status"].value_counts()
    summary = ", ".join(f"{status} {counts[status]}" for status in STATUSES if counts.get(status))
    print(f"collected {len(result.table)} point(s): {summary}")
    return _report_outcome(result, folder, "plain", stopped=False)


def _scan_clean(args) -> int:
    if (args.dir is None) == (args.cache is None):
        print("give a scan folder DIR, or --cache PATH for a shared cache", file=sys.stderr)
        return EXIT_BAD_INPUT
    if args.all and args.dir is None:
        print("--all removes a whole scan folder: give DIR", file=sys.stderr)
        return EXIT_BAD_INPUT
    if args.dir is None:
        return _clear_cache(Path(args.cache), args.dry_run)
    folder = Path(args.dir)
    if args.all:
        return _remove_scan_folder(folder, args.dry_run)
    return _clear_cache(folder / "cache", args.dry_run, scan_folder=folder)


def _plural(count: int, word: str) -> str:
    """``3 cache entries``, ``1 cache entry``."""
    if count == 1:
        return f"{count} {word}"
    return f"{count} {word[:-1]}ies" if word.endswith("y") else f"{count} {word}s"


def _clear_cache(path: Path, dry_run: bool, scan_folder: Path | None = None) -> int:
    """Remove the cache entries at ``path``. With ``scan_folder`` (the folder that owns the
    cache) the removal holds that folder's lock, so a scan running there is not disturbed."""
    from srim.rundir import disk_usage
    from srim.scan.cache import clear_cache, is_cache
    from srim.scan.executor import scan_lock

    if not path.is_dir():
        print(f"no cache at {path}")
        return EXIT_OK
    if not is_cache(path):
        print(f"{path} is not a pysrim2 result cache; nothing removed", file=sys.stderr)
        return EXIT_BAD_INPUT
    if dry_run:
        entries = [
            entry
            for shard in path.iterdir()
            if shard.is_dir() and _SHARD_NAME.fullmatch(shard.name)
            for entry in shard.iterdir()
            if entry.is_dir() and _KEY_NAME.fullmatch(entry.name)
        ]
        size = sum(disk_usage(entry) for entry in entries)
        print(
            f"would remove {_plural(len(entries), 'cache entry')} ({size / 1e6:.1f} MB) from {path}"
        )
        return EXIT_OK
    lock = None
    if scan_folder is not None:
        lock = scan_lock(scan_folder)
        try:
            lock.acquire()
        except TimeoutError:
            print(f"a scan is running in {scan_folder}; nothing removed", file=sys.stderr)
            return EXIT_FAILED
    try:
        removed = clear_cache(path)
    finally:
        if lock is not None:
            lock.release()
    print(f"removed {_plural(removed, 'cache entry')} from {path}")
    return EXIT_OK


def _removal_refusal(folder: Path) -> str | None:
    """Why ``folder`` must never be removed (a symbolic link, the working directory or a
    folder above it, the home folder), or None."""
    if folder.is_symlink():
        return "is a symbolic link"
    target = folder.resolve()
    cwd = Path.cwd().resolve()
    if target == cwd or target in cwd.parents:
        return "holds the working directory"
    if target == Path.home().resolve():
        return "is the home folder"
    return None


def _remove_scan_folder(folder: Path, dry_run: bool) -> int:
    from srim.rundir import disk_usage
    from srim.scan.executor import MARKER_NAME, scan_lock

    refusal = _removal_refusal(folder)
    if refusal:
        print(f"{folder} {refusal}; nothing removed", file=sys.stderr)
        return EXIT_FAILED
    if not (folder / MARKER_NAME).is_file():
        print(
            f"{folder} is not a scan folder (no {MARKER_NAME} marker); nothing removed",
            file=sys.stderr,
        )
        return EXIT_BAD_INPUT
    size = disk_usage(folder)
    if dry_run:
        print(f"would remove {folder} ({size / 1e6:.1f} MB)")
        return EXIT_OK
    lock = scan_lock(folder)
    try:
        lock.acquire()
    except TimeoutError:
        print(f"a scan is running in {folder}; nothing removed", file=sys.stderr)
        return EXIT_FAILED
    lock.release()  # the lock file is inside the folder: release it before the removal
    try:
        shutil.rmtree(folder)
    except OSError as exc:
        print(f"{folder}: could not remove it: {exc}", file=sys.stderr)
        return EXIT_FAILED
    print(f"removed {folder} ({size / 1e6:.1f} MB)")
    return EXIT_OK


# --- doctor -----------------------------------------------------------------


def _check(name: str, ok: bool, detail: str, fix: str = "") -> dict:
    return {"name": name, "ok": bool(ok), "detail": detail, "fix": "" if ok else fix}


def _container_checks() -> list[dict]:
    runtime = container.find_runtime()
    if runtime is None:
        stopped = container.installed_runtimes()
        if stopped:
            name = stopped[0]
            return [
                _check(
                    "container-runtime",
                    False,
                    f"{name} is installed but not running",
                    f"run: {container.start_hint(name)} "
                    "(or install Podman: pysrim2 install runtime)",
                )
            ]
        return [
            _check(
                "container-runtime",
                False,
                "podman or docker not found",
                "run: pysrim2 install runtime",
            )
        ]
    tag = container.image_tag()
    pull = " ".join(container.pull_command(runtime, tag))
    return [
        _check("container-runtime", True, runtime),
        _check(
            "image",
            container.image_present(runtime, tag),
            container.image_ref(tag),
            f"run: {pull}",
        ),
    ]


def collect_checks() -> list[dict]:
    system = steps_module.current_platform()
    checks = [
        _check(
            "python",
            sys.version_info >= (3, 11),
            sys.version.split()[0],
            "pysrim2 needs Python 3.11 or newer",
        ),
        _check("pysrim2", True, srim.__version__),
    ]
    directory = srim_dir()
    status = wine_status() if system == "linux" else None
    if system == "windows":
        native = True
    elif system == "linux":
        # Same rule as the default install plan: wine, or no container runtime at all.
        native = bool(status.wine) or not steps_module.container_runtime()
    else:
        native = False
    skip = "not needed (container route)"
    if native:
        trim = directory / "TRIM.exe"
        checks.append(
            _check("srim-dir", directory.is_dir(), str(directory), "run: pysrim2 install srim")
        )
        checks.append(_check("trim-exe", trim.is_file(), str(trim), "run: pysrim2 install srim"))
        legal = next(
            (p for p in (directory / "Data" / "LEGAL.rtf", directory / "LEGAL.rtf") if p.is_file()),
            None,
        )
        checks.append(
            _check(
                "srim-legal-notice",
                legal is not None,
                str(legal) if legal else "LEGAL.rtf not found",
                "run: pysrim2 install srim",
            )
        )
    else:
        checks += [_check(n, True, skip) for n in ("srim-dir", "trim-exe", "srim-legal-notice")]

    if system == "windows":
        ready = vb5_ready(directory)
        checks.append(
            _check(
                "vb5",
                ready,
                "ready" if ready else "VB5 runtime not set up",
                "run: pysrim2 install vb5 (run from an elevated terminal)",
            )
        )
    if status is not None and not native:
        checks += [
            _check(n, True, skip)
            for n in ("wine", "xvfb-run", "extractor", "downloader", "wine-prefix")
        ]
    elif status is not None:
        fix = "run: pysrim2 install wine"
        # On a distribution we have no package commands for, "install wine" cannot help.
        pkg_fix = fix if distro_wine_commands() else WINE_MANUAL_HINT
        checks += [
            _check("wine", status.wine, status.wine or "not found", pkg_fix),
            _check("xvfb-run", status.xvfb_run, status.xvfb_run or "not found", pkg_fix),
            _check("extractor", status.extractor, status.extractor or "not found", pkg_fix),
            _check("downloader", status.downloader, status.downloader or "not found", pkg_fix),
            _check(
                "wine-prefix",
                status.prefix_ready,
                "ready" if status.prefix_ready else "not set up",
                pkg_fix if not (status.wine and status.xvfb_run) else fix,
            ),
        ]
    # The skill is optional: only an installed copy that is out of date is a problem.
    candidates = skill.skill_targets("all", "user", Path.cwd()) + skill.skill_targets(
        "claude", "project", Path.cwd()
    )
    installed = [t for t in candidates if (t / skill.MARKER).is_file()]
    stale = [t for t in installed if not skill.skill_current([t])]
    # Stale copies carry the marker, so they are ours to update without --yes.
    checks.append(
        _check(
            "agent-skill",
            not stale,
            ", ".join(str(t) for t in installed) or "not installed (optional)",
            "run: pysrim2 install skill",
        )
    )
    from srim.rundir import retention_days, runs_base, runs_disk_usage

    count, size = runs_disk_usage()
    days = retention_days()
    checks.append(
        _check(
            "run-directories",
            True,
            f"{count} in {runs_base()}, {size / 1e6:.1f} MB "
            f"(unused ones are deleted after {days:g} days; `pysrim2 clean runs` deletes them now)"
            if days
            else f"{count} in {runs_base()}, {size / 1e6:.1f} MB (kept: retention is off)",
        )
    )
    if not native:
        checks += _container_checks()
    return checks


def _doctor(args) -> int:
    checks = collect_checks()
    if args.json:
        print(json.dumps({"checks": checks}, indent=2))
    else:
        for check in checks:
            print(f"[{'ok' if check['ok'] else '!!'}] {check['name']}: {check['detail']}")
            if check["fix"]:
                print(f"     fix: {check['fix']}")
    return EXIT_OK if all(c["ok"] for c in checks) else EXIT_FAILED
