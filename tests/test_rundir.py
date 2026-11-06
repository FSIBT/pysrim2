"""Isolated run directories, file locks and per-run wine prefixes (SRIM itself is mocked)."""

import concurrent.futures as cf
import multiprocessing
import os
import shutil
import stat
import subprocess
import sys
import tempfile
import threading
import time
from pathlib import Path

import pytest

import srim.rundir as rundir_module
from srim import SR, TRIM, Ion, Layer, Target
from srim._lock import FileLock
from srim.installer.wine import XVFB_ARGS
from srim.rundir import RunDir, clone_wine_prefix, disk_usage, install_lock_path
from srim.srim import SRIMRunError

TEST_FILES = Path(__file__).parent.parent / "test_files"


def make_srim(root):
    """A small stand-in for the SRIM install with the real layout."""
    root.mkdir()
    big = b"x" * 200_000
    (root / "TRIM.exe").write_bytes(big * 5)
    (root / "SRIM.exe").write_bytes(big * 3)
    (root / "TRIM.IN").write_text("old input")
    (root / "TRIMAUTO").write_text("1\n")
    (root / "TRIM.dat").write_text("d" * 7000)
    (root / "RANGE.txt").write_text("stale result")  # a legacy in-place run left this
    (root / ".pysrim2-installed").write_text("abc123\n")
    (root / "HELP.pdf").write_bytes(big * 4)
    (root / "Data").mkdir()
    (root / "Data" / "POTCOEF03.DAT").write_bytes(big * 3)
    (root / "Data" / "TRIM.cfg").write_text("cfg" * 300)
    (root / "Data" / "tiny.ico").write_bytes(b"i" * 700)
    (root / "SR Module").mkdir()
    (root / "SR Module" / "SRModule.exe").write_bytes(big)
    (root / "SR Module" / "SCOEF03.dat").write_bytes(big)
    (root / "SR Module" / "SR.IN").write_text("sr in")
    (root / "SRIM Outputs").mkdir()
    (root / "SRIM Outputs" / "SRIM Outputs.rtf").write_text("rtf")
    (root / "SRIM Outputs" / "RANGE_3D.txt").write_text("stale")
    (root / "SRIM Restore").mkdir()
    (root / "SRIM Restore" / "SRIM Restore.rtf").write_text("rtf")
    return root


def snapshot(root):
    """What "unchanged" means: the names, and the size and mtime of every regular file.
    Not link counts, ctimes or folder mtimes (creating a hard link changes those)."""
    out = {}
    for dirpath, dirs, files in os.walk(root):
        for name in dirs:
            out[os.path.relpath(os.path.join(dirpath, name), root)] = None
        for name in files:
            path = os.path.join(dirpath, name)
            st = os.lstat(path)
            out[os.path.relpath(path, root)] = (st.st_size, st.st_mtime_ns)
    return out


@pytest.fixture
def installed(tmp_path, monkeypatch):
    monkeypatch.setenv("PYSRIM2_HOME", str(tmp_path / "home"))
    monkeypatch.setenv("PYSRIM2_SRIM_DIR", str(make_srim(tmp_path / "srim")))
    monkeypatch.setattr(shutil, "which", lambda name: None)
    return tmp_path / "srim"


def same_inode(a, b):
    return os.path.samestat(os.stat(a), os.stat(b))


def test_layout_links_read_only_files_and_copies_written_ones(installed, tmp_path):
    with RunDir.create(tmp_path / "work", wine=False) as rd:
        for rel in ("TRIM.exe", "SRIM.exe", "HELP.pdf", "Data/POTCOEF03.DAT"):
            assert same_inode(installed / rel, rd.path / rel), rel
        for rel in (
            "TRIM.IN",
            "TRIMAUTO",
            "TRIM.dat",
            "Data/TRIM.cfg",
            "Data/tiny.ico",
            "SR Module/SRModule.exe",
            "SR Module/SCOEF03.dat",
            "SR Module/SR.IN",
            ".pysrim2-installed",
        ):
            assert (rd.path / rel).is_file()
            assert not same_inode(installed / rel, rd.path / rel), rel
        # stale results of the installed directory are not carried over
        assert not (rd.path / "RANGE.txt").exists()
        assert not (rd.path / "SRIM Outputs" / "RANGE_3D.txt").exists()
        assert (rd.path / "SRIM Outputs" / "SRIM Outputs.rtf").is_file()
        assert rd.wine_prefix is None
        assert rd.root.parent == tmp_path / "work"
    assert not rd.root.exists()


def test_writing_a_run_directory_does_not_touch_the_install(installed, tmp_path):
    before = snapshot(installed)
    with RunDir.create(tmp_path / "work", wine=False) as rd:
        (rd.path / "TRIM.IN").write_text("new")
        (rd.path / "RANGE.txt").write_text("new result")
        (rd.path / "SR Module" / "SR.IN").write_text("new")
    assert snapshot(installed) == before


