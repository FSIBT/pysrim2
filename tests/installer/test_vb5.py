import subprocess

import pytest

from srim.installer import steps, vb5
from srim.installer.vb5 import Vb5SetupError


def szdd(payload: bytes) -> bytes:
    """Compress ``payload`` as SZDD using literals only."""
    header = b"SZDD\x88\xf0\x27\x33A\x00" + len(payload).to_bytes(4, "little")
    body = bytearray()
    for i in range(0, len(payload), 8):
        chunk = payload[i : i + 8]
        body.append((1 << len(chunk)) - 1)
        body += chunk
    return header + bytes(body)


def test_szdd_expand_literals_and_a_match():
    # control 0b00000011: two literals "ab", then a 3 byte match from window offset 4080
    window_start = 4096 - 16
    match = bytes([window_start & 0xFF, (window_start >> 4 & 0xF0) | 0])
    data = b"SZDD\x88\xf0\x27\x33A\x00" + (5).to_bytes(4, "little") + bytes([0b11]) + b"ab" + match
    assert vb5.szdd_expand(data) == b"ab" + b"aba"  # the match overlaps what it is writing


def test_szdd_expand_rejects_other_data():
    with pytest.raises(Vb5SetupError):
        vb5.szdd_expand(b"MZ\x90\x00" + b"\x00" * 20)


def test_szdd_expand_rejects_truncated_stream():
    with pytest.raises(Vb5SetupError, match="truncated"):
        vb5.szdd_expand(szdd(b"0123456789abcdef")[:-5])


def test_szdd_expand_rejects_bad_mode_byte():
    data = bytearray(szdd(b"abc"))
    data[8] = ord("B")
    with pytest.raises(Vb5SetupError):
        vb5.szdd_expand(bytes(data))


@pytest.fixture
def srim_tree(tmp_path):
    root = tmp_path / "srim"
    auto = root / "SRIM-Setup" / "SRIM-AutoSetup"
    auto.mkdir(parents=True)
    (auto / "MSVBVM50.dl_").write_bytes(szdd(b"vbruntime"))
    (auto / "ComCat.dl_").write_bytes(szdd(b"comcat"))
    for name in vb5.OCXS:
        (root / "SRIM-Setup" / name).write_bytes(b"ocx " + name.encode())
    (root / "SR Module").mkdir()
    return root


def test_copy_runtime_files_is_app_local(srim_tree):
    registered = vb5.copy_runtime_files(srim_tree)
    assert not vb5.vb5_ready(srim_tree)  # not registered yet
    (srim_tree / vb5.REGISTERED_MARKER).write_text("1")
    assert vb5.vb5_ready(srim_tree)
    assert (srim_tree / "MSVBVM50.DLL").read_bytes() == b"vbruntime"
    assert (srim_tree / "SR Module" / "COMCAT.DLL").read_bytes() == b"comcat"
    assert len(registered) == 2 * len(vb5.OCXS)


def test_copy_runtime_files_needs_a_complete_install(tmp_path):
    with pytest.raises(Vb5SetupError, match="complete SRIM install"):
        vb5.copy_runtime_files(tmp_path)


def test_install_vb5_registers_controls(srim_tree, monkeypatch):
    calls = []
    monkeypatch.setattr(subprocess, "run", lambda cmd, **kw: calls.append(cmd))
    vb5.install_vb5(srim_tree)
    assert len(calls) == 2 * len(vb5.OCXS)
    assert calls[0][:2] == ["regsvr32", "/s"]
    assert vb5.vb5_ready(srim_tree)


def test_failed_registration_is_retried(srim_tree, monkeypatch):
    monkeypatch.setattr(subprocess, "run", lambda cmd, **kw: None)
    vb5.install_vb5(srim_tree)

    def boom(cmd, **kw):
        raise subprocess.CalledProcessError(5, cmd)

    monkeypatch.setattr(subprocess, "run", boom)
    with pytest.raises(Vb5SetupError):
        vb5.install_vb5(srim_tree)
    assert not vb5.vb5_ready(srim_tree)


def test_register_failure_is_reported(monkeypatch, tmp_path):
    def boom(cmd, **kw):
        raise subprocess.CalledProcessError(5, cmd)

    monkeypatch.setattr(subprocess, "run", boom)
    with pytest.raises(Vb5SetupError, match="regsvr32"):
        vb5.register_controls([tmp_path / "x.ocx"])


def test_vb5_component_is_windows_only(monkeypatch):
    monkeypatch.setattr(steps, "current_platform", lambda: "linux")
    with pytest.raises(steps.ComponentUnavailable):
        steps._vb5_step()
    monkeypatch.setattr(steps, "current_platform", lambda: "windows")
    step = steps._vb5_step()
    assert step.needs_admin
    assert any(c.startswith("regsvr32 /s") for c in step.commands())
