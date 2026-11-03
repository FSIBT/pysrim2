import importlib.metadata
import shutil
import subprocess
import tarfile

import pytest

import srim

# Repository paths that must not ship in the source distribution.
SDIST_EXCLUDED = ("CLAUDE.md", "docs/superpowers", ".github", "tests")


def test_version_matches_distribution():
    assert srim.__version__ == importlib.metadata.version("pysrim2")


def test_element_data_is_packaged():
    assert srim.ElementDB.lookup("Si")["z"] == 14


def test_no_invalid_escape_warnings():
    import pathlib
    import warnings

    package_dir = pathlib.Path(srim.__file__).parent
    sources = sorted(package_dir.rglob("*.py"))
    assert sources
    for path in sources:
        source = path.read_text(encoding="utf-8")
        with warnings.catch_warnings():
            # Invalid escapes are SyntaxWarning on 3.12+ and DeprecationWarning on 3.11.
            warnings.simplefilter("error", SyntaxWarning)
            warnings.simplefilter("error", DeprecationWarning)
            compile(source, str(path), "exec")


def test_sdist_contents(tmp_path):
    if shutil.which("uv") is None:
        pytest.skip("uv is required to build the sdist")
    subprocess.run(
        ["uv", "build", "--sdist", "--out-dir", str(tmp_path)],
        check=True,
        capture_output=True,
    )
    (sdist,) = tmp_path.glob("*.tar.gz")
    with tarfile.open(sdist) as tar:
        # Members are prefixed with "<name>-<version>/"; drop that top-level directory.
        names = [member.name.split("/", 1)[1] for member in tar.getmembers() if "/" in member.name]
    assert names
    for excluded in SDIST_EXCLUDED:
        leaked = [name for name in names if name == excluded or name.startswith(excluded + "/")]
        assert not leaked, f"{excluded} is in the sdist"
