"""Install the packaged agent skill (``skills/pysrim2``) for coding agents.

The skill is a directory (``SKILL.md`` plus ``examples/``) that agents following the
agentskills convention read from ``~/.claude/skills`` and friends. A marker file records
that pysrim2 wrote it, so a later install may update it but never clobbers a
hand-written skill of the same name.
"""

import importlib.resources
from pathlib import Path

import srim
from srim.installer.steps import Step, register

SKILL_NAME = "pysrim2"
MARKER = ".pysrim2-skill"
AGENTS = ("claude", "all")
SCOPES = ("user", "project")

# Chosen by the command line before the step is planned.
_options = {"agent": "claude", "scope": "user", "yes": False}


class SkillInstallError(RuntimeError):
    """The skill could not be installed (for example, an unmanaged copy is in the way)."""


def configure(agent: str = "claude", scope: str = "user", yes: bool = False) -> None:
    _options.update(agent=agent, scope=scope, yes=yes)


def skill_targets(agent: str, scope: str, cwd: Path) -> list[Path]:
    """Return the skill directories for ``agent`` and ``scope``."""
    if agent not in AGENTS:
        raise ValueError(f"unknown agent {agent!r}; choose from {', '.join(AGENTS)}")
    if scope not in SCOPES:
        raise ValueError(f"unknown scope {scope!r}; choose from {', '.join(SCOPES)}")
    base = Path.home() if scope == "user" else Path(cwd)
    targets = [base / ".claude" / "skills" / SKILL_NAME]
    if agent == "all":
        targets.append(Path.home() / ".agents" / "skills" / SKILL_NAME)
    return targets


def _source():
    packaged = importlib.resources.files("srim") / "_skill"
    if packaged.joinpath("SKILL.md").is_file():
        return packaged
    # Running from a source checkout (no force-include applied).
    return importlib.resources.files("srim").parent / "skills" / SKILL_NAME


def _files() -> dict[str, bytes]:
    """Relative POSIX path -> content of every file in the packaged skill."""
    found: dict[str, bytes] = {}

    def walk(node, prefix):
        for child in sorted(node.iterdir(), key=lambda c: c.name):
            if child.name == "__pycache__" or child.name.endswith((".pyc", ".pyo")):
                continue
            if child.is_dir():
                walk(child, f"{prefix}{child.name}/")
            elif child.is_file():
                found[f"{prefix}{child.name}"] = child.read_bytes()

    walk(_source(), "")
    if "SKILL.md" not in found:
        raise SkillInstallError("the packaged SKILL.md is missing")
    return found


def _managed(target: Path) -> bool:
    return (target / MARKER).is_file()


def _conflicts(target: Path, files: dict[str, bytes]) -> list[str]:
    return [
        rel
        for rel, data in files.items()
        if (target / rel).is_file() and (target / rel).read_bytes() != data
    ]


def install_skill(targets: list[Path], yes: bool = False) -> list[Path]:
    """Copy the skill into each target directory; return the targets written.

    Idempotent. A differing file that pysrim2 did not write (no marker file) is
    never overwritten unless ``yes`` is true.
    """
    files = _files()
    written = []
    for target in targets:
        target = Path(target)
        if not yes and not _managed(target):
            clash = _conflicts(target, files)
            if clash:
                raise SkillInstallError(
                    f"{target} already holds different {', '.join(clash)} not written by "
                    "pysrim2; move it away or rerun with --yes to overwrite"
                )
        for rel, data in files.items():
            dest = target / rel
            dest.parent.mkdir(parents=True, exist_ok=True)
            dest.write_bytes(data)
        (target / MARKER).write_text(f"{srim.__version__}\n", encoding="utf-8")
        written.append(target)
    return written


def skill_current(targets: list[Path]) -> bool:
    """True when every target holds exactly the packaged files."""
    try:
        files = _files()
    except (OSError, SkillInstallError):
        return False
    return all(
        all((t / rel).is_file() and (t / rel).read_bytes() == data for rel, data in files.items())
        for t in targets
    )


def _current_targets() -> list[Path]:
    return skill_targets(_options["agent"], _options["scope"], Path.cwd())


def _skill_step() -> Step:
    targets = _current_targets()
    shown = ", ".join(str(t) for t in targets)
    return Step(
        name="skill",
        summary=f"install the pysrim2 agent skill into {shown}",
        check=lambda: skill_current(targets),
        commands=lambda: [f"copy the pysrim2 agent skill into {t}" for t in targets],
        run=lambda: _run(targets),
    )


def _run(targets: list[Path]) -> None:
    for path in install_skill(targets, yes=bool(_options["yes"])):
        print(f"  skill written to {path}")


register("skill", _skill_step)