def test_disk_usage_counts_hard_links_once(installed, tmp_path):
    total = sum(p.stat().st_size for p in installed.rglob("*") if p.is_file())
    with RunDir.create(tmp_path / "work", wine=False) as rd:
        used = rd.disk_usage()
        assert used == disk_usage(rd.root)
        print(f"install {total} bytes, run directory adds {used} bytes")
        assert used < total * 0.15
        (rd.path / "RANGE.txt").write_bytes(b"r" * 1000)
        assert rd.disk_usage() == used + 1000


def test_default_base_is_data_dir_runs(installed, tmp_path):
    with RunDir.create(wine=False) as rd:
        assert rd.root.parent == tmp_path / "home" / "runs"


def test_keep_and_failure_keep_the_directory(installed, tmp_path):
    with RunDir.create(tmp_path / "work", keep=True, wine=False) as rd:
        pass
    assert rd.path.is_dir()
    with (
        pytest.raises(RuntimeError) as excinfo,
        RunDir.create(tmp_path / "work", wine=False) as bad,
    ):
        raise RuntimeError("boom")
    assert bad.path.is_dir()
    assert str(bad.path) in "\n".join(excinfo.value.__notes__)
    rd.cleanup()
    assert not rd.root.exists()


def test_create_failure_leaves_nothing_behind(tmp_path, monkeypatch):
    with pytest.raises(FileNotFoundError, match="nowhere"):
        RunDir.create(tmp_path / "work", source=tmp_path / "nowhere", wine=False)
    assert not (tmp_path / "work").exists() or not list((tmp_path / "work").iterdir())


def test_reset_restores_inputs_and_removes_outputs(installed, tmp_path):
    with RunDir.create(tmp_path / "work", wine=False) as rd:
        (rd.path / "TRIM.IN").write_text("changed")
        (rd.path / "RANGE.txt").write_text("result")
        (rd.path / "SRIM Outputs" / "VACANCY.txt").write_text("result")
        (rd.path / "newdir").mkdir()
        (rd.path / "newdir" / "x").write_text("x")
        os.remove(rd.path / "TRIM.exe")  # a program that replaces a linked file
        (rd.path / "TRIM.exe").write_text("replaced")
        rd.reset()
        assert (rd.path / "TRIM.IN").read_text() == "old input"
        assert same_inode(installed / "TRIM.exe", rd.path / "TRIM.exe")
        assert not (rd.path / "RANGE.txt").exists()
        assert not (rd.path / "SRIM Outputs" / "VACANCY.txt").exists()
        assert not (rd.path / "newdir").exists()


def test_without_hard_links_everything_is_copied(installed, tmp_path, monkeypatch):
    def no_link(src, dst, *a, **kw):
        raise OSError(18, "Invalid cross-device link")

    monkeypatch.setattr(rundir_module, "_warned_full_copy", False)
    monkeypatch.setattr(os, "link", no_link)
    with pytest.warns(RuntimeWarning, match="full copy"):
        rd = RunDir.create(tmp_path / "work", wine=False)
    with rd:
        assert not same_inode(installed / "TRIM.exe", rd.path / "TRIM.exe")
        assert (rd.path / "TRIM.exe").read_bytes() == (installed / "TRIM.exe").read_bytes()
        assert rd.disk_usage() > 1_000_000
        rd.reset()  # copies are restored even though linking is impossible
        assert not (rd.path / "RANGE.txt").exists()
    # the warning is given once only
    import warnings

    with warnings.catch_warnings():
        warnings.simplefilter("error")
        RunDir.create(tmp_path / "work", wine=False).cleanup()


def test_missing_install_raises_with_the_path(tmp_path):
    with pytest.raises(FileNotFoundError) as excinfo:
        RunDir.create(tmp_path / "work", source=tmp_path / "missing", wine=False)
    assert str(tmp_path / "missing") in excinfo.value.filename


# TRIM.run / SR.run


def quick_trim():
    layer = Layer.from_formula("Ni", 8.9, 1000.0)
    return TRIM(Target([layer]), Ion("Ni", energy=1.0e6), number_ions=1, random_seed=7)


def fake_srim_program(monkeypatch, seen=None):
    """check_call replacement: 'runs' TRIM/SR in the directory given as cwd."""

    def fake(cmd, **kw):
        cwd = Path(kw["cwd"])
        if seen is not None:
            seen.append((str(cwd), kw.get("env")))
        if "TRIM.exe" in cmd[-1]:
            for name in (
                "RANGE.txt",
                "IONIZ.txt",
                "VACANCY.txt",
                "NOVAC.txt",
                "E2RECOIL.txt",
                "PHONON.txt",
            ):
                shutil.copy(TEST_FILES / "1" / name, cwd / name)
            (cwd / "SRIM Outputs").mkdir(exist_ok=True)
            (cwd / "SRIM Outputs" / "LATERAL.txt").write_text("v")
        else:
            shutil.copy(TEST_FILES / "SRIM" / "SR_OUTPUT.txt", cwd / "SR_OUTPUT.txt")

    monkeypatch.setattr(subprocess, "check_call", fake)


