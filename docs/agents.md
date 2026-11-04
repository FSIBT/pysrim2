# Agent skill

pysrim2 ships a skill, `SKILL.md`, that teaches coding agents (Claude Code and
others that read `SKILL.md` folders) how to run SRIM through pysrim2: build a
target and ion, run TRIM or SR, read the results, and run it in the
{doc}`container`. It includes a runnable example (boron into gold on silicon).
The skill is shipped inside the wheel.

Install it with

```shell
pysrim2 install skill [--agent claude|all] [--scope user|project]
```

| Option | Destination |
|---|---|
| default (`--agent claude --scope user`) | `~/.claude/skills/pysrim2/` |
| `--scope project` | `<current folder>/.claude/skills/pysrim2/` |
| `--agent all` | also `~/.agents/skills/pysrim2/` |

It is part of the default `pysrim2 install`. Re-running updates a copy pysrim2
wrote earlier; a skill folder it did not write is never overwritten unless you
pass `--yes`. `pysrim2 doctor` reports an installed copy that is out of date.

The skill is for agents that *use* pysrim2. If you want an agent to *work on*
pysrim2 itself, it should read `AGENTS.md` in the repository, which is
for contributors.
