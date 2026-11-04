import os
import shutil
import subprocess

import pytest

from srim.core.ion import Ion
from srim.core.layer import Layer
from srim.core.target import Target
from srim.output import SRResults
from srim.srim import SR, TRIM, SRIMRunError, TRIMSettings, _launch_command

TESTDATA_DIRECTORY = "test_files"


def test_launch_command_uses_wine_when_available(monkeypatch):
    monkeypatch.setattr("shutil.which", lambda n: "/usr/bin/wine" if n == "wine" else None)
    assert _launch_command("TRIM.exe") == ["wine", os.path.join(".", "TRIM.exe")]


def test_launch_command_native_without_wine(monkeypatch):
    monkeypatch.setattr("shutil.which", lambda n: None)
    assert _launch_command("TRIM.exe") == [os.path.join(".", "TRIM.exe")]


def test_launch_command_uses_xvfb_run_with_wine(monkeypatch):
    found = {"wine": "/usr/bin/wine", "xvfb-run": "/usr/bin/xvfb-run"}
    monkeypatch.setattr("shutil.which", lambda n: found.get(n))
    assert _launch_command("TRIM.exe") == ["xvfb-run", "-a", "wine", os.path.join(".", "TRIM.exe")]


def test_launch_command_native_with_xvfb_run_only(monkeypatch):
    monkeypatch.setattr("shutil.which", lambda n: "/usr/bin/xvfb-run" if n == "xvfb-run" else None)
    assert _launch_command("TRIM.exe") == [os.path.join(".", "TRIM.exe")]


@pytest.mark.parametrize("ranges,sputtered", [(0, 0), (0, 1), (1, 0), (1, 1)])
def test_trim_sputtered_output_independent_of_ranges(tmp_path, monkeypatch, ranges, sputtered):
    ion = Ion("Ni", 1.0e6)
    target = Target([Layer.from_formula("Ni", 8.9, 1000.0)])
    trim = TRIM(target, ion, ranges=ranges, sputtered=sputtered)

    assert trim.settings.ranges == ranges
    assert trim.settings.sputtered == sputtered

    monkeypatch.chdir(tmp_path)
    trim._write_input_files()
    lines = (tmp_path / "TRIM.IN").read_bytes().decode("utf-8").splitlines()
    header_index = next(i for i, line in enumerate(lines) if line.startswith("Diskfiles"))
    assert lines[header_index + 1].split() == [str(ranges), "0", "0", str(sputtered), "0", "0"]


def test_trim_sputtered_default():
    assert TRIMSettings().sputtered == 0


@pytest.mark.parametrize("sputtered", [-1, 2])
def test_trim_sputtered_invalid(sputtered):
    with pytest.raises(ValueError):
        TRIMSettings(sputtered=sputtered)


def test_simple_trim_init():
    ion = Ion("Ni", 1.0e6)

    layer = Layer.from_formula("Ni", 8.9, 1000.0)
    target = Target([layer])

    trim = TRIM(target, ion)


def test_trim_settings_autosave_accepts_positive_intervals():
    settings = TRIMSettings(autosave=5)

    assert settings.autosave == 5


def test_trim_settings_autosave_rejects_negative_intervals():
    with pytest.raises(ValueError):
        TRIMSettings(autosave=-1)


def test_simple_srim_init():
    # Construct a Nickel ion
    ion = Ion("Xe", energy=1.2e9)

    # Construct a layer of nick 20um thick with a displacement energy of 30 eV
    layer = Layer(
        {
            "Si": {
                "stoich": 0.5,
                "E_d": 35.0,  # Displacement Energy
                "lattice": 0.0,
                "surface": 3.0,
            },
            "C": {
                "stoich": 0.5,
                "E_d": 20.0,  # Displacement Energy
                "lattice": 0.0,
                "surface": 3.0,
            },
        },
        density=3.21,
        width=10000.0,
    )

    target = Target([layer])

    srim = SR(layer, ion, output_type=5)

    # resulting file should be equal to
    # test_files/SRIM/SR_OUTPUT.txt


def test_sr_run_reads_results_from_relative_srim_directory(tmp_path, monkeypatch):
    sr_module = tmp_path / "srim" / "SR Module"
    sr_module.mkdir(parents=True)

    layer = Layer(
        {
            "Si": {"stoich": 0.5, "E_d": 35.0, "lattice": 0.0, "surface": 3.0},
            "C": {"stoich": 0.5, "E_d": 20.0, "lattice": 0.0, "surface": 3.0},
        },
        density=3.21,
        width=10000.0,
    )
    ion = Ion("Xe", energy=1.2e9)
    sr = SR(layer, ion, output_type=5)

    commands = []
    sample = os.path.abspath(os.path.join(TESTDATA_DIRECTORY, "SRIM", "SR_OUTPUT.txt"))

    def fake_run(cmd, **kw):
        commands.append(cmd)
        shutil.copy(
            sample,
            sr_module / "SR_OUTPUT.txt",
        )

    monkeypatch.setattr(subprocess, "check_call", fake_run)
    monkeypatch.chdir(tmp_path)

    results = sr.run(srim_directory="srim")

    assert isinstance(results, SRResults)
    assert len(commands) == 1
    assert os.getcwd() == os.path.realpath(tmp_path)