def test_trim_run_is_isolated_by_default(installed, tmp_path, monkeypatch):
    cwds = []
    fake_srim_program(monkeypatch, cwds)
    before = snapshot(installed)
    results = quick_trim().run(work_dir=tmp_path / "work")
    assert snapshot(installed) == before
    run_path = Path(cwds[0][0])
    assert run_path.parent.parent == (tmp_path / "work").resolve()
    assert results.range.ions.sum() > 0
    assert (run_path / "run.yaml").is_file()
    assert (run_path / "TRIM.IN").read_text() != "old input"
    # a second run gets its own directory
    quick_trim().run(work_dir=tmp_path / "work")
    assert cwds[0][0] != cwds[1][0]


def test_trim_run_output_dir_collects_outputs_and_removes_run_dir(installed, tmp_path, monkeypatch):
    fake_srim_program(monkeypatch)
    out = tmp_path / "out"
    out.mkdir()
    (out / "EXYZ.txt").write_text("from an earlier run")
    results = quick_trim().run(work_dir=tmp_path / "work", output_dir=out)
    assert (out / "RANGE.txt").is_file() and (out / "run.yaml").is_file()
    assert (out / "LATERAL.txt").is_file()  # copied from SRIM Outputs
    assert not (out / "EXYZ.txt").exists()
    assert results.range.ions.sum() > 0
    assert list((tmp_path / "work").iterdir()) == []
    quick_trim().run(work_dir=tmp_path / "work", output_dir=out, keep=True)
    assert len(list((tmp_path / "work").iterdir())) == 1


def test_failed_run_keeps_its_directory_and_names_it(installed, tmp_path, monkeypatch):
    monkeypatch.setattr(subprocess, "check_call", lambda cmd, **kw: None)  # writes nothing
    with pytest.raises(SRIMRunError) as excinfo:
        quick_trim().run(work_dir=tmp_path / "work")
    (kept,) = (tmp_path / "work").iterdir()
    assert str(kept / "srim") in "\n".join(excinfo.value.__notes__)
    assert (kept / "srim" / "TRIM.exe").is_file()


def test_run_dir_can_be_reused(installed, tmp_path, monkeypatch):
    fake_srim_program(monkeypatch)
    with RunDir.create(tmp_path / "work", wine=False) as rd:
        quick_trim().run(run_dir=rd)
        assert (rd.path / "RANGE.txt").is_file()
        quick_trim().run(run_dir=rd)
        assert (rd.path / "RANGE.txt").is_file()
    assert not rd.root.exists()


def test_sr_run_is_isolated_and_supports_output_dir(installed, tmp_path, monkeypatch):
    fake_srim_program(monkeypatch)
    before = snapshot(installed)
    layer = Layer.from_formula("Si", 2.33, 1000.0)
    sr = SR(layer, Ion("He", energy=1.0e6))
    results = sr.run(work_dir=tmp_path / "work")
    assert snapshot(installed) == before
    assert results.ion
    out = tmp_path / "out"
    sr.run(work_dir=tmp_path / "work2", output_dir=out)
    assert (out / "SR_OUTPUT.txt").is_file()


def test_in_place_run_writes_to_the_given_directory(installed, monkeypatch):
    fake_srim_program(monkeypatch)
    quick_trim().run(srim_directory=str(installed))
    assert (installed / "RANGE.txt").read_text() != "stale result"
    assert install_lock_path(installed).is_file()
    assert not list(installed.glob(".pysrim2*")) or [
        p.name for p in installed.glob(".pysrim2*")
    ] == [".pysrim2-installed"]


def test_work_dir_with_srim_directory_is_an_error(installed, tmp_path):
    with pytest.raises(ValueError, match="srim_directory"):
        quick_trim().run(srim_directory=str(installed), work_dir=tmp_path)


# locks


def _lock_file(install):
    path = install_lock_path(install)
    path.parent.mkdir(parents=True, exist_ok=True)
    return path


def _hold(path, seconds, ready):
    with FileLock(path):
        ready.set()
        time.sleep(seconds)


@pytest.fixture
def mp():
    return multiprocessing.get_context("spawn")


def test_lock_contention_between_processes(tmp_path, mp, capsys):
    lock = tmp_path / "x.lock"
    ready = mp.Event()
    holder = mp.Process(target=_hold, args=(str(lock), 1.0, ready))
    holder.start()
    try:
        assert ready.wait(30)
        with pytest.raises(TimeoutError, match="another pysrim2 run"):
            FileLock(lock, timeout=0.2).acquire()
        start = time.monotonic()
        with FileLock(lock, message_after=0.2):
            assert time.monotonic() - start > 0.3  # had to wait for the holder
        assert "waiting for another run" in capsys.readouterr().err
    finally:
        holder.join(30)


def test_lock_is_released_when_the_holder_dies(tmp_path, mp):
    lock = tmp_path / "x.lock"
    ready = mp.Event()
    holder = mp.Process(target=_hold, args=(str(lock), 60, ready))
    holder.start()
    assert ready.wait(30)
    holder.kill()
    holder.join(30)
    with FileLock(lock, timeout=5):
        pass


