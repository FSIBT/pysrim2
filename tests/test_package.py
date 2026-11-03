import importlib.metadata
import srim

def test_version_matches_distribution():
    assert srim.__version__ == importlib.metadata.version("pysrim2")

def test_element_data_is_packaged():
    assert srim.ElementDB.lookup("Si")["z"] == 14
