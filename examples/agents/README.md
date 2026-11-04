# Using pysrim2 with an AI agent

pysrim2 ships a skill (`SKILL.md`) that teaches coding agents such as Claude Code
how to run SRIM through pysrim2.

## Set up

```shell
uv tool install pysrim2
pysrim2 install            # SRIM, wine or container, and the skill
pysrim2 doctor             # everything should be [ok]
```

`pysrim2 install skill` alone copies the skill to `~/.claude/skills/pysrim2/`
(`--scope project` for `./.claude/skills/`, `--agent all` also for
`~/.agents/skills/`). See the
[agent documentation](https://pysrim2.readthedocs.io/en/latest/agents.html).

## Example prompts

- "Simulate a 100 nm gold layer on a silicon wafer, implant 200 boron ions at 7
  degrees and 20 keV, and give me a pandas DataFrame of implantation depth and
  xy straggle."
- "How far do 1 MeV helium ions travel in silicon? Give me the stopping table."
- "Run a full-cascade TRIM calculation for 100 keV Ni into 50 nm Au / 500 nm Si /
  Al and plot the vacancy profile."

## What the agent should do

1. Run `pysrim2 doctor`; if something fails, follow its `fix:` line (or use the
   container, see the [container page](https://pysrim2.readthedocs.io/en/latest/container.html)).
2. Write a short script with `srim.Layer`, `Target`, `Ion` and `TRIM`/`SR`, run it
   (natively, or in the container with the script's folder mounted at `/data`).
3. Parse the results: `results.range`, `results.vacancy`, ..., and per-ion final
   positions from `RANGE_3D.txt` (`ranges=1`) into a DataFrame.

## Expected result

For the first prompt (200 ions, `random_seed=12345`) the mean implantation depth is
about **282 angstrom**; the lateral straggle is a few hundred angstrom. The agent
can start from
[`boron_in_gold_on_silicon.py`](../scripts/boron_in_gold_on_silicon.py), the
same file the skill ships as its example.

SRIM © SRIM.com (James F. Ziegler) — free for non-commercial use only; see `LEGAL.rtf`.