def test_in_place_run_waits_for_the_lock(installed, mp, monkeypatch):
    fake_srim_program(monkeypatch)
    ready = mp.Event()
    holder = mp.Process(target=_hold, args=(str(_lock_file(installed)), 3, ready))
    holder.start()
    try:
        assert ready.wait(30)
        with pytest.raises(TimeoutError):
            quick_trim().run(srim_directory=str(installed), lock_timeout=0.3)
        # a default-mode run waits only while the copy is made (no torn copy)
        quick_trim().run()
    finally:
        holder.kill()
        holder.join(30)


# wine prefixes


def make_prefix(root):
    root.mkdir()
    (root / ".pysrim2-vb5").write_text("1\n")
    for name in ("system.reg", "user.reg", "userdef.reg"):
        (root / name).write_text("REGEDIT\n" * 100)
    (root / "wineserver").write_text("lock")
    (root / "dosdevices").mkdir()
    os.symlink("../drive_c", root / "dosdevices" / "c:")
    sys32 = root / "drive_c" / "windows" / "system32"
    sys32.mkdir(parents=True)
    (sys32 / "kernel32.dll").write_bytes(b"k" * 5000)
    (sys32 / "msvbvm50.dll").write_bytes(b"m" * 5000)
    (sys32 / "comcat.dll").write_bytes(b"c" * 5000)
    (root / "drive_c" / "windows" / "win.ini").write_text("[x]")
    users = root / "drive_c" / "users" / "me"
    users.mkdir(parents=True)
    (users / "NTUSER.DAT").write_bytes(b"u" * 5000)
    return root


WINE_SKIP = pytest.mark.skipif(sys.platform == "win32", reason="wine prefixes are Linux-only")


@WINE_SKIP
def test_clone_wine_prefix_links_binaries_and_copies_state(tmp_path):
    src = make_prefix(tmp_path / "wine")
    clone = clone_wine_prefix(src, tmp_path / "clone")
    for rel in ("drive_c/windows/system32/kernel32.dll", "drive_c/windows/system32/msvbvm50.dll"):
        assert same_inode(src / rel, clone / rel)
    for rel in (
        "system.reg",
        "user.reg",
        "userdef.reg",
        "drive_c/windows/win.ini",
        "drive_c/users/me/NTUSER.DAT",
        ".pysrim2-vb5",
    ):
        assert (clone / rel).is_file()
        assert not same_inode(src / rel, clone / rel), rel
    assert os.readlink(clone / "dosdevices" / "c:") == "../drive_c"
    assert not (clone / "wineserver").exists()
    assert disk_usage(clone) < 20_000
    # writing in the clone's registry leaves the original alone
    (clone / "user.reg").write_text("changed")
    assert (src / "user.reg").read_text().startswith("REGEDIT")


@WINE_SKIP
def test_clone_without_links_copies(tmp_path, monkeypatch):
    src = make_prefix(tmp_path / "wine")
    monkeypatch.setattr(rundir_module, "_warned_full_copy", False)
    monkeypatch.setattr(os, "link", lambda *a, **k: (_ for _ in ()).throw(OSError(1, "no")))
    with pytest.warns(RuntimeWarning, match="wine prefix"):
        clone = clone_wine_prefix(src, tmp_path / "clone")
    assert (clone / "drive_c/windows/system32/kernel32.dll").read_bytes() == b"k" * 5000


@WINE_SKIP
def test_run_dir_gets_a_prefix_and_stops_only_its_wineserver(installed, tmp_path, monkeypatch):
    from srim.installer import wine as wine_module

    monkeypatch.setenv("PYSRIM2_WINEPREFIX", str(make_prefix(tmp_path / "private")))
    monkeypatch.setattr(shutil, "which", lambda name: "/usr/bin/wine" if name == "wine" else None)
    monkeypatch.setattr(rundir_module.sys, "platform", "linux")
    killed = []
    monkeypatch.setattr(wine_module, "_kill_wineserver", lambda env: killed.append(env))
    rd = RunDir.create(tmp_path / "work")
    assert rd.wine_prefix == rd.root / "wine" and (rd.wine_prefix / "system.reg").is_file()
    rd.cleanup()
    assert [e["WINEPREFIX"] for e in killed] == [str(rd.root / "wine")]
    assert not rd.root.exists()


@WINE_SKIP
def test_launch_env_and_failure_cleanup_use_the_run_prefix(tmp_path, monkeypatch):
    from srim import srim as srim_module

    monkeypatch.setenv("PYSRIM2_WINEPREFIX", str(tmp_path / "shared"))
    monkeypatch.setattr(shutil, "which", lambda name: "/usr/bin/wine" if name == "wine" else None)
    assert srim_module._launch_env(tmp_path / "mine")["WINEPREFIX"] == str(tmp_path / "mine")
    assert srim_module._launch_env()["WINEPREFIX"] == str(tmp_path / "shared")

    killed = []
    monkeypatch.setattr(srim_module, "_kill_wineserver", lambda env: killed.append(env))

    def fail(cmd, **kw):
        raise subprocess.CalledProcessError(1, cmd)

    monkeypatch.setattr(subprocess, "check_call", fail)
    monkeypatch.setattr(srim_module, "_launch_command", lambda exe: ["wine", exe])
    with pytest.raises(subprocess.CalledProcessError):
        srim_module._run_srim("TRIM.exe", tmp_path / "mine")
    assert [e["WINEPREFIX"] for e in killed] == [str(tmp_path / "mine")]
    # the shared prefix is never killed
    with pytest.raises(subprocess.CalledProcessError):
        srim_module._run_srim("TRIM.exe")
    assert len(killed) == 1


