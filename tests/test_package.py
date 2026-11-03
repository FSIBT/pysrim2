import importlib.metadata
import srim


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
