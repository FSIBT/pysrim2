"""A module-level extractor for the app's scan tests, referenced as ``python:tests.app.extractors:depth_sum``.

It only has to be importable by name (the round-trip test reads a scan file that names it);
no scan in the tests runs it.
"""


def depth_sum(results, point=None):
    """A stand-in extractor: one column, the number of ions in the results."""
    return {"depth_sum_nm": 0.0}