@pytest.mark.skipif(sys.platform == "win32", reason="POSIX file modes")
def test_copied_files_are_writable_even_if_the_install_is_read_only(installed, tmp_path):
    os.chmod(installed / "TRIM.IN", 0o444)
    with RunDir.create(tmp_path / "work", wine=False) as rd:
        (rd.path / "TRIM.IN").write_text("new")


# fix round 1: no chdir, xvfb displays, run-dir lifecycle, stale reads, relative paths, locks


def test_threads_run_concurrently_without_changing_the_working_directory(
    installed, tmp_path, monkeypatch
):
    barrier = threading.Barrier(2, timeout=20)
    seen = []

    def fake(cmd, **kw):
        cwd = Path(kw["cwd"])
        barrier.wait()  # both runs are inside "SRIM" at the same time
        seen.append((os.getcwd(), cwd, (cwd / "TRIM.IN").read_bytes()))
        for name in ("RANGE.txt", "IONIZ.txt", "VACANCY.txt", "NOVAC.txt", "E2RECOIL.txt"):
            shutil.copy(TEST_FILES / "1" / name, cwd / name)
        shutil.copy(TEST_FILES / "1" / "PHONON.txt", cwd / "PHONON.txt")

    monkeypatch.setattr(subprocess, "check_call", fake)
    monkeypatch.chdir(tmp_path)
    start = os.getcwd()
    layer = Layer.from_formula("Ni", 8.9, 1000.0)
    runs = [TRIM(Target([layer]), Ion("Ni", energy=e), number_ions=1) for e in (1.0e6, 2.5e6)]
    with cf.ThreadPoolExecutor(2) as pool:
        results = list(pool.map(lambda t: t.run(work_dir=tmp_path / "work"), runs))
    assert os.getcwd() == start
    assert all(cwd == start for cwd, _, _ in seen)
    assert len({d for _, d, _ in seen}) == 2
    inputs = {data for _, _, data in seen}
    assert len(inputs) == 2
    for run, result in zip(runs, results, strict=True):
        assert (Path(result.directory) / "TRIM.IN").read_bytes() in inputs
        assert str(run.ion.energy / 1000) in (Path(result.directory) / "TRIM.IN").read_text()


FAKE_XVFB = """#!{python}
import os, sys, time
args = sys.argv[1:]
n = args[args.index("-n") + 1]
d = os.environ["FAKE_X_DIR"]
if os.environ.get("FAKE_X_FAIL") and not os.path.exists(os.path.join(d, "failed-once")):
    open(os.path.join(d, "failed-once"), "w").close()
    sys.stderr.write("xvfb-run: error: Xvfb failed to start\\n")
    sys.exit(1)
try:
    fd = os.open(os.path.join(d, "X" + n), os.O_CREAT | os.O_EXCL)
except FileExistsError:
    open(os.path.join(d, "collision"), "a").write(n + "\\n")
    sys.stderr.write("xvfb-run: error: Xvfb failed to start\\n")
    sys.exit(1)
time.sleep(0.3)
os.remove(os.path.join(d, "X" + n))
open(os.path.join(d, "ok"), "a").write(n + "\\n")
if os.environ.get("FAKE_X_EXIT"):
    sys.stderr.write("wine: something else broke\\n")
    sys.exit(int(os.environ["FAKE_X_EXIT"]))
"""


@pytest.fixture
def fake_xvfb(tmp_path, monkeypatch):
    import srim.srim as srim_module
    from srim import _xvfb

    script = tmp_path / "xvfb-run"
    script.write_text(FAKE_XVFB.format(python=sys.executable))
    script.chmod(0o755)
    shared = tmp_path / "x"
    shared.mkdir()
    locks = tmp_path / "locks"
    locks.mkdir()
    monkeypatch.setenv("FAKE_X_DIR", str(shared))
    monkeypatch.setattr(tempfile, "tempdir", str(locks))
    monkeypatch.setattr(_xvfb, "_sleep", lambda s: None)
    monkeypatch.setattr(srim_module, "_launch_command", lambda exe: ["xvfb-run", "-a", "wine", exe])
    real_run = subprocess.run

    def run(cmd, **kw):  # "xvfb-run" is our script
        return real_run([str(script), *cmd[1:]], **kw)

    monkeypatch.setattr(subprocess, "run", run)
    return shared


@pytest.mark.skipif(sys.platform == "win32", reason="xvfb is Linux only")
def test_32_simultaneous_xvfb_starts_get_distinct_displays(fake_xvfb, tmp_path):
    from srim.srim import _run_srim

    with cf.ThreadPoolExecutor(32) as pool:
        list(pool.map(lambda _: _run_srim("TRIM.exe", None, str(tmp_path)), range(32)))
    assert not (fake_xvfb / "collision").exists()
    displays = (fake_xvfb / "ok").read_text().split()
    assert len(displays) == 32 and all(int(d) >= 100 for d in displays)