def test_launch_env_uses_private_prefix_only_with_wine(tmp_path, monkeypatch):
    from srim import srim as srim_module

    monkeypatch.setenv("WINEPREFIX", "/home/user/.wine")
    monkeypatch.setenv("PYSRIM2_WINEPREFIX", str(tmp_path / "wine"))
    before = dict(os.environ)

    monkeypatch.setattr(shutil, "which", lambda name: "/usr/bin/wine" if name == "wine" else None)
    env = srim_module._launch_env()
    assert env["WINEPREFIX"] == str(tmp_path / "wine")
    assert env["WINEARCH"] == "win32"
    assert env["WINEDEBUG"] == "-all"
    assert dict(os.environ) == before

    monkeypatch.setattr(shutil, "which", lambda name: None)
    assert srim_module._launch_env() == before


def test_trim_run_reads_results_from_relative_srim_directory(tmp_path, monkeypatch):
    srim_directory = tmp_path / "srim"
    srim_directory.mkdir()

    layer = Layer(
        {"Si": {"stoich": 1.0, "E_d": 35.0, "lattice": 0.0, "surface": 3.0}},
        density=2.32,
        width=10000.0,
    )
    trim = TRIM(Target([layer]), Ion("Ni", energy=1.0e6), number_ions=1)

    seen = {}
    monkeypatch.setattr(
        subprocess,
        "check_call",
        lambda cmd, **kw: (srim_directory / "RANGE.txt").write_text("new"),
    )
    monkeypatch.setattr(TRIM, "_write_input_files", lambda self: None, raising=False)

    class FakeResults:
        def __init__(self, directory):
            seen["directory"] = directory

    monkeypatch.setattr("srim.srim.Results", FakeResults)
    monkeypatch.chdir(tmp_path)

    trim.run(srim_directory="srim")

    assert os.path.isabs(seen["directory"])
    assert os.path.realpath(seen["directory"]) == os.path.realpath(srim_directory)
    assert os.getcwd() == os.path.realpath(tmp_path)


def _quick_trim():
    layer = Layer.from_formula("Ni", 8.9, 1000.0)
    return TRIM(Target([layer]), Ion("Ni", energy=1.0e6), number_ions=1)


def test_trim_run_removes_stale_output_and_fails_if_none_written(tmp_path, monkeypatch):
    (tmp_path / "SRIM Outputs").mkdir()
    for stale in (tmp_path / "RANGE.txt", tmp_path / "SRIM Outputs" / "RANGE_3D.txt"):
        stale.write_text("old")
    seen = {}
    monkeypatch.setattr(
        subprocess,
        "check_call",
        lambda cmd, **kw: seen.update(left=sorted(p.name for p in tmp_path.rglob("*.txt"))),
    )
    monkeypatch.setattr(TRIM, "_write_input_files", lambda self: None, raising=False)

    with pytest.raises(SRIMRunError, match="RANGE.txt"):
        _quick_trim().run(srim_directory=str(tmp_path))

    assert seen["left"] == []


def test_sr_run_removes_stale_output_and_fails_if_none_written(tmp_path, monkeypatch):
    sr_module = tmp_path / "SR Module"
    sr_module.mkdir()
    (sr_module / "SR_OUTPUT.txt").write_text("old")
    monkeypatch.setattr(subprocess, "check_call", lambda cmd, **kw: None)
    layer = Layer.from_formula("Si", 2.33, 1000.0)
    sr = SR(layer, Ion("He", energy=1.0e6))

    with pytest.raises(SRIMRunError, match="SR_OUTPUT.txt"):
        sr.run(srim_directory=str(tmp_path))

    assert not (sr_module / "SR_OUTPUT.txt").exists()


@pytest.mark.parametrize(
    "name", ["/etc/passwd", "../x.txt", "sub/x.txt", "..\\x.txt", "", ".", ".."]
)
def test_sr_output_filename_must_be_plain(name):
    with pytest.raises(ValueError, match="output_filename"):
        SR(Layer.from_formula("Si", 2.33, 1000.0), Ion("He", energy=1.0e6), output_filename=name)
