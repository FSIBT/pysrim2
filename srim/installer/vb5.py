"""Make SRIM start on native Windows, which lacks the Visual Basic 5 runtime.

TRIM.exe and SRModule.exe need ``MSVBVM50.DLL``, ``COMCAT.DLL`` and a few OCX
controls. SRIM's own zip carries all of them in ``SRIM-Setup``: the OCX files
as they are, the two DLLs in Microsoft's SZDD compression. They are copied
next to ``TRIM.exe`` and into ``SR Module`` (Windows looks there first), and
the OCX controls are registered with ``regsvr32``, which needs admin rights.
"""

import shutil
import struct
import subprocess
from pathlib import Path

SETUP_DIR = "SRIM-Setup"
AUTOSETUP_DIR = "SRIM-AutoSetup"
DLLS = {"MSVBVM50.DLL": "MSVBVM50.dl_", "COMCAT.DLL": "ComCat.dl_"}
OCXS = ("ComDlg32.ocx", "MSFlxGrd.ocx", "RichTx32.ocx", "ComCtl32.ocx", "TabCtl32.ocx")
TARGETS = ("", "SR Module")
REGSVR_TIMEOUT_SECONDS = 120
REGISTERED_MARKER = ".pysrim2-vb5-registered"


class Vb5SetupError(RuntimeError):
    """The VB5 runtime could not be put in place for SRIM."""


def szdd_expand(data: bytes) -> bytes:
    """Decompress a file in Microsoft's SZDD format (what ``expand.exe`` reads)."""
    if data[:8] != b"SZDD\x88\xf0\x27\x33" or len(data) < 14 or data[8:9] != b"A":
        raise Vb5SetupError("not an SZDD compressed file")
    (size,) = struct.unpack("<I", data[10:14])
    window = bytearray(b" " * 4096)
    pos = 4096 - 16
    out = bytearray()
    i = 14
    while len(out) < size and i < len(data):
        control = data[i]
        i += 1
        for bit in range(8):
            if len(out) >= size or i >= len(data):
                break
            if control >> bit & 1:
                byte = data[i]
                i += 1
                out.append(byte)
                window[pos] = byte
                pos = (pos + 1) & 4095
            else:
                if i + 1 >= len(data):
                    break
                lo, hi = data[i], data[i + 1]
                i += 2
                start = lo | (hi & 0xF0) << 4
                for k in range((hi & 0x0F) + 3):
                    byte = window[(start + k) & 4095]
                    out.append(byte)
                    window[pos] = byte
                    pos = (pos + 1) & 4095
    if len(out) < size:
        raise Vb5SetupError(f"SZDD data is truncated or corrupt ({len(out)} of {size} bytes)")
    return bytes(out[:size])


def _targets(srim_directory: Path) -> list[Path]:
    return [srim_directory / t if t else srim_directory for t in TARGETS]


def vb5_ready(srim_directory: Path) -> bool:
    """True if every runtime file is in place and the controls were registered."""
    if not (Path(srim_directory) / REGISTERED_MARKER).is_file():
        return False
    return all((d / name).is_file() for d in _targets(srim_directory) for name in (*DLLS, *OCXS))


def copy_runtime_files(srim_directory: Path) -> list[Path]:
    """Copy the DLLs and OCX controls app-local; return the OCX files to register."""
    srim_directory = Path(srim_directory)
    setup = srim_directory / SETUP_DIR
    blobs: dict[str, bytes] = {}
    for name, packed in DLLS.items():
        source = setup / AUTOSETUP_DIR / packed
        if not source.is_file():
            raise Vb5SetupError(f"{source} not found; is this a complete SRIM install?")
        blobs[name] = szdd_expand(source.read_bytes())
    for name in OCXS:
        if not (setup / name).is_file():
            raise Vb5SetupError(f"{setup / name} not found; is this a complete SRIM install?")
    registered = []
    for directory in _targets(srim_directory):
        directory.mkdir(parents=True, exist_ok=True)
        for name, content in blobs.items():
            (directory / name).write_bytes(content)
        for name in OCXS:
            shutil.copyfile(setup / name, directory / name)
        registered += [directory / name for name in OCXS]
    return registered


def register_controls(files: list[Path]) -> None:
    """Register OCX controls with ``regsvr32 /s``; needs admin rights."""
    for file in files:
        try:
            subprocess.run(
                ["regsvr32", "/s", str(file)], check=True, timeout=REGSVR_TIMEOUT_SECONDS
            )
        except (OSError, subprocess.SubprocessError) as exc:
            raise Vb5SetupError(f"regsvr32 /s {file} failed: {exc}") from exc


def install_vb5(srim_directory: Path) -> None:
    """Put the VB5 runtime in place for the SRIM install at ``srim_directory``."""
    srim_directory = Path(srim_directory)
    (srim_directory / REGISTERED_MARKER).unlink(missing_ok=True)
    register_controls(copy_runtime_files(srim_directory))
    (srim_directory / REGISTERED_MARKER).write_text("1\n")