@pytest.mark.skipif(sys.platform == "win32", reason="xvfb is Linux only")
def test_xvfb_startup_failure_is_retried_other_failures_are_not(fake_xvfb, tmp_path, monkeypatch):
    from srim.srim import SRIMProcessError, _run_srim

    monkeypatch.setenv("FAKE_X_FAIL", "1")
    _run_srim("TRIM.exe", None, str(tmp_path))  # first start fails, the retry works
    assert (fake_xvfb / "failed-once").exists() and len((fake_xvfb / "ok").read_text().split()) == 1
    monkeypatch.delenv("FAKE_X_FAIL")
    monkeypatch.setenv("FAKE_X_EXIT", "3")
    with pytest.raises(SRIMProcessError, match="something else broke"):
        _run_srim("TRIM.exe", None, str(tmp_path))
    assert len((fake_xvfb / "ok").read_text().split()) == 2  # ran once, not three times


def test_xvfb_gives_up_after_three_startup_failures(tmp_path, monkeypatch):
    import srim.srim as srim_module
    from srim import _xvfb
    from srim.srim import SRIMProcessError

    calls = []

    def always_fails(cmd, **kw):
        calls.append(cmd)
        kw["stderr"].write(b"xvfb-run: error: Xvfb failed to start\n")
        return subprocess.CompletedProcess(cmd, 1)

    monkeypatch.setattr(tempfile, "tempdir", str(tmp_path))
    monkeypatch.setattr(_xvfb, "_sleep", lambda s: None)
    monkeypatch.setattr(
        srim_module, "_launch_command", lambda exe: ["xvfb-run", *XVFB_ARGS, "wine", exe]
    )
    monkeypatch.setattr(subprocess, "run", always_fails)
    with pytest.raises(SRIMProcessError):
        srim_module._run_srim("TRIM.exe")
    assert len(calls) == 3
    assert len({c[c.index("-n") + 1] for c in calls}) == 3 and "-a" not in calls[0]
    assert "-screen 0 1024x768x24" in calls[0]


def old(path, days):
    t = time.time() - days * 86400
    os.utime(path, (t, t))


def test_prune_deletes_old_unused_run_dirs_only(installed, tmp_path):
    from srim.rundir import RUN_LOCK_NAME, prune_runs

    base = tmp_path / "work"
    kept_new = RunDir.create(base, wine=False, keep=True)
    kept_new.release_lock()
    stale = RunDir.create(base, wine=False, keep=True)
    stale.release_lock()
    busy = RunDir.create(base, wine=False, keep=True)  # still locked: in use
    for rd in (stale, busy):
        old(rd.root / RUN_LOCK_NAME, 30)
    assert prune_runs(base, 7, dry_run=True) == [stale.root]
    assert stale.root.exists()
    assert prune_runs(base, 7) == [stale.root]
    assert not stale.root.exists() and kept_new.root.exists() and busy.root.exists()
    busy.release_lock()
    assert prune_runs(base, 7) == []  # releasing counts as use: it is new again
    old(busy.root / RUN_LOCK_NAME, 30)
    assert prune_runs(base, 7) == [busy.root]
    foreign = base / "run-not-ours"
    foreign.mkdir()
    old(foreign, 30)
    assert prune_runs(base, 0) == []  # younger than a minute: never touched
    old(kept_new.root / RUN_LOCK_NAME, 0.01)
    assert prune_runs(base, 0) == [kept_new.root] and foreign.exists()


def test_new_runs_prune_old_ones_including_failed_runs(installed, tmp_path, monkeypatch):
    from srim.rundir import RUN_LOCK_NAME

    base = tmp_path / "work"
    monkeypatch.setattr(subprocess, "check_call", lambda cmd, **kw: None)  # run fails
    with pytest.raises(SRIMRunError):
        quick_trim().run(work_dir=base)
    (failed,) = base.iterdir()
    old(failed / RUN_LOCK_NAME, 8)
    fake_srim_program(monkeypatch)
    monkeypatch.setenv("PYSRIM2_RUN_RETENTION_DAYS", "0")  # retention off
    quick_trim().run(work_dir=base)
    assert failed.exists()
    monkeypatch.setenv("PYSRIM2_RUN_RETENTION_DAYS", "7")
    quick_trim().run(work_dir=base)
    assert not failed.exists() and len(list(base.iterdir())) == 2


def test_results_close_and_context_manager_delete_the_run_dir(installed, tmp_path, monkeypatch):
    fake_srim_program(monkeypatch)
    results = quick_trim().run(work_dir=tmp_path / "work")
    root = Path(results.directory).parent
    assert root.is_dir()
    results.close()
    assert not root.exists()
    results.close()  # idempotent
    with quick_trim().run(work_dir=tmp_path / "work") as again:
        where = Path(again.directory)
        assert where.is_dir()
    assert not where.exists()
    out = quick_trim().run(work_dir=tmp_path / "work", output_dir=tmp_path / "out")
    out.close()
    assert (tmp_path / "out" / "RANGE.txt").is_file()  # output_dir is the caller's


