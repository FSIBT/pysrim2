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
def fake_system(monkeypatch, tmp_path):
    """Mock tools, download and subprocess; return the list of recorded commands."""
    calls = []
    monkeypatch.setenv("PYSRIM2_HOME", str(tmp_path / "home"))
    monkeypatch.setattr(wine, "WINETRICKS_SHA256", hashlib.sha256(FAKE_EXE).hexdigest())
    monkeypatch.setattr(
        shutil, "which", fake_which({"wine", "wineserver", "xvfb-run", "7z", "bash", "curl"})
    )
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
    assert cmds[0][:3] == ["7z", "e", "-y"]
    assert cmds[1] == ["xvfb-run", *wine.XVFB_ARGS, "wineboot", "-i"]
    assert cmds[2] == ["/usr/bin/wineserver", "-w"]
    assert cmds[3][:6] == ["xvfb-run", *wine.XVFB_ARGS, "/usr/bin/bash", cmds[3][5]]
    assert cmds[3][5].endswith("winetricks-" + wine.WINETRICKS_TAG)
    assert cmds[3][6:] == ["-q", "comdlg32ocx", "msflxgrd", "richtx32", "comctl32ocx", "tabctl32"]
    assert cmds[4] == ["/usr/bin/wineserver", "-w"]
    assert all(kw["timeout"] == 600 for c, kw in fake_system if c[0] != "7z")
    env = fake_system[1][1]["env"]
    assert env["WINEPREFIX"] == str(prefix)
    assert env["WINEARCH"] == "win32"
    assert env["WINEDEBUG"] == "-all"
    system32 = prefix / "drive_c" / "windows" / "system32"
    assert (system32 / "msvbvm50.dll").read_bytes() == b"vb5"
    assert (system32 / "comcat.dll").read_bytes() == b"comcat"
    assert (prefix / MARKER_NAME).exists()


def test_setup_without_xvfb_and_with_cabextract(tmp_path, fake_system, monkeypatch):
    monkeypatch.setattr(shutil, "which", fake_which({"wine", "cabextract", "winetricks", "wget"}))

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
    assert cmds[0][:2] == ["cabextract", "-q"]
    assert cmds[1] == ["wineboot", "-i"]


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


def test_setup_prefers_system_winetricks(tmp_path, fake_system, monkeypatch):
    monkeypatch.setattr(shutil, "which", fake_which({"wine", "7z", "winetricks", "curl"}))
    setup_wine_prefix(tmp_path / "wine")
    cmds = [c for c, _ in fake_system]
    assert cmds[3][0] == "/usr/bin/winetricks"
    assert not list((tmp_path / "home" / "cache").glob("winetricks-*"))


def test_download_failure_happens_before_wine_is_touched(tmp_path, fake_system, monkeypatch):
    monkeypatch.setattr(wine, "MSVBVM50_SHA256", "0" * 64)
    with pytest.raises(WineSetupError):
        setup_wine_prefix(tmp_path / "wine")
    assert fake_system == []
    assert not (tmp_path / "wine").exists()


def test_setup_timeout_becomes_setup_error(tmp_path, fake_system, monkeypatch):
    extract = subprocess.run

    def run(cmd, **kwargs):
        if cmd[0] == "7z":
            return extract(cmd, **kwargs)
        raise subprocess.TimeoutExpired(cmd, 600)

    monkeypatch.setattr(subprocess, "run", run)
    with pytest.raises(WineSetupError, match="failed"):
        setup_wine_prefix(tmp_path / "wine")


def test_setup_without_wine(tmp_path, monkeypatch):
    monkeypatch.setattr(shutil, "which", fake_which(set()))
    with pytest.raises(WineSetupError, match="wine"):
        setup_wine_prefix(tmp_path / "wine")


def test_setup_leaves_environ_and_ignores_user_wineprefix(tmp_path, fake_system, monkeypatch):
    monkeypatch.setenv("WINEPREFIX", "/home/user/.wine")
    before = dict(os.environ)
    setup_wine_prefix(tmp_path / "wine")
    assert dict(os.environ) == before
    for _, kwargs in fake_system[1:5]:
        assert kwargs["env"]["WINEPREFIX"] == str(tmp_path / "wine")


