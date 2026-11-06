"""The result cache of scans: keys, entries, locks and clear_cache (no SRIM, no scan).

The key tests build specs with every field that goes into ``TRIM.IN``. The entry tests store
and read run folders directly; the lock tests take a key's lock in the same process, where a
second holder does not wait. Processes that write the same cache are tested with the scan
(``test_cache_runs.py``).
"""

import copy
import logging
import multiprocessing
import os
import secrets

import pytest

from srim.scan.cache import RunCache, cache_key, clear_cache, materialise
from srim.spec import SETTINGS_SCHEMA, RunSpec, SpecError

#: seconds to wait for a child process
WAIT_SECONDS = 120


# -- keys ----------------------------------------------------------------------------------

#: a spec with every kind of field that goes into TRIM.IN (all settings, two layers, masses)
FULL = {
    "version": 1,
    "ion": {"symbol": "B", "mass_amu": 10.5, "energy_keV": 20.0, "angle_deg": 5.0},
    "number_ions": 100,
    "calculation": 1,
    "seed": 3,
    "settings": {
        **{key: default for key, (_, default) in SETTINGS_SCHEMA.items()},
        "plot_xmin": 1.0,
        "plot_xmax": 10.0,
    },
    "target": [
        {
            "name": "Si",
            "thickness_nm": 100.0,
            "density_g_cm3": 2.33,
            "phase": 0,
            "elements": [
                {
                    "symbol": "Si",
                    "stoich": 1.0,
                    "displacement_eV": 25.0,
                    "lattice_eV": 2.0,
                    "surface_eV": 3.0,
                    "mass_amu": 28.2,
                }
            ],
        },
        {
            "name": "Al",
            "thickness_nm": 50.0,
            "density_g_cm3": 2.7,
            "phase": 1,
            "elements": [
                {"symbol": "Al", "stoich": 0.5},
                {"symbol": "Si", "stoich": 0.5, "displacement_eV": 20.0},
            ],
        },
    ],
}


def leaves(node, path=()):
    """Every leaf of a nested dict/list: ``(path, value)``."""
    if isinstance(node, dict):
        for key, value in node.items():
            yield from leaves(value, path + (key,))
    elif isinstance(node, list):
        for index, value in enumerate(node):
            yield from leaves(value, path + (index,))
    else:
        yield path, node


def other_values(value):
    """Values to try in place of ``value`` (the first one the schema accepts is used)."""
    if isinstance(value, str):
        return [value + "x", "Al", "Si"]
    if isinstance(value, bool):
        return [not value]
    if isinstance(value, int):
        return [value + 1, value - 1, 0, 2]
    if isinstance(value, float):
        return [value + 0.5, value - 0.5, value + 1.0, value - 1.0, value * 0.5, value * 1.5]
    return []


def with_leaf(data, path, value):
    new = copy.deepcopy(data)
    node = new
    for step in path[:-1]:
        node = node[step]
    node[path[-1]] = value
    return new


def test_the_key_changes_for_every_field_that_goes_into_trim_in():
    base = RunSpec.from_dict(FULL)
    data = base.to_dict()
    base_key = cache_key(base, 3)
    changed = []
    for path, value in leaves(data):
        if path in (("version",), ("seed",)):  # not in TRIM.IN; the key takes its own seed
            continue
        spec = None
        for candidate in other_values(value):
            if type(candidate) is type(value) and candidate == value:
                continue
            try:
                spec = RunSpec.from_dict(with_leaf(data, path, candidate))
            except SpecError:
                continue
            break
        assert spec is not None, f"no valid other value for {path}"
        assert cache_key(spec, 3) != base_key, f"{path} is not part of the key"
        changed.append(path)
    assert ("number_ions",) in changed
    assert ("ion", "energy_keV") in changed and ("ion", "mass_amu") in changed
    assert ("target", 1, "elements", 1, "displacement_eV") in changed
    assert all(("settings", key) in changed for key in SETTINGS_SCHEMA)
    assert len(changed) > 40


def test_the_seed_is_part_of_the_key_and_the_spec_seed_is_not():
    base = RunSpec.from_dict(FULL)
    assert cache_key(base, 1) != cache_key(base, 2)
    assert cache_key(base.with_overrides(["seed=99"]), 3) == cache_key(base, 3)