def test_remove_tree_handles_read_only_items(tmp_path):
    from srim.rundir import remove_tree

    top = tmp_path / "t"
    (top / "sub").mkdir(parents=True)
    (top / "sub" / "f").write_text("x")
    os.chmod(top / "sub" / "f", 0o444)
    os.chmod(top / "sub", 0o555)
    assert remove_tree(top) == [] and not top.exists()


def test_clean_runs_cli_and_doctor_report(installed, tmp_path, capsys):
    from srim import cli
    from srim.rundir import RUN_LOCK_NAME

    rd = RunDir.create(wine=False, keep=True)
    rd.release_lock()
    old(rd.root / RUN_LOCK_NAME, 10)
    assert cli.main(["clean", "runs", "--dry-run", "--older-than", "7"]) == 0
    assert rd.root.exists() and "would delete" in capsys.readouterr().out
    check = next(c for c in cli.collect_checks() if c["name"] == "run-directories")
    assert check["ok"] and "1 in" in check["detail"]
    assert cli.main(["clean", "runs", "--older-than", "20"]) == 0
    assert rd.root.exists()
    assert cli.main(["clean", "runs", "--older-than", "7"]) == 0
    assert not rd.root.exists()


@pytest.fixture
def stale_setup(installed, tmp_path, monkeypatch):
    monkeypatch.setattr(rundir_module, "_warned_stale", False)
    for name in ("RANGE.txt", "TRIM.IN"):
        (installed / name).write_text("old result")
        old(installed / name, 1)
    rundir_module.note_isolated_run()
    marker = tmp_path / "home" / rundir_module.LAST_RUN_MARKER
    t = time.time() - 100  # explicit times: no dependence on file system resolution
    os.utime(marker, (t, t))
    return installed


def test_reading_outputs_of_the_installed_dir_after_isolated_runs_warns_once(stale_setup, tmp_path):
    with pytest.warns(UserWarning, match=r"results\.directory.*output_dir"):
        TRIM.copy_output_files(str(stale_setup), str(tmp_path))
    import warnings

    with warnings.catch_warnings():
        warnings.simplefilter("error")  # one time only
        TRIM.copy_output_files(str(stale_setup), str(tmp_path))


def test_results_of_the_installed_dir_warn_too(stale_setup):
    from srim.output import Results

    for p in (TEST_FILES / "1").iterdir():
        shutil.copy(p, stale_setup / p.name)
        old(stale_setup / p.name, 1)
    with pytest.warns(UserWarning, match="0.9.0"):
        Results(stale_setup)


def test_no_stale_warning_for_other_dirs_or_fresh_in_place_outputs(stale_setup, tmp_path):
    import warnings

    other = tmp_path / "other"
    other.mkdir()
    with warnings.catch_warnings():
        warnings.simplefilter("error")
        TRIM.copy_output_files(str(other), str(tmp_path))
        (stale_setup / "RANGE.txt").write_text("fresh in-place run")  # newer than the marker
        now = time.time()
        os.utime(stale_setup / "RANGE.txt", (now, now))
        TRIM.copy_output_files(str(stale_setup), str(tmp_path))


