import hashlib
import os
import shutil
import subprocess
from pathlib import Path

import pytest

from srim import paths
from srim.installer import wine
from srim.installer.wine import (
    DLLS,
    MARKER_NAME,
    WineSetupError,
    distro_wine_commands,
    setup_wine_prefix,
    wine_status,
)

FAKE_EXE = b"fake msvbvm50.exe"


def fake_which(available):
    return lambda name: f"/usr/bin/{name}" if name in available else None


@pytest.fixture
def fake_system(monkeypatch):
    """Mock tools, download and subprocess; return the list of recorded commands."""
    calls = []
    monkeypatch.setattr(shutil, "which", fake_which({"wine", "xvfb-run", "7z"}))
    monkeypatch.setattr(wine, "MSVBVM50_SHA256", hashlib.sha256(FAKE_EXE).hexdigest())
    monkeypatch.setattr(wine, "_download", lambda url, dest: dest.write_bytes(FAKE_EXE))

    def run(cmd, **kwargs):
        calls.append((cmd, kwargs))
        if cmd[0] in ("7z", "7zz"):
            out = next(a[2:] for a in cmd if a.startswith("-o"))
            # lower-case names exercise the case-insensitive lookup
            (Path(out) / "msvbvm50.dll").write_bytes(b"vb5")
            (Path(out) / "COMCAT.DLL").write_bytes(b"comcat")
        return subprocess.CompletedProcess(cmd, 0)

    monkeypatch.setattr(subprocess, "run", run)
    return calls


def test_setup_runs_recipe_and_copies_dlls(tmp_path, fake_system):
    prefix = tmp_path / "wine"
    setup_wine_prefix(prefix)

    cmds = [c for c, _ in fake_system]
    assert cmds[0] == ["xvfb-run", "-a", "wineboot", "-i"]
    assert cmds[1] == ["wineserver", "-w"]
    assert cmds[2][:3] == ["7z", "e", "-y"]
    env = fake_system[0][1]["env"]
    assert env["WINEPREFIX"] == str(prefix)
    assert env["WINEARCH"] == "win32"
    assert env["WINEDEBUG"] == "-all"
    system32 = prefix / "drive_c" / "windows" / "system32"
    assert (system32 / "msvbvm50.dll").read_bytes() == b"vb5"
    assert (system32 / "comcat.dll").read_bytes() == b"comcat"
    assert (prefix / MARKER_NAME).exists()


def test_setup_without_xvfb_and_with_cabextract(tmp_path, fake_system, monkeypatch):
    monkeypatch.setattr(shutil, "which", fake_which({"wine", "cabextract"}))

    def run(cmd, **kwargs):
        fake_system.append((cmd, kwargs))
        if cmd[0] == "cabextract":
            out = cmd[cmd.index("-d") + 1]
            (Path(out) / "msvbvm50.dll").write_bytes(b"a")
            (Path(out) / "comcat.dll").write_bytes(b"b")
        return subprocess.CompletedProcess(cmd, 0)

    monkeypatch.setattr(subprocess, "run", run)
    setup_wine_prefix(tmp_path / "wine")
    cmds = [c for c, _ in fake_system]
    assert cmds[0] == ["wineboot", "-i"]
    assert cmds[2][:2] == ["cabextract", "-q"]


def test_setup_is_idempotent(tmp_path, fake_system):
    prefix = tmp_path / "wine"
    setup_wine_prefix(prefix)
    n = len(fake_system)
    setup_wine_prefix(prefix)
    assert len(fake_system) == n


def test_setup_reruns_when_dll_missing(tmp_path, fake_system):
    prefix = tmp_path / "wine"
    setup_wine_prefix(prefix)
    (prefix / "drive_c" / "windows" / "system32" / "comcat.dll").unlink()
    n = len(fake_system)
    setup_wine_prefix(prefix)
    assert len(fake_system) > n


def test_setup_checksum_mismatch(tmp_path, fake_system, monkeypatch):
    monkeypatch.setattr(wine, "MSVBVM50_SHA256", "0" * 64)
    with pytest.raises(WineSetupError, match="SHA-256"):
        setup_wine_prefix(tmp_path / "wine")
    assert not (tmp_path / "wine" / MARKER_NAME).exists()


def test_setup_without_wine(tmp_path, monkeypatch):
    monkeypatch.setattr(shutil, "which", fake_which(set()))
    with pytest.raises(WineSetupError, match="wine"):
        setup_wine_prefix(tmp_path / "wine")


def test_setup_leaves_environ_and_ignores_user_wineprefix(tmp_path, fake_system, monkeypatch):
    monkeypatch.setenv("WINEPREFIX", "/home/user/.wine")
    before = dict(os.environ)
    setup_wine_prefix(tmp_path / "wine")
    assert dict(os.environ) == before
    for cmd, kwargs in fake_system[:2]:
        assert kwargs["env"]["WINEPREFIX"] == str(tmp_path / "wine")


def test_wine_status(tmp_path, monkeypatch):
    monkeypatch.setenv("PYSRIM2_WINEPREFIX", str(tmp_path / "wine"))
    monkeypatch.setattr(shutil, "which", fake_which({"wine", "7zz"}))
    status = wine_status()
    assert status.wine == "/usr/bin/wine"
    assert status.xvfb_run is None
    assert status.extractor == "7zz"
    assert status.prefix_ready is False
    system32 = tmp_path / "wine" / "drive_c" / "windows" / "system32"
    system32.mkdir(parents=True)
    for dll in DLLS:
        (system32 / dll).write_bytes(b"x")
    (tmp_path / "wine" / MARKER_NAME).write_text("1")
    assert wine_status().prefix_ready is True
    assert paths.wine_prefix() == tmp_path / "wine"


@pytest.mark.parametrize(
    "content, first, count",
    [
        ('ID=debian\nVERSION_ID="13"\n', "sudo dpkg --add-architecture i386", 3),
        ('ID=ubuntu\nID_LIKE=debian\n', "sudo dpkg --add-architecture i386", 3),
        ('ID=linuxmint\nID_LIKE="ubuntu debian"\n', "sudo dpkg --add-architecture i386", 3),
        ("ID=fedora\n", "sudo dnf install -y wine xorg-x11-server-Xvfb cabextract", 1),
        ("ID=arch\n", None, 0),
    ],
)
def test_distro_wine_commands(tmp_path, content, first, count):
    path = tmp_path / "os-release"
    path.write_text(content)
    commands = distro_wine_commands(path)
    assert len(commands) == count
    if first:
        assert commands[0] == first
    if count == 3:
        assert commands[2] == "sudo apt-get install -y wine wine32:i386 xvfb xauth cabextract"


def test_distro_wine_commands_missing_file(tmp_path):
    assert distro_wine_commands(tmp_path / "nope") == []
