import shutil
import subprocess
import tomllib
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[1]


def _load_cff():
    return yaml.safe_load((ROOT / "CITATION.cff").read_text(encoding="utf-8"))


def _load_pyproject():
    return tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))


def _cff_display_name(author):
    if "family-names" not in author:
        return author["alias"]
    return f"{author['given-names']} {author['family-names']}"


def _git(*args):
    return subprocess.run(
        ["git", "-C", str(ROOT), *args],
        check=True,
        capture_output=True,
        text=True,
    ).stdout


def test_citation_cff_cites_the_original_paper():
    cff = _load_cff()

    assert cff["cff-version"] == "1.2.0"
    assert cff["title"] == "pysrim2"
    assert cff["repository-code"] == "https://github.com/FSIBT/pysrim2"
    assert cff["url"] == "https://pysrim2.readthedocs.io"
    assert cff["license"] == "MIT"
    assert cff["version"] == _load_pyproject()["project"]["version"]

    paper = cff["preferred-citation"]
    assert paper["type"] == "article"
    assert paper["doi"] == "10.21105/joss.00829"
    assert [a["family-names"] for a in paper["authors"]] == [
        "Ostrouchov",
        "Zhang",
        "Weber",
    ]
    assert paper["title"] == "pysrim: Automation, Analysis, and Plotting of SRIM Calculations"
    assert paper["journal"] == "Journal of Open Source Software"
    assert paper["volume"] == 3
    assert paper["issue"] == 28
    assert paper["start"] == 829
    assert paper["year"] == 2018


def test_authors_are_the_same_in_pyproject_and_citation_cff():
    pyproject_names = [a["name"] for a in _load_pyproject()["project"]["authors"]]
    cff_names = [_cff_display_name(a) for a in _load_cff()["authors"]]

    assert cff_names == pyproject_names


def test_every_git_author_is_listed():
    if shutil.which("git") is None:
        pytest.skip("git is not installed")
    if not (ROOT / ".git").exists():
        pytest.skip("not a git checkout")

    listed = {a["name"] for a in _load_pyproject()["project"]["authors"]}

    authors = set(_git("log", "--format=%aN").splitlines())
    trailers = _git("log", "--format=%(trailers:key=Co-authored-by,valueonly)").splitlines()
    co_authors = {line.split(" <", 1)[0].strip() for line in trailers if line.strip()}

    missing = sorted((authors | co_authors) - listed - {""})
    assert missing == []
