# pysrim2 Foundation (Plan 1 of 6) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Turn the GitLab pysrim history into github.com/FSIBT/pysrim2 on `main`, with modern uv/hatchling packaging, all "easy" fork fixes integrated with credit, ruff + pre-commit, CI on Python 3.11–3.15, docs claimed on Read the Docs, and `pysrim2 0.6.0` released to PyPI via trusted publishing.

**Architecture:** No new features — this plan modernizes and fixes the existing `srim` package in place. Fixes land *before* the ruff reformat so cherry-picks apply cleanly. Each fix is its own commit carrying original authorship and a regression test.

**Tech Stack:** Python 3.11–3.15, uv, hatchling, pytest (+mock, cov), ruff, pre-commit, GitHub Actions, PyPI trusted publishing, Sphinx on Read the Docs.

**Spec:** `docs/superpowers/specs/2026-11-03-pysrim2-design.md`

## Roadmap (later plans, written when their predecessor ships)

1. **Foundation → 0.6.0** (this plan)
2. Container + SRIM GitHub Release asset + in-container integration tests + container CI
3. DataFrames, `run.yaml` spec round-trip, EXYZ parser (jphilis), cascade export (Parquet/HDF5)
4. Execution: job dirs, scans, chunked runs with seeds, caching, local/container backends, long runs, `--ui` progress front ends
5. `pysrim2 install` / `doctor` CLI, SKILL.md + skill installation, full AGENTS.md
6. Docs overhaul, CITATION.cff, Zenodo, templates, Dependabot, announcements

## Global Constraints

- Distribution name `pysrim2`; import name stays `srim`.
- `requires-python = ">=3.11"`; CI matrix 3.11, 3.12, 3.13, 3.14, 3.15.
- Runtime deps: `numpy`, `pyyaml`, `pandas` (no version floors unless a test needs one).
- License stays MIT; keep `Copyright (c) 2018 Chris Ostrouchov`, add `Copyright (c) 2026 pysrim2 contributors`.
- First release version `0.6.0`; Semantic Versioning; `CHANGELOG.md` in Keep a Changelog format; every user-visible commit adds a line under `## [Unreleased]`.
- **Never squash or amend fork commits.** Each original fork commit is cherry-picked individually with `git cherry-pick -x` (author, message and content kept; conflict resolution only). Our additions — regression tests, follow-up adjustments, credit for others who fixed the same thing (`Co-authored-by:`/"Also fixed by" in the message) — go in **separate follow-up commits** authored by us. Where a task says "Commit (author X)" it means the cherry-picked original; where it says "amend message", instead write a follow-up commit.
- Small logical commits throughout.
- No SRIM binaries or large outputs added to git (existing upstream history is left untouched — decided by Claude: rewriting it would break SHAs shared with every fork; SRIM's license permits non-commercial redistribution and the zip includes Ziegler's LEGAL.rtf).
- Default branch `main`. Tooling via `uv` (`uv sync`, `uv run`, `uv build`).
- Fork commits referenced below live in the scratch clone `/private/tmp/claude-501/-Users-arun-src-pysrim/8c33dd20-49aa-4b53-a251-1176d3ee9f87/scratchpad/up.git`; add the fork as a remote in the working repo before cherry-picking if the scratch clone is gone (remote URLs: `https://github.com/<user>/pysrim.git`, `https://gitlab.com/<user>/pysrim.git`).

## Review Focus

1. Ion energies below 1 keV / written as `.3` or `3.` — parser must return the correct eV value, not raise. (Task 6)
2. SRIM output written on a comma-decimal Windows locale (`1,234E+02`) — must parse identically to dot-decimal output, without changing the process locale. (Task 7)
3. Neither `wine` nor `xvfb-run` on PATH (native Windows) — must launch `TRIM.exe` directly; `wine` present without `xvfb-run` — must not try `xvfb-run`. (Tasks 3, 9)
4. `import srim` on Python 3.12+ in a clean env — must succeed (no `distutils`, no `pkg_resources`). (Tasks 2, 3; CI matrix Task 14)
5. Unknown unit token in SR output (e.g. `nm`) — must raise `SRIMOutputParseError` naming the token, not `KeyError`/`TypeError`. (Task 8)