def test_relative_work_dir_is_made_absolute(installed, tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    with RunDir.create("rel", wine=False) as rd:
        assert rd.root.is_absolute() and rd.path.is_absolute()
        assert rd.root.parent == tmp_path / "rel"
    fake_srim_program(monkeypatch)
    results = quick_trim().run(work_dir="rel2")
    assert os.path.isabs(results.directory)


def _hold_exclusive(path, seconds, ready):
    with FileLock(path):
        ready.set()
        time.sleep(seconds)


def test_copying_the_install_waits_for_an_in_place_run_to_finish_writing(installed, tmp_path, mp):
    ready = mp.Event()
    holder = mp.Process(target=_hold_exclusive, args=(str(_lock_file(installed)), 1.5, ready))
    holder.start()
    try:
        assert ready.wait(30)
        start = time.monotonic()
        RunDir.create(tmp_path / "work", wine=False).cleanup()
        assert time.monotonic() - start > 0.8
    finally:
        holder.join(30)


@pytest.mark.skipif(
    sys.platform == "win32" or (hasattr(os, "geteuid") and os.geteuid() == 0),
    reason="needs a read-only directory that root can still write",
)
def test_read_only_install_works_for_copies_and_in_place_fails_cleanly(installed):
    os.chmod(installed, 0o555)
    try:
        with pytest.raises(PermissionError):  # SRIM's inputs cannot be written there
            quick_trim().run(srim_directory=str(installed))
        RunDir.create(wine=False).cleanup()  # a copy needs no write access to the install
        assert not list(installed.glob(".pysrim2.lock"))
    finally:
        os.chmod(installed, 0o755)


def test_remove_tree_never_changes_the_mode_of_hard_linked_files(tmp_path):
    from srim.rundir import remove_tree

    source = tmp_path / "install.exe"
    source.write_text("x")
    os.chmod(source, 0o444)
    top = tmp_path / "t"
    (top / "sub").mkdir(parents=True)
    os.link(source, top / "sub" / "linked.exe")
    os.chmod(top / "sub", 0o555)  # read-only folder: the link can only go after it is writable
    if sys.platform == "win32":
        # a read-only name cannot be deleted on Windows, and clearing the attribute would
        # also change the shared file: the link is reported and kept
        with pytest.warns(RuntimeWarning, match="could not delete"):
            assert remove_tree(top) == [str(top / "sub" / "linked.exe")]
        assert top.exists()
    else:
        assert remove_tree(top) == [] and not top.exists()
    assert source.read_text() == "x" and (os.stat(source).st_mode & 0o777) == 0o444


def test_remove_tree_clears_read_only_on_windows_for_lone_files(tmp_path, monkeypatch):
    from types import SimpleNamespace

    # Windows branch on any OS: unlink refuses a read-only file until its attribute is
    # cleared (simulated here), and a lone file must be cleared and removed
    top = tmp_path / "t"
    top.mkdir()
    lone = top / "lone.txt"
    lone.write_text("x")
    os.chmod(lone, 0o444)
    real_unlink = os.unlink

    def unlink(path, *, dir_fd=None):  # rmtree may pass a name relative to dir_fd
        if not os.stat(path, dir_fd=dir_fd).st_mode & stat.S_IWRITE:
            raise PermissionError(13, "read-only", path)
        return real_unlink(path, dir_fd=dir_fd)

    monkeypatch.setattr(
        rundir_module, "sys", SimpleNamespace(platform="win32", version_info=sys.version_info)
    )
    monkeypatch.setattr(os, "unlink", unlink)
    assert rundir_module.remove_tree(top) == [] and not top.exists()


def test_wine_prefix_is_never_cloned_on_windows(installed, tmp_path, monkeypatch):
    from types import SimpleNamespace

    # only rundir's own view of sys is changed; wine is "installed" and asked for
    monkeypatch.setattr(
        rundir_module, "sys", SimpleNamespace(platform="win32", version_info=sys.version_info)
    )
    monkeypatch.setattr(shutil, "which", lambda name: "C:\\wine.exe")

    def no_clone(*args, **kwargs):
        raise AssertionError("a wine prefix was cloned on Windows")

    monkeypatch.setattr(rundir_module, "clone_wine_prefix", no_clone)
    assert rundir_module._wine_available() is False
    with RunDir.create(tmp_path / "work", wine=True) as rd:
        assert rd.wine_prefix is None


def _prune_in_child(base):
    from srim.rundir import prune_runs

    return [str(p) for p in prune_runs(base, 0)]


def test_open_results_keep_their_run_dir_alive_across_processes(
    installed, tmp_path, monkeypatch, mp
):
    import gc

    fake_srim_program(monkeypatch)
    base = tmp_path / "work"
    results = quick_trim().run(work_dir=base)
    root = Path(results.directory).parent
    old(root / rundir_module.RUN_LOCK_NAME, 30)
    with cf.ProcessPoolExecutor(1, mp_context=mp) as pool:
        assert pool.submit(_prune_in_child, str(base)).result(60) == []  # open: skipped
        assert root.exists()
        results.close()  # deletes it anyway, and releases the lock
        assert not root.exists()
        again = quick_trim().run(work_dir=base)
        root2 = Path(again.directory).parent
        old(root2 / rundir_module.RUN_LOCK_NAME, 30)
        assert pool.submit(_prune_in_child, str(base)).result(60) == []
        del again
        gc.collect()  # garbage collection releases the lock
        assert pool.submit(_prune_in_child, str(base)).result(60) == [str(root2)]


@pytest.mark.skipif(sys.platform == "win32", reason="POSIX errno")
def test_in_place_run_on_a_read_only_file_system_gives_the_clear_error(installed, monkeypatch):
    import errno

    def erofs(self):
        raise OSError(errno.EROFS, "Read-only file system")

    monkeypatch.setattr(FileLock, "acquire", erofs)
    with pytest.raises(PermissionError, match="not writable"):
        quick_trim().run(srim_directory=str(installed))


def test_srim_is_started_with_an_absolute_path_inside_the_run_dir(installed, tmp_path, monkeypatch):
    commands = []
    fake_srim_program(monkeypatch)
    inner = subprocess.check_call

    def spy(cmd, **kw):
        commands.append((cmd, kw["cwd"]))
        return inner(cmd, **kw)

    monkeypatch.setattr(subprocess, "check_call", spy)
    results = quick_trim().run(work_dir=tmp_path / "work")
    ((cmd, cwd),) = commands
    assert os.path.isabs(cmd[-1]) and cmd[-1] == os.path.join(cwd, "TRIM.exe")
    assert Path(cwd) == Path(results.directory)
    sr = SR(Layer.from_formula("Si", 2.33, 1000.0), Ion("He", energy=1.0e6))
    sr.run(work_dir=tmp_path / "work")
    cmd, cwd = commands[-1]
    assert os.path.isabs(cmd[-1]) and cmd[-1].endswith("SRModule.exe") and cwd in cmd[-1]