def test_provenance_is_not_part_of_the_key(tmp_path):
    base = RunSpec.from_dict(FULL)
    base.to_yaml(tmp_path / "a.yaml", {"host": "one", "started_utc": "2026-01-01T00:00:00Z"})
    base.to_yaml(tmp_path / "b.yaml", {"host": "two", "srim_sha256": "0" * 64})
    keys = {cache_key(RunSpec.from_yaml(tmp_path / name), 3) for name in ("a.yaml", "b.yaml")}
    assert keys == {cache_key(base, 3)}


def test_equal_integers_and_floats_give_the_same_key():
    as_int = RunSpec.from_dict({**FULL, "ion": {**FULL["ion"], "energy_keV": 30}})
    as_float = RunSpec.from_dict({**FULL, "ion": {**FULL["ion"], "energy_keV": 30.0}})
    assert cache_key(as_int, 3) == cache_key(as_float, 3)
    overridden = RunSpec.from_dict(FULL).with_overrides(["ion.energy_keV=30"])
    assert cache_key(overridden, 3) == cache_key(as_float, 3)
    assert cache_key(as_float, 3) != cache_key(RunSpec.from_dict(FULL), 3)


# -- entries -------------------------------------------------------------------------------


def make_run(folder, sha="a" * 64, text="1 2 3\n"):
    """A run's output folder: outputs, run.yaml (with a provenance sha) and an error.log."""
    folder.mkdir(parents=True)
    (folder / "RANGE.txt").write_text(text)
    (folder / "TRIM.IN").write_text("TRIM.IN\n")
    (folder / "sub").mkdir()
    (folder / "sub" / "deep.txt").write_text("deep\n")
    (folder / "error.log").write_text("attempt 1: boom\n")
    provenance = {"srim_sha256": sha} if sha is not None else {}
    RunSpec.from_dict(FULL).to_yaml(folder / "run.yaml", provenance)
    return folder


def new_key():
    return secrets.token_hex(32)


def test_a_stored_run_is_found_and_its_files_come_back(tmp_path):
    cache = RunCache(tmp_path / "cache")
    key = new_key()
    assert cache.lookup(key) is None
    run = make_run(tmp_path / "run")
    entry = cache.store(key, run)
    assert cache.lookup(key) == entry
    assert (entry / ".complete").is_file()
    assert not (entry / "error.log").exists()  # a failed attempt's log is not part of a run
    out = tmp_path / "out"
    materialise(entry, out)
    assert (out / "RANGE.txt").read_text() == "1 2 3\n"
    assert (out / "sub" / "deep.txt").read_text() == "deep\n"
    assert not (out / ".complete").exists()


def test_editing_a_point_folder_leaves_the_entry_unchanged(tmp_path):
    cache = RunCache(tmp_path / "cache")
    cache.prepare()
    key = new_key()
    run = make_run(tmp_path / "run")
    entry = cache.store(key, run)
    (run / "run.yaml").write_text("edited\n")
    (run / "RANGE.txt").write_text("edited\n")
    assert (entry / "RANGE.txt").read_text() == "1 2 3\n"
    assert "edited" not in (entry / "run.yaml").read_text()
    out = tmp_path / "out"
    materialise(entry, out)
    (out / "run.yaml").write_text("edited\n")
    (out / "TRIM.IN").write_text("edited\n")
    assert "edited" not in (entry / "run.yaml").read_text()
    assert (entry / "TRIM.IN").read_text() == "TRIM.IN\n"


@pytest.mark.skipif(os.name != "posix", reason="hard links are checked with inode numbers")
def test_only_large_outputs_are_hard_linked(tmp_path):
    cache = RunCache(tmp_path / "cache")
    cache.prepare()
    key = new_key()
    run = make_run(tmp_path / "run")
    big = b"x" * ((1 << 20) + 1)  # just over the hard-link threshold
    (run / "BIG.bin").write_bytes(big)
    (run / "BIG.IN").write_bytes(big)  # an input file: always copied
    entry = cache.store(key, run)
    assert os.path.samefile(entry / "BIG.bin", run / "BIG.bin")
    assert not os.path.samefile(entry / "BIG.IN", run / "BIG.IN")
    assert not os.path.samefile(entry / "RANGE.txt", run / "RANGE.txt")
    assert not os.path.samefile(entry / "run.yaml", run / "run.yaml")
    out = tmp_path / "out"
    materialise(entry, out)
    assert os.path.samefile(out / "BIG.bin", entry / "BIG.bin")
    assert not os.path.samefile(out / "run.yaml", entry / "run.yaml")
    assert not os.path.samefile(out / "RANGE.txt", entry / "RANGE.txt")


