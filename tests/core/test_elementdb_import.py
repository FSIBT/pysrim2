import importlib
import warnings

import srim.core.elementdb


def test_elementdb_import_emits_no_warnings():
    with warnings.catch_warnings():
        warnings.simplefilter("error")
        importlib.reload(srim.core.elementdb)
    assert srim.core.elementdb.ElementDB.lookup("Au")["z"] == 79
