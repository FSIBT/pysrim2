# AGENTS.md

pysrim2 is a maintained continuation of pysrim (distribution `pysrim2`, import `srim`).

## Commands

- Setup: `uv sync`
- Test: `uv run pytest` (run from the repository root; tests use relative paths such as `test_files/`)
- Lint and format: `uv run pre-commit run --all-files`
- Docs: `uv run --group docs sphinx-build -W -b html docs <output dir>`
- Build: `uv build`
- Once per clone: `git config blame.ignoreRevsFile .git-blame-ignore-revs`

## Conventions

- Conventional commit prefixes (feat, fix, test, build, docs, chore, style, ci); one concern per commit.
- Every user-visible change adds a line under `## [Unreleased]` in `CHANGELOG.md`.
- Never commit SRIM binaries or large outputs.
- Fixtures in `test_files/` are byte-exact SRIM output with CRLF line endings. Do not reformat them or let an editor change line endings.
- Fork commits: bring them in with `git cherry-pick -x` so author and message are kept. Never squash or amend them. Put our own changes (tests, adjustments, credit for others who fixed the same thing via `Co-authored-by:`) in separate follow-up commits. Record the result in `docs/changes-from-pysrim.md`.
- Python 3.11 or newer; runtime dependencies are numpy, pyyaml and pandas.