def test_an_entry_without_its_marker_is_not_a_hit(tmp_path):
    cache = RunCache(tmp_path / "cache")
    cache.prepare()
    key = new_key()
    partial = cache.entry_dir(key)
    partial.mkdir(parents=True)
    (partial / "RANGE.txt").write_text("half written")
    assert cache.lookup(key) is None
    cache.store(key, make_run(tmp_path / "run", text="whole"))
    assert (cache.entry_dir(key) / "RANGE.txt").read_text() == "whole"


def test_another_srim_build_is_a_miss_with_one_warning_and_the_entry_is_kept(tmp_path, caplog):
    cache = RunCache(tmp_path / "cache")
    key = new_key()
    entry = cache.store(key, make_run(tmp_path / "run", sha="a" * 64))
    with caplog.at_level(logging.WARNING, logger="srim.scan.cache"):
        assert cache.lookup(key, "b" * 64) is None
        assert cache.lookup(key, "b" * 64) is None
    warnings = [r for r in caplog.records if r.name == "srim.scan.cache"]
    assert len(warnings) == 1
    assert entry.is_dir() and cache.lookup(key, "a" * 64) == entry
    assert cache.lookup(key, None) == entry


def test_a_run_of_another_srim_build_replaces_the_entry(tmp_path):
    cache = RunCache(tmp_path / "cache")
    key = new_key()
    cache.store(key, make_run(tmp_path / "old", sha="a" * 64, text="old"))
    stored = cache.store(key, make_run(tmp_path / "new", sha="b" * 64, text="new"))
    assert (stored / "RANGE.txt").read_text() == "new"
    assert _recorded(stored) == "b" * 64
    assert not list((cache.root / "tmp").glob(f"{key}-*"))


def test_a_run_of_the_same_srim_build_keeps_the_entry(tmp_path):
    cache = RunCache(tmp_path / "cache")
    key = new_key()
    cache.store(key, make_run(tmp_path / "old", sha="a" * 64, text="old"))
    stored = cache.store(key, make_run(tmp_path / "new", sha="a" * 64, text="new"))
    assert (stored / "RANGE.txt").read_text() == "old"


def _recorded(entry):
    import yaml

    return yaml.safe_load((entry / "run.yaml").read_text())["provenance"]["srim_sha256"]


def test_an_unknown_srim_build_on_either_side_matches(tmp_path):
    cache = RunCache(tmp_path / "cache")
    unknown = new_key()
    cache.store(unknown, make_run(tmp_path / "run1", sha=None))
    assert cache.lookup(unknown, "b" * 64) is not None
    known = new_key()
    cache.store(known, make_run(tmp_path / "run2", sha="a" * 64))
    assert cache.lookup(known, None) is not None


def test_a_writer_killed_midway_leaves_no_entry_and_the_next_write_recovers(tmp_path):
    root = tmp_path / "cache"
    key = new_key()
    run = make_run(tmp_path / "run")
    ctx = multiprocessing.get_context("spawn")
    child = ctx.Process(target=_die_while_storing, args=(str(root), str(run), key))
    child.start()
    child.join(timeout=WAIT_SECONDS)
    assert not child.is_alive() and child.exitcode == 3
    cache = RunCache(root)
    assert cache.lookup(key) is None
    assert not cache.entry_dir(key).exists()
    assert list((root / "tmp").glob(f"{key}-*"))  # the leftover of the killed writer
    cache.store(key, run)
    assert cache.lookup(key) is not None
    assert not list((root / "tmp").glob(f"{key}-*"))