---

### Task 1: Repository bootstrap on GitHub

**Files:**
- Modify: git config (branches, remotes); delete `.gitattributes`, `assets/srim.2013.zip` (LFS pointer), `pysrim.egg-info/` if tracked, `.DS_Store` if tracked
- Modify: `.gitignore` (add `.DS_Store`, `__pycache__/`, `*.egg-info/`, `.venv/`, `dist/`, `.coverage`, `htmlcov/`)
- Add: `docs/superpowers/specs/2026-11-03-pysrim2-design.md`, this plan

**Interfaces:** Produces remote `origin` = `https://github.com/FSIBT/pysrim2.git`, remote `upstream` = `https://gitlab.com/costrouc/pysrim.git`, branch `main`.

- [ ] **Step 1:** `git branch -m master main`; `git remote rename origin upstream`.
- [ ] **Step 2:** `gh repo create FSIBT/pysrim2 --public --description "Maintained fork of pysrim: automate, parse and scan SRIM/TRIM calculations (Python 3.11+)"`; `git remote add origin https://github.com/FSIBT/pysrim2.git`.
- [ ] **Step 3:** Commit "chore: drop Git LFS and stale build artifacts" (removals + `.gitignore`), then commit "docs: add pysrim2 design and foundation plan".
- [ ] **Step 4:** `git push -u origin main`; `gh repo edit FSIBT/pysrim2 --default-branch main --enable-wiki=false`.
  Verify: `gh repo view FSIBT/pysrim2 --json defaultBranchRef --jq .defaultBranchRef.name` → `main`.
- [ ] **Step 5 (needs explicit user confirmation at that moment):** delete the old fork. Requires `gh auth refresh -h github.com -s delete_repo` by the user, then `gh repo delete FSIBT/pysrim --yes`.

### Task 2: Modern packaging with uv + hatchling

**Files:**
- Create: `pyproject.toml`, `uv.lock`, `.python-version` (`3.13`)
- Modify: `srim/__init__.py` (add `__version__`)
- Delete: `setup.py`, `setup.cfg`, `.conda/`, `install.sh`, `default.nix`, `.gitlab-ci.yml`, `Dockerfile.pysrim`, `Dockerfile.srim`
- Test: `tests/test_package.py`

**Interfaces:** Produces `srim.__version__: str` read via `importlib.metadata.version("pysrim2")`.

- [ ] **Step 1: Write failing test** `tests/test_package.py`:
  ```python
  import importlib.metadata
  import srim


  def test_version_matches_distribution():
      assert srim.__version__ == importlib.metadata.version("pysrim2")


  def test_element_data_is_packaged():
      assert srim.ElementDB.lookup("Si")["z"] == 14
  ```
- [ ] **Step 2:** `uv run --python 3.11 pytest tests/test_package.py` → FAIL (no `pyproject.toml` / no `pysrim2` dist).
- [ ] **Step 3:** Write `pyproject.toml`: `[project] name="pysrim2"`, `version="0.6.0.dev0"`, `requires-python=">=3.11"`, deps `numpy`, `pyyaml`, `pandas`; authors Chris Ostrouchov + Arun Persaud; `license = "MIT"`; classifiers for 3.11–3.15; URLs Homepage/Docs (`https://pysrim2.readthedocs.io`)/Changelog/Original (`https://gitlab.com/costrouc/pysrim`); hatchling `[tool.hatch.build.targets.wheel] packages=["srim"]` including `srim/data/*.yaml`; sdist excludes `examples/images`, `test_files`, `docs/paper`. `[dependency-groups] dev = ["pytest","pytest-mock","pytest-cov","ruff","pre-commit"]`, `docs = ["sphinx","furo","myst-parser"]`. `[tool.pytest.ini_options] testpaths=["tests"]`. Set `srim.__version__` from `importlib.metadata`. Then `uv lock`.
- [ ] **Step 4:** `uv run --python 3.11 pytest` → all 77 old tests + 2 new PASS. (3.12+ still fails until Task 3.)
- [ ] **Step 5:** `uv build` → produces `dist/pysrim2-0.6.0.dev0-py3-none-any.whl`; `unzip -l` lists `srim/data/elements.yaml`.
- [ ] **Step 6:** Commit "build: switch to pyproject/hatchling and uv; drop setup.py, conda, nix, GitLab CI, old Dockerfiles" (credit `Co-authored-by: Angus Hollands <goosey15@gmail.com>` for the hatchling approach).

