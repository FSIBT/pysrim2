import builtins
import json
import os
import re
import shutil
import subprocess
import sys
import textwrap

import pandas as pd
import pytest
import yaml

from srim.output import Collision, Results

pa = pytest.importorskip("pyarrow")
pq = pytest.importorskip("pyarrow.parquet")
pytest.importorskip("tables")

D = "test_files"
O = os.path.join(D, "outputs-0.8")  # noqa: E741
SICC = os.path.join(D, "SiC-C")
# every table except collision: label -> (fixture directory, tables)
FIXTURES = {
    "1": ["ioniz", "vacancy", "e2recoil", "phonon", "range", "range3d"],
    "SiC-C": ["ioniz", "vacancy", "novac", "e2recoil", "phonon", "range"],
    "events": ["exyz", "backscat", "transmit", "sputter"],
}
CASES = [(d, t) for d, ts in FIXTURES.items() for t in ts]

pytestmark = pytest.mark.filterwarnings("ignore:.*stray recoil")


def ids(case):
    return f"{case[0]}-{case[1]}"


@pytest.fixture(scope="module")
def events_dir(tmp_path_factory):
    """A result folder that has the event files (from different fixture runs)"""
    path = tmp_path_factory.mktemp("events")
    for name in os.listdir(os.path.join(D, "1")):
        if os.path.isfile(os.path.join(D, "1", name)):
            shutil.copy(os.path.join(D, "1", name), path)
    for folder, name in [("exyz", "EXYZ.txt"), ("thin", "BACKSCAT.txt"),
                         ("thin", "TRANSMIT.txt"), ("sputter", "SPUTTER.txt")]:  # fmt: skip
        shutil.copy(os.path.join(O, folder, name), path)
    return str(path)


@pytest.fixture(scope="module")
def cache(events_dir):
    results = {}

    def get(label):
        if label not in results:
            directory = events_dir if label == "events" else os.path.join(D, label)
            results[label] = Results(directory)
        return results[label]

    return get


def check_frames(actual, expected):
    """Equal frames; numeric column dtypes must agree in kind (only object vs string
    columns, which differ by pandas version, are relaxed)"""
    assert list(actual.columns) == list(expected.columns)
    for column in expected.columns:
        if expected[column].dtype != object:
            assert actual[column].dtype.kind == expected[column].dtype.kind, column
    pd.testing.assert_frame_equal(actual, expected, check_dtype=False)


@pytest.mark.parametrize("case", CASES, ids=ids)
def test_parquet_round_trip_and_metadata(case, cache, tmp_path):
    directory, name = case
    results = cache(directory)
    spec = {"ion": "C", "energy_keV": 4500}
    results.to_parquet(tmp_path, spec=spec, include=[name])
    assert [p.name for p in tmp_path.iterdir()] == [f"{name}.parquet"]
    path = tmp_path / f"{name}.parquet"
    check_frames(pd.read_parquet(path), results.to_dataframes()[name])
    document = json.loads(pq.read_schema(path).metadata[b"pysrim2"])
    assert document["table"] == name
    assert document["spec"] == spec
    assert document["pysrim2_version"]
    assert re.fullmatch(r"\d{4}-\d\d-\d\dT\d\d:\d\d:\d\dZ", document["exported_utc"])
    assert "source" in document["metadata"] or name == "novac"


@pytest.mark.parametrize("case", CASES, ids=ids)
def test_hdf5_round_trip_and_metadata(case, cache, tmp_path):
    directory, name = case
    results = cache(directory)
    path = tmp_path / "out.h5"
    results.to_hdf5(path, spec={"a": 1}, include=[name])
    check_frames(pd.read_hdf(path, name), results.to_dataframes()[name])
    with pd.HDFStore(path, mode="r") as store:
        assert store.keys() == [f"/{name}"]
        document = json.loads(store.get_storer(name).attrs.pysrim2)
    assert document["table"] == name
    assert document["spec"] == {"a": 1}
    assert document["metadata"] == json.loads(json.dumps(document["metadata"]))


@pytest.mark.parametrize("case", CASES, ids=ids)
def test_csv_round_trip(case, cache, tmp_path):
    directory, name = case
    results = cache(directory)
    results.to_csv(tmp_path, include=[name])
    assert sorted(p.name for p in tmp_path.iterdir()) == sorted([f"{name}.csv", "metadata.yaml"])
    back = pd.read_csv(tmp_path / f"{name}.csv")
    check_frames(back, results.to_dataframes()[name])
    meta = yaml.safe_load((tmp_path / "metadata.yaml").read_text())
    assert meta["spec"] is None
    assert meta["pysrim2_version"]
    assert set(meta["tables"]) == {name}