def _die_while_storing(root, source, key):
    """Child process: exits (code 3) just before the entry is renamed into place."""
    import srim.scan.cache as cache_module

    def die(*args, **kwargs):
        os._exit(3)

    cache_module.os.replace = die
    cache_module.RunCache(root).store(key, source)


def test_a_key_is_locked_while_held_and_clear_cache_keeps_it(tmp_path):
    root = tmp_path / "cache"
    cache = RunCache(root)
    cache.prepare()
    key = new_key()
    cache.store(key, make_run(tmp_path / "run"))
    lock = cache.try_lock(key)
    assert lock is not None
    try:
        assert cache.try_lock(key) is None  # a second holder does not wait
        assert clear_cache(root) == 0
        assert cache.lookup(key) is not None
    finally:
        lock.release()
    assert clear_cache(root) == 1
    assert cache.lookup(key) is None


def test_clear_cache_removes_entries_and_keeps_the_tag_and_the_locks(tmp_path):
    root = tmp_path / "cache"
    cache = RunCache(root)
    cache.prepare()
    keys = [new_key(), new_key()]
    for number, key in enumerate(keys):
        cache.store(key, make_run(tmp_path / f"run{number}"))
    assert clear_cache(root) == 2
    assert all(cache.lookup(key) is None for key in keys)
    assert (root / "CACHEDIR.TAG").is_file()
    assert not [
        shard for shard in root.iterdir() if shard.name not in ("CACHEDIR.TAG", "locks", "tmp")
    ]
    assert all((root / "locks" / f"{key}.lock").is_file() for key in keys)  # lock files stay


def test_clear_cache_leaves_strays_alone_and_warns(tmp_path, caplog):
    root = tmp_path / "cache"
    cache = RunCache(root)
    cache.prepare()
    key = new_key()
    cache.store(key, make_run(tmp_path / "run"))
    stray_dir = root / "notes"
    stray_dir.mkdir()
    (stray_dir / "thesis.txt").write_text("keep me")
    (root / "zz").mkdir()  # not a shard name
    (root / "zz" / ("f" * 64)).mkdir()
    (root / "ab").mkdir()
    (root / "ab" / "not-a-key").mkdir()  # a shard holding something that is not an entry
    with caplog.at_level(logging.WARNING, logger="srim.scan.cache"):
        assert clear_cache(root) == 1
    assert (stray_dir / "thesis.txt").read_text() == "keep me"
    assert (root / "zz" / ("f" * 64)).is_dir()
    assert (root / "ab" / "not-a-key").is_dir()
    assert cache.lookup(key) is None
    messages = " ".join(r.getMessage() for r in caplog.records if r.name == "srim.scan.cache")
    for name in ("notes", "zz", "not-a-key"):
        assert name in messages


def test_clear_cache_refuses_a_folder_that_is_not_a_cache(tmp_path):
    folder = tmp_path / "documents"
    folder.mkdir()
    (folder / "thesis.txt").write_text("keep me")
    with pytest.raises(ValueError, match="not a pysrim2 result cache"):
        clear_cache(folder)
    assert (folder / "thesis.txt").read_text() == "keep me"


def test_scans_creating_the_same_cache_at_once_all_succeed(tmp_path):
    import threading

    root = tmp_path / "cache"
    barrier = threading.Barrier(8)
    errors = []

    def create():
        barrier.wait(timeout=10)
        try:
            RunCache(root).prepare()
        except Exception as exc:  # noqa: BLE001 - collected for the assertion
            errors.append(exc)

    for _ in range(20):
        threads = [threading.Thread(target=create) for _ in range(8)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=10)
        assert errors == []
        clear_cache(root)
        for p in sorted(root.rglob("*"), reverse=True):
            p.rmdir() if p.is_dir() else p.unlink()
        root.rmdir()


def test_a_cache_being_created_is_not_mistaken_for_a_foreign_folder(tmp_path):
    root = tmp_path / "cache"
    (root / "locks").mkdir(parents=True)
    (root / ".CACHEDIR.TAG.0123abcd").write_text("half")
    RunCache(root).prepare()
    assert (root / "CACHEDIR.TAG").is_file()
    foreign = tmp_path / "data"
    foreign.mkdir()
    (foreign / "notes.txt").write_text("mine")
    with pytest.raises(ValueError):
        RunCache(foreign).prepare()