### Task 3: Replace distutils with shutil.which (lievcm 368157d, Mirochill 91d2c1a)

**Files:** Modify `srim/srim.py`; Test `tests/test_srim.py`

**Interfaces:** Produces `srim.srim._launch_command(executable: str) -> list[str]` used by `TRIM.run` and `SR.run`. Task 9 extends it.

- [ ] **Step 1: Failing tests** in `tests/test_srim.py`:
  ```python
  def test_launch_command_uses_wine_when_available(monkeypatch):
      monkeypatch.setattr("shutil.which", lambda n: "/usr/bin/wine" if n == "wine" else None)
      assert _launch_command("TRIM.exe") == ["wine", os.path.join(".", "TRIM.exe")]


  def test_launch_command_native_without_wine(monkeypatch):
      monkeypatch.setattr("shutil.which", lambda n: None)
      assert _launch_command("TRIM.exe") == [os.path.join(".", "TRIM.exe")]
  ```
- [ ] **Step 2:** `uv run --python 3.13 pytest tests/test_srim.py` → FAIL (ImportError on distutils / missing `_launch_command`).
- [ ] **Step 3:** Cherry-pick `368157d` (author lievcm), resolve, then introduce `_launch_command` (call `shutil.which` at call time, not import time, so monkeypatching works) and use it in both `run()` methods.
- [ ] **Step 4:** `uv run --python 3.13 pytest` → all PASS; also `--python 3.11`.
- [ ] **Step 5:** Commit (author lievcm) "fix: replace removed distutils with shutil.which" + `Co-authored-by: Mirochill` (email from 91d2c1a).

### Task 4: yaml loading — already fixed, verify only

`yaml.load(..., Loader=yaml.SafeLoader)` is already in history (73205cf, Arun Persaud, 2020); the lievcm/gapost/Allentro/peterhys variants are equivalent. No code change.

- [ ] **Step 1:** Add `test_elementdb_import_emits_no_warnings`: `importlib.reload(srim.core.elementdb)` under `warnings.simplefilter("error")`; assert `ElementDB.lookup("Au")["z"] == 79`.
- [ ] **Step 2:** Run → PASS (guards against regressions); commit "test: guard element database loading against yaml warnings"; list the equivalent fork fixes as credited-but-superseded in `docs/changes-from-pysrim.md` (Task 15).

### Task 5: Raw-string regexes (lievcm 53e5bd2)

**Files:** Modify `srim/output.py`, `srim/core/material.py`, plus `srim/core/utils.py`/`srim/output.py` patterns lievcm missed (`int_regex`, `ion_regex`, `b'...\s...'` literals)

- [ ] **Step 1: Failing test** `tests/test_package.py::test_no_invalid_escape_warnings`: for each module file under `srim/`, `compile(source, path, "exec")` inside `warnings.simplefilter("error", SyntaxWarning)`; must not raise.
- [ ] **Step 2:** Run → FAIL (`SyntaxWarning: invalid escape sequence '\s'`).
- [ ] **Step 3:** Cherry-pick `53e5bd2`; convert any remaining patterns to raw strings.
- [ ] **Step 4:** Full suite PASS.
- [ ] **Step 5:** Commit (author lievcm) "fix: use raw strings for regular expressions".

### Task 6: Leading/trailing-decimal ion energies (Mirochill 67d32f4)

**Files:** Modify `srim/output.py` (`double_regex`); Test `tests/test_output.py`

- [ ] **Step 1: Failing tests** (parametrized) on `SRIM_Output()._read_ion(header)` with `header = b"Ion = Ni   Energy = %s keV"`:
  `(b".3", 300.0)`, `(b"3.", 3000.0)`, `(b"0.5", 500.0)`, `(b"20", 20000.0)`, `(b"1.5E+03", 1.5e6)` → `ion.energy == pytest.approx(expected)` (eV). Keep Mirochill's test from 67d32f4.