def test_wine_status(tmp_path, monkeypatch):
    monkeypatch.setenv("PYSRIM2_WINEPREFIX", str(tmp_path / "wine"))
    monkeypatch.setattr(shutil, "which", fake_which({"wine", "7zz", "curl", "aria2c"}))
    status = wine_status()
    assert status.wine == "/usr/bin/wine"
    assert status.xvfb_run is None
    assert status.extractor == "7zz"
    assert status.winetricks is None
    assert status.downloader == "curl"
    assert status.prefix_ready is False
    system32 = tmp_path / "wine" / "drive_c" / "windows" / "system32"
    system32.mkdir(parents=True)
    for dll in DLLS:
        (system32 / dll).write_bytes(b"x")
    (tmp_path / "wine" / MARKER_NAME).write_text("1")
    assert wine_status().prefix_ready is True
    assert paths.wine_prefix() == tmp_path / "wine"


ZYPPER = "sudo zypper install -y wine xvfb-run cabextract unzip curl"


@pytest.mark.parametrize(
    "content, first, count",
    [
        ('ID=debian\nVERSION_ID="13"\n', "sudo dpkg --add-architecture i386", 3),
        ("ID=ubuntu\nID_LIKE=debian\n", "sudo dpkg --add-architecture i386", 3),
        ('ID=linuxmint\nID_LIKE="ubuntu debian"\n', "sudo dpkg --add-architecture i386", 3),
        ("ID=fedora\n", "sudo dnf install -y wine xorg-x11-server-Xvfb cabextract unzip curl", 1),
        ("ID=arch\n", "sudo pacman -S --needed wine xorg-server-xvfb cabextract unzip curl", 1),
        ('ID=opensuse-tumbleweed\nID_LIKE="opensuse suse"\n', ZYPPER, 1),
        ("ID=opensuse-leap\n", ZYPPER, 1),
        ('ID=sles\nID_LIKE="suse"\n', ZYPPER, 1),
        ("ID=gentoo\n", None, 0),
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
        assert (
            commands[2]
            == "sudo apt-get install -y wine wine32:i386 xvfb xauth 7zip cabextract unzip curl"
        )


def test_distro_wine_commands_missing_file(tmp_path):
    assert distro_wine_commands(tmp_path / "nope") == []


def test_failure_message_includes_command_output(tmp_path, fake_system, monkeypatch):
    extract = subprocess.run

    def run(cmd, **kwargs):
        if cmd[0] == "7z":
            return extract(cmd, **kwargs)
        out = "\n".join(f"line {i}" for i in range(50))
        raise subprocess.CalledProcessError(1, cmd, output=out)

    monkeypatch.setattr(subprocess, "run", run)
    with pytest.raises(WineSetupError) as info:
        setup_wine_prefix(tmp_path / "wine")
    message = str(info.value)
    assert "line 49" in message
    assert "line 30" in message
    assert "line 29" not in message


def test_winetricks_download_needs_bash(tmp_path, fake_system, monkeypatch):
    monkeypatch.setattr(shutil, "which", fake_which({"wine", "7z", "curl"}))
    with pytest.raises(WineSetupError, match="bash"):
        setup_wine_prefix(tmp_path / "wine")
    assert fake_system == []


def test_wine_status_downloader_preference_and_absence(monkeypatch):
    monkeypatch.setattr(shutil, "which", fake_which({"aria2c", "wget"}))
    assert wine_status().downloader == "wget"
    monkeypatch.setattr(shutil, "which", fake_which(set()))
    assert wine_status().downloader is None


def test_setup_requires_downloader_before_touching_wine(tmp_path, fake_system, monkeypatch):
    monkeypatch.setattr(shutil, "which", fake_which({"wine", "7z", "bash"}))
    monkeypatch.setattr(wine, "distro_wine_commands", lambda: ["sudo apt-get install -y curl"])
    with pytest.raises(WineSetupError, match="wget, curl or aria2c.*sudo apt-get install -y curl"):
        setup_wine_prefix(tmp_path / "wine")
    assert fake_system == []
    assert not (tmp_path / "wine").exists()


def test_winetricks_uses_private_cache(tmp_path, fake_system):
    setup_wine_prefix(tmp_path / "wine")

    expected = str(tmp_path / "home" / "cache" / "winetricks")
    envs = [kw["env"] for c, kw in fake_system if "-q" in c]
    assert len(envs) == 1
    assert envs[0]["W_CACHE"] == expected
    other = [kw["env"] for c, kw in fake_system if c[0] != "7z" and "-q" not in c]
    assert all(e.get("W_CACHE") != expected for e in other)


def test_wineserver_found_on_path(monkeypatch):
    monkeypatch.setattr(wine.shutil, "which", lambda name: "/usr/bin/wineserver")
    assert wine._wineserver() == "/usr/bin/wineserver"


def test_wineserver_found_in_known_location(monkeypatch, tmp_path):
    server = tmp_path / "wineserver"
    server.write_text("#!/bin/sh\n")
    server.chmod(0o755)
    monkeypatch.setattr(wine.shutil, "which", lambda name: None)
    monkeypatch.setattr(wine, "WINESERVER_PATHS", ("/nonexistent/wineserver", str(server)))
    assert wine._wineserver() == str(server)


def test_wait_for_wineserver_uses_wineserver_when_found(monkeypatch):
    calls = []
    monkeypatch.setattr(wine, "_wineserver", lambda: "/usr/lib/wine/wineserver")
    monkeypatch.setattr(wine, "_run", lambda cmd, env: calls.append(cmd))
    wine._wait_for_wineserver({}, ["xvfb-run", *wine.XVFB_ARGS])
    assert calls == [["/usr/lib/wine/wineserver", "-w"]]


def test_wait_for_wineserver_falls_back_to_end_session(monkeypatch):
    calls = []
    monkeypatch.setattr(wine, "_wineserver", lambda: None)
    monkeypatch.setattr(wine, "_run", lambda cmd, env: calls.append(cmd))
    monkeypatch.setattr(wine, "_wait_until_no_wineserver", lambda: calls.append("polled"))
    wine._wait_for_wineserver({}, ["xvfb-run", *wine.XVFB_ARGS])
    assert calls == [["xvfb-run", *wine.XVFB_ARGS, "wine", "wineboot", "--end-session"], "polled"]


def test_wait_until_no_wineserver_polls_until_gone(monkeypatch):
    codes = iter([0, 0, 1])
    monkeypatch.setattr(wine.shutil, "which", lambda name: "/usr/bin/pgrep")
    monkeypatch.setattr(wine.time, "sleep", lambda s: None)
    monkeypatch.setattr(
        subprocess, "run", lambda cmd, **kw: subprocess.CompletedProcess(cmd, next(codes))
    )
    wine._wait_until_no_wineserver()
    assert next(codes, None) is None


def test_wait_until_no_wineserver_gives_up_after_timeout(monkeypatch):
    monkeypatch.setattr(wine.shutil, "which", lambda name: "/usr/bin/pgrep")
    monkeypatch.setattr(wine.time, "sleep", lambda s: None)
    monkeypatch.setattr(subprocess, "run", lambda cmd, **kw: subprocess.CompletedProcess(cmd, 0))
    wine._wait_until_no_wineserver(timeout=0)


def test_setup_reports_each_sub_step(tmp_path, fake_system):
    lines = []
    setup_wine_prefix(tmp_path / "wine", lines.append)
    assert lines == [
        "  - creating wine prefix (wineboot)…",
        "  - installing OCX controls with winetricks (downloads; can take several minutes)…",
        "  - installing VB5 runtime DLLs…",
    ]


@pytest.mark.parametrize(
    ("exc", "expected"),
    [
        (subprocess.TimeoutExpired(["x"], 600), WineSetupError),
        (KeyboardInterrupt(), KeyboardInterrupt),
    ],
)
@pytest.mark.parametrize("fail_on", ["wineboot", "-q"])
def test_failure_kills_the_private_wineserver(
    tmp_path, fake_system, monkeypatch, exc, expected, fail_on
):
    real = subprocess.run
    killed = []

    def run(cmd, **kwargs):
        if cmd[-1] == "-k":
            killed.append((cmd, kwargs))
            return subprocess.CompletedProcess(cmd, 0)
        if fail_on in cmd:
            raise exc
        return real(cmd, **kwargs)

    monkeypatch.setattr(subprocess, "run", run)
    prefix = tmp_path / "wine"
    with pytest.raises(expected):
        setup_wine_prefix(prefix)
    assert len(killed) == 1
    cmd, kwargs = killed[0]
    assert cmd == ["/usr/bin/wineserver", "-k"]
    assert kwargs["env"]["WINEPREFIX"] == str(prefix)
    assert kwargs["timeout"] <= 30
    assert not (prefix / MARKER_NAME).exists()


def test_failed_wineserver_kill_does_not_mask_the_error(tmp_path, fake_system, monkeypatch):
    real = subprocess.run

    def run(cmd, **kwargs):
        if cmd[-1] == "-k":
            raise subprocess.TimeoutExpired(cmd, 15)
        if "wineboot" in cmd:
            raise subprocess.CalledProcessError(1, cmd)
        return real(cmd, **kwargs)

    monkeypatch.setattr(subprocess, "run", run)
    with pytest.raises(WineSetupError, match="wineboot"):
        setup_wine_prefix(tmp_path / "wine")


def test_no_wineserver_kill_on_success(tmp_path, fake_system):
    setup_wine_prefix(tmp_path / "wine")
    assert not any(c[-1] == "-k" for c, _ in fake_system)
