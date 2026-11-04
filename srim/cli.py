"""Command line interface: ``pysrim2 install`` and ``pysrim2 doctor``."""

import argparse
import json
import subprocess
import sys
from pathlib import Path

import srim
from srim.installer import container, skill
from srim.installer import steps as steps_module
from srim.installer.srim_install import NOTICE, SrimInstallError
from srim.installer.steps import COMPONENTS, ComponentUnavailable
from srim.installer.vb5 import Vb5SetupError, vb5_ready
from srim.installer.wine import WINE_MANUAL_HINT, WineSetupError, distro_wine_commands, wine_status
from srim.paths import srim_dir

EXIT_OK = 0
EXIT_FAILED = 1
EXIT_ADMIN_SKIPPED = 2
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
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    container.reset_cache()
    if args.command == "install":
        skill.configure(args.agent, args.scope, args.yes)
    if args.command == "install":
        return _install(args)
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