- [ ] **Step 2:** Run → FAIL for `.3`.
- [ ] **Step 3:** Cherry-pick `67d32f4` (regex `[-+]?(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][-+]?\d+)?`).
- [ ] **Step 4:** Full suite PASS.
- [ ] **Step 5:** Commit (author Mirochill) "fix: parse ion energies with a leading decimal point" + Co-authored-by George Apostolopoulos (fa239a1), SimonF24.

### Task 7: Comma-decimal SRIM output (felipebihaiek ed61a8f, philis-johan 547780e)

**Files:** Modify `srim/output.py`; Create fixture `test_files/comma/` (copy of `test_files/1/*.txt` with `.`→`,` in numeric fields); Test `tests/test_output.py`

**Interfaces:** Produces `srim.output._to_float(token: str | bytes) -> float` (replaces `,` with `.`), used by every `float(...)` on parsed SRIM tokens and by table reads (`np.genfromtxt` on bytes with `,`→`.`). Does **not** call `locale.setlocale` (felipebihaiek's global locale change is rejected).

- [ ] **Step 1: Failing tests:** for each parser class in `test_output.py`'s parametrized list, parsing `test_files/comma` yields arrays `np.testing.assert_allclose`-equal to parsing `test_files/1`; `Range(...)`'s total-ions value equals the dot version; `_to_float(b"1,5E+02") == 150.0`.
- [ ] **Step 2:** Run → FAIL.
- [ ] **Step 3:** Implement `_to_float` and apply in `SRIM_Output._read_num_ions`, `_read_table`, `Collision`, `SRResults` parsing.
- [ ] **Step 4:** Full suite PASS; `locale.getlocale()` unchanged after import (add assertion).
- [ ] **Step 5:** Commit `--author="philis-johan <philis@student.chalmers.se>"` "fix: parse SRIM output written with comma decimal separators" + Co-authored-by MrRobot2211 <felipebihaiek@gmail.com>.

### Task 8: SR unit prefixes incl. "m" (agoose77 f67b0c2)

**Files:** Modify `srim/output.py` (`SRResults`); Test `tests/test_output.py`

**Interfaces:** Module-level `energy_prefixes = {"eV": 1e-3, "keV": 1, "MeV": 1e3, "GeV": 1e6}`, `length_prefixes = {"A": 1e-4, "um": 1, "mm": 1e3, "cm": 1e4, "m": 1e6}` (values from f67b0c2: keV and µm base units).

- [ ] **Step 1: Failing tests:** existing `test_files/SRIM/SR_OUTPUT.txt` still parses to the same arrays; a fixture line containing a range in `m` (copy SR_OUTPUT.txt into `test_files/SRIM_m/`, change one row's range unit to `m`, e.g. `1.20 m`) gives `range == 1.2e6` µm for that row; a row with unit `nm` raises `SRIMOutputParseError` whose message contains `"nm"`.
- [ ] **Step 2:** Run → FAIL.
- [ ] **Step 3:** Cherry-pick `f67b0c2`; wrap dict lookups to raise `SRIMOutputParseError(f"unknown unit {unit!r}")`.
- [ ] **Step 4:** Full suite PASS.
- [ ] **Step 5:** Commit (author Angus Hollands) "fix: support all SRIM unit prefixes incl. metres in SR output".

### Task 9: Prefer xvfb-run (janmayer 0dd019e)

**Files:** Modify `srim/srim.py` (`_launch_command`); Test `tests/test_srim.py`

- [ ] **Step 1: Failing tests:** with `shutil.which` returning paths for both `wine` and `xvfb-run` → `["xvfb-run", "-a", "wine", os.path.join(".", "TRIM.exe")]`; only `wine` → `["wine", ...]` (Task 3 test stays); only `xvfb-run` (no wine) → native `[os.path.join(".", "TRIM.exe")]`.
- [ ] **Step 2:** Run → FAIL.
- [ ] **Step 3:** Extend `_launch_command` (logic from 0dd019e, using `shutil.which`).
- [ ] **Step 4:** Full suite PASS.
- [ ] **Step 5:** Commit `--author="Jan Mayer <...from 0dd019e>"` "feat: run wine under xvfb-run when available".

### Task 10: Positive autosave intervals (Mirochill cce778b)

- [ ] **Step 1–4:** `git cherry-pick cce778b` (brings its own test in `tests/test_srim.py`); run test before (on parent) to confirm it fails, after to confirm it passes; full suite PASS.
- [ ] **Step 5:** Commit kept as-is (author Mirochill); add CHANGELOG line.

### Task 11: Sputtered setting independent of ranges (Mirochill eb8263d)

- [ ] **Step 1–4:** Same procedure as Task 10 with `eb8263d`.
- [ ] **Step 5:** Commit kept (author Miro); CHANGELOG line.

### Task 12: Notebook "m"-prefix + misc already-in-history check

- [ ] **Step 1:** `git log --oneline main | grep -E "40a1f67|73205cf"` confirms agoose77/ASamarkRoth notebook fix and yaml warning fix are already present; `git cherry -v main <fork-branch>` for every fork branch listed in the spec's fix table shows no remaining `+` commits except the ones deliberately deferred (FilipovicLado, elmitsi, jesse.carter, jphilis EXYZ part → Plan 3).
- [ ] **Step 2:** Record the reviewed-but-deferred list in `docs/changes-from-pysrim.md` (created in Task 15). No commit on its own.

### Task 13: ruff + pre-commit, one formatting commit

**Files:** Create `.pre-commit-config.yaml`; Modify `pyproject.toml` (`[tool.ruff]` target-version `py311`, line-length 100, lint select `E,F,W,I,UP,B,NPY,PD`); Create `.git-blame-ignore-revs`

- [ ] **Step 1:** `.pre-commit-config.yaml` hooks: `ruff-check --fix`, `ruff-format`, `end-of-file-fixer`, `trailing-whitespace` (exclude `test_files/`, `*.IN`, `*.txt` fixtures — SRIM files need CRLF and exact bytes), `check-yaml`, `check-toml`, `check-added-large-files --maxkb=1024`, `uv-lock` (astral-sh/uv-pre-commit).
- [ ] **Step 2:** `uv run ruff format . && uv run ruff check --fix .`; fix remaining lint by hand (no behaviour change).
- [ ] **Step 3:** `uv run pytest` full suite PASS on 3.11 and 3.15; `uv run pre-commit run --all-files` → all hooks pass; `git diff --stat test_files` is empty.
- [ ] **Step 4:** Commit "style: format with ruff"; add its SHA to `.git-blame-ignore-revs`; commit "chore: add pre-commit with ruff".

### Task 14: GitHub Actions CI

**Files:** Create `.github/workflows/ci.yml`

- [ ] **Step 1:** Jobs: `lint` (uv, `pre-commit run --all-files`); `test` matrix `python: [3.11, 3.12, 3.13, 3.14, 3.15]` × `os: [ubuntu-latest, windows-latest, macos-latest]` running `uv run --python ${{ matrix.python }} pytest --cov=srim --cov-report=xml --cov-fail-under=70` (floor = current baseline 68 % rounded up after new tests; Plans 2–4 raise it to 85 %); `build` (`uv build`, upload `dist/` artifact). Trigger: push to `main`, PRs. Use `astral-sh/setup-uv` pinned by SHA.
- [ ] **Step 2:** Push branch, open PR, `gh pr checks --watch` → all green.
- [ ] **Step 3:** Merge; enable branch protection on `main` requiring `lint` and `test` (`gh api -X PUT repos/FSIBT/pysrim2/branches/main/protection ...`).
- [ ] **Step 4:** Commit "ci: add lint, test matrix and build workflow".

### Task 15: README notice, changelog, license, agents file, Read the Docs

**Files:** Modify `README.md`, `LICENSE`, `docs/conf.py`, `docs/index.rst`, `docs/installation.rst`; Create `CHANGELOG.md`, `docs/changes-from-pysrim.md`, `AGENTS.md`, `CLAUDE.md` (content: `@AGENTS.md`), `.readthedocs.yaml` (delete `.readthedocs.yml`)

- [ ] **Step 1:** README top: admonition block — "**pysrim2 is a maintained continuation of [pysrim](https://gitlab.com/costrouc/pysrim) by Chris Ostrouchov.** See [what changed](https://pysrim2.readthedocs.io/en/latest/changes-from-pysrim.html)." Then: install `uv add pysrim2` / `pip install pysrim2`, `import srim` unchanged, cannot co-install with `pysrim`, SRIM non-commercial licence note, badges (PyPI, CI, docs). Remove GitLab/conda/Docker Hub/Travis badges and the "contributions on GitLab" note. Same notice at the top of `docs/index.rst`.
- [ ] **Step 2:** `docs/changes-from-pysrim.md`: table of integrated fixes (topic, original author, source fork/commit), deferred items with reason, breaking changes (Python ≥ 3.11, distribution name), migration (`pip uninstall pysrim && pip install pysrim2`).
- [ ] **Step 3:** `CHANGELOG.md` with `## [Unreleased]` populated from Tasks 2–14 (Added/Changed/Fixed/Removed).
- [ ] **Step 4:** `LICENSE`: add `Copyright (c) 2026 pysrim2 contributors` under the existing line.
- [ ] **Step 5:** `AGENTS.md` (short): setup `uv sync`, test `uv run pytest`, lint `uv run pre-commit run -a`, commit conventions (conventional prefixes, one concern per commit, CHANGELOG line), never commit SRIM binaries, fixtures in `test_files/` are byte-exact CRLF.
- [ ] **Step 6:** `.readthedocs.yaml` v2: `build.os: ubuntu-24.04`, `build.tools.python: "3.13"`, `build.jobs` using uv (`asdf`-free: `pip install uv` then `uv sync --group docs` and `uv run sphinx-build -b html docs $READTHEDOCS_OUTPUT/html`); `docs/conf.py`: project `pysrim2`, version from `srim.__version__`, theme `furo`, `myst_parser` enabled.
- [ ] **Step 7:** `uv run --group docs sphinx-build -W -b html docs /tmp/pysrim2-docs` → builds with no warnings.
- [ ] **Step 8:** Commit in three commits: "docs: credit pysrim and document changes", "docs: add changelog and agents guide", "docs: build on Read the Docs with uv".
- [ ] **Step 9 (user, see setup guide):** import FSIBT/pysrim2 on Read the Docs as project `pysrim2`; verify `https://pysrim2.readthedocs.io/en/latest/` returns 200.

### Task 16: Release workflow and 0.6.0

**Files:** Create `.github/workflows/release.yml`; Modify `pyproject.toml` version, `CHANGELOG.md`

- [ ] **Step 1:** `release.yml` on `push: tags: ["v*"]`: job `build` (uv build, check tag == `v` + project version, upload artifact); job `publish` with `environment: pypi`, `permissions: id-token: write`, `pypa/gh-action-pypi-publish` (SHA-pinned); job `github-release` creating a release whose body is the matching CHANGELOG section. Also `workflow_dispatch` input `testpypi: true` publishing to TestPyPI via environment `testpypi`.
- [ ] **Step 2 (needs user's PyPI/TestPyPI pending-publisher setup):** run `gh workflow run release.yml -f testpypi=true`; verify `uv pip install --index-url https://test.pypi.org/simple/ --extra-index-url https://pypi.org/simple/ pysrim2==0.6.0.dev0` then `python -c "import srim; print(srim.__version__)"`.
- [ ] **Step 3:** Set version `0.6.0`, move Unreleased → `## [0.6.0] - <date>` in CHANGELOG; commit "release: 0.6.0"; `git tag -a v0.6.0 -m "pysrim2 0.6.0"`; push tag **after user confirms**.
- [ ] **Step 4:** Verify: `uv run --with pysrim2==0.6.0 --no-project python -c "import srim; print(srim.__version__)"` → `0.6.0`; GitHub release exists; RTD shows `0.6.0` under versions.
- [ ] **Step 5:** Bump to `0.7.0.dev0`, commit "chore: start 0.7.0 development".