def test_default_selection_excludes_collision(cache, tmp_path):
    results = cache("SiC-C")
    written = results.to_parquet(tmp_path / "p")
    assert set(written) == {"ioniz", "vacancy", "novac", "e2recoil", "phonon", "range"}
    results.to_csv(tmp_path / "c")
    assert not (tmp_path / "c" / "collision.csv").exists()
    results.to_hdf5(tmp_path / "x.h5")
    with pd.HDFStore(tmp_path / "x.h5", mode="r") as store:
        assert "/collision" not in store.keys()
        assert "/ioniz" in store.keys()


def test_include_selects_and_validates(cache, tmp_path):
    results = cache("SiC-C")
    assert set(results.to_parquet(tmp_path, include=("ioniz", "range"))) == {"ioniz", "range"}
    assert set(results.to_csv(tmp_path / "s", include="phonon")) == {"phonon"}
    with pytest.raises(ValueError, match="unknown table"):
        results.to_parquet(tmp_path, include=["nope"])
    with pytest.raises(ValueError, match="not available"):
        results.to_parquet(tmp_path, include=["range3d"])


def test_collision_opt_in_all_formats(cache, tmp_path):
    results = cache("SiC-C")
    expected = results.to_dataframes(include=["collision"])["collision"]
    results.to_parquet(tmp_path / "p", include=["collision"])
    check_frames(pd.read_parquet(tmp_path / "p" / "collision.parquet"), expected)
    results.to_hdf5(tmp_path / "c.h5", include=["collision", "range"])
    check_frames(pd.read_hdf(tmp_path / "c.h5", "collision"), expected)
    results.to_csv(tmp_path / "c", include=["collision"])
    back = pd.read_csv(tmp_path / "c" / "collision.csv")
    check_frames(back, expected)


def test_collision_chunked_methods_match(tmp_path):
    collision = Collision(SICC)
    expected = collision.df
    n = collision.to_parquet(tmp_path / "a.parquet", chunk_rows=1000, spec={"x": 1})
    assert n == len(expected)
    with pq.ParquetFile(tmp_path / "a.parquet") as pf:
        assert pf.metadata.num_row_groups == -(-n // 1000) > 1
        assert json.loads(pf.schema_arrow.metadata[b"pysrim2"])["spec"] == {"x": 1}
        check_frames(pf.read().to_pandas(), expected)
    assert collision.to_csv(tmp_path / "a.csv", chunk_rows=777) == n
    back = pd.read_csv(tmp_path / "a.csv")
    check_frames(back, expected)
    with pytest.raises(ValueError, match="chunk_rows"):
        collision.to_parquet(tmp_path / "b.parquet", chunk_rows=0)


def test_hdf5_collision_appended_in_chunks(cache, tmp_path):
    results = cache("SiC-C")
    from srim import export

    export.results_to_hdf5(results, tmp_path / "c.h5", include=["collision"], chunk_rows=1000)
    expected = results.to_dataframes(include=["collision"])["collision"]
    check_frames(pd.read_hdf(tmp_path / "c.h5", "collision"), expected)


@pytest.mark.parametrize(("module", "extra", "method"), [
    ("pyarrow", "parquet", "to_parquet"),
    ("tables", "hdf5", "to_hdf5"),
])  # fmt: skip
def test_missing_extra_raises_import_error(module, extra, method, cache, tmp_path, monkeypatch):
    real_import = builtins.__import__

    def fake_import(name, *args, **kwargs):
        if name == module or name.startswith(module + "."):
            raise ImportError(f"No module named {name!r}")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", fake_import)
    for key in [k for k in sys.modules if k == module or k.startswith(module + ".")]:
        monkeypatch.delitem(sys.modules, key)
    results = cache("SiC-C")
    with pytest.raises(ImportError, match=re.escape(f"pysrim2[{extra}]")):
        getattr(results, method)(tmp_path / "out")
    if extra == "parquet":
        with pytest.raises(ImportError, match=re.escape("pysrim2[parquet]")):
            Collision(SICC).to_parquet(tmp_path / "c.parquet")


def synthetic_collision(path, target_bytes):
    """COLLISON.txt made of renumbered copies of the SiC-C file (about target_bytes)"""
    with open(os.path.join(SICC, "COLLISON.txt"), "rb") as f:
        data = f.read()
    copies = max(1, round(target_bytes / len(data)))
    patterns = [
        (re.compile(rb"(?m)^\xb3(\d{5})\xb3"), b"\xb3%05d\xb3"),
        (re.compile(rb"Ion Numb (\d{5})="), b"Ion Numb %05d="),
        (re.compile(rb"For Ion (\d{7}):"), b"For Ion %07d:"),
    ]
    with open(path, "wb") as out:
        for k in range(copies):
            chunk = data
            for regex, template in patterns:
                chunk = regex.sub(
                    lambda m, t=template, k=k: t % (int(m.group(1)) + 1000 * k), chunk
                )
            out.write(chunk)
    return 1000 * copies


MEASURE = textwrap.dedent("""
    import resource, sys, time, warnings
    import pyarrow.parquet, pandas  # noqa: baseline: libraries already loaded
    from srim.output import Collision
    warnings.simplefilter("ignore")
    unit = 1 if sys.platform == "darwin" else 1024
    rss = lambda: resource.getrusage(resource.RUSAGE_SELF).ru_maxrss * unit / 2**20
    c = Collision(sys.argv[1])
    before = rss()
    t = time.perf_counter()
    n = c.to_parquet(sys.argv[2], chunk_rows=int(sys.argv[3]))
    print(n, round(before, 1), round(rss(), 1), round(time.perf_counter() - t, 2))
""")


def test_large_collision_export_is_chunked(tmp_path):
    pytest.importorskip("resource")  # the child process measures RSS with it (POSIX only)
    ions = synthetic_collision(tmp_path / "COLLISON.txt", 20e6)
    collision = Collision(tmp_path)
    events = list(collision.iter_events())
    assert events[-1]["ion"] == ions
    expected = len(events)
    del events
    out = tmp_path / "events.parquet"
    script = tmp_path / "measure.py"
    script.write_text(MEASURE)
    run = subprocess.run(
        [sys.executable, "-I", str(script), str(tmp_path), str(out), "1000"],
        capture_output=True, text=True, check=True,
        env={**os.environ, "PYTHONPATH": os.getcwd()},
    )  # fmt: skip
    rows, before, after, seconds = run.stdout.split()
    assert int(rows) == expected
    with pq.ParquetFile(out) as pf:
        assert pf.metadata.num_rows == expected
        assert pf.metadata.num_row_groups == -(-expected // 1000) > 1
        assert max(pf.metadata.row_group(i).num_rows for i in range(pf.num_row_groups)) <= 1000
    # peak resident memory grows by far less than the 20 MB text / the full frame
    print(f"peak RSS {before} -> {after} MiB, {seconds} s for {rows} events")
    assert float(after) - float(before) < 100


def test_all_none_chunk_keeps_schema_and_gives_nulls(tmp_path):
    collision = Collision(SICC)
    ions = [e for e in collision.iter_events() if e["kind"] == "ion"][:6]
    assert all(e["atomic_number"] is None and e["vacancy"] is None for e in ions)
    collision.iter_events = lambda: iter(ions)  # every chunk has all-None nullable columns
    path = tmp_path / "n.parquet"
    assert collision.to_parquet(path, chunk_rows=2) == 6
    with pq.ParquetFile(path) as pf:
        assert pf.metadata.num_row_groups == 3
        for field in ("atomic_number", "vacancy", "replacement"):
            assert pf.schema_arrow.field(field).type == pa.float64()
        table = pf.read()
    assert table["atomic_number"].null_count == 6
    assert table["kind"].to_pylist() == ["ion"] * 6


def test_exported_source_is_file_name_only(cache, tmp_path):
    results = cache("SiC-C")
    results.to_parquet(tmp_path / "p", include=["ioniz", "collision"])
    for name in ("ioniz", "collision"):
        meta = pq.read_schema(tmp_path / "p" / f"{name}.parquet").metadata[b"pysrim2"]
        source = json.loads(meta)["metadata"]["source"]
        assert "/" not in source and "\\" not in source
        assert source == os.path.basename(getattr(results, name).metadata["source"])
    results.to_hdf5(tmp_path / "x.h5", include=["ioniz"])
    with pd.HDFStore(tmp_path / "x.h5", mode="r") as store:
        assert (
            json.loads(store.get_storer("ioniz").attrs.pysrim2)["metadata"]["source"] == "IONIZ.txt"
        )
    results.to_csv(tmp_path / "c", include=["ioniz"])
    assert (
        yaml.safe_load((tmp_path / "c" / "metadata.yaml").read_text())["tables"]["ioniz"]["source"]
        == "IONIZ.txt"
    )
    full = results.ioniz.metadata["source"]  # in memory the full path stays
    assert os.path.basename(full) == "IONIZ.txt"
    assert full != "IONIZ.txt"


def test_results_chunk_rows_is_passed_and_validated(cache, tmp_path):
    results = cache("SiC-C")
    results.to_parquet(tmp_path, include=["collision"], chunk_rows=30_000)
    with pq.ParquetFile(tmp_path / "collision.parquet") as pf:
        assert pf.metadata.num_row_groups == 2  # 59 633 events
    for method in ("to_parquet", "to_csv", "to_hdf5"):
        with pytest.raises(ValueError, match="chunk_rows"):
            getattr(results, method)(tmp_path / "bad", chunk_rows=0)
