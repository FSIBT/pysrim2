"""pysrim2 run / pysrim2 import with the SRIM subprocess mocked."""

import shutil
import subprocess
from pathlib import Path

import pytest
import yaml

from srim import cli
from srim.beam.exact import read_trim_dat
from srim.scan.spec import spec_hash
from srim.spec import RunSpec

TEST_FILES = Path(__file__).resolve().parent.parent / "test_files"

SPEC = {
    "version": 1,
    "spec": {
        "ion": {"symbol": "B", "mass_amu": 10.811, "energy_keV": 20.0, "angle_deg": 7.0},
        "number_ions": 200,
        "calculation": 1,
        "seed": 12345,
        "settings": {"description": "pysrim run", "ranges": 1},
        "target": [
            {
                "name": "Au",
                "thickness_nm": 100.0,
                "density_g_cm3": 19.3,
                "phase": 0,
                "elements": [{"symbol": "Au", "stoich": 1.0}],
            }
        ],
    },
}


@pytest.fixture
def srim_install(tmp_path, monkeypatch):
    directory = tmp_path / "srim"
    directory.mkdir()
    (directory / ".pysrim2-installed").write_text("deadbeef")
    monkeypatch.setenv("PYSRIM2_SRIM_DIR", str(directory))
    monkeypatch.setenv("PYSRIM2_HOME", str(tmp_path / "home"))
    monkeypatch.setattr(shutil, "which", lambda name: None)
    seen = {}

    def fake_call(cmd, **kw):
        # the run happens in a private copy of the install (the current directory)
        seen["trim_in"] = Path(kw["cwd"], "TRIM.IN").read_bytes()
        shutil.copy(TEST_FILES / "1" / "RANGE.txt", Path(kw["cwd"], "RANGE.txt"))

    monkeypatch.setattr(subprocess, "check_call", fake_call)
    monkeypatch.setattr("srim.srim.Results", lambda path: type("R", (), {})())
    return directory, seen


@pytest.fixture
def run_yaml(tmp_path):
    path = tmp_path / "in.yaml"
    path.write_text(yaml.safe_dump(SPEC, sort_keys=False))
    return path


def test_run_copies_outputs_and_run_yaml(srim_install, run_yaml, tmp_path, capsys):
    directory, seen = srim_install
    out = tmp_path / "out"
    assert cli.main(["run", str(run_yaml), "--output", str(out)]) == 0
    assert (out / "RANGE.txt").is_file() and (out / "TRIM.IN").is_file()
    written = yaml.safe_load((out / "run.yaml").read_text())
    assert written["spec"]["seed"] == 12345
    assert written["provenance"]["srim_sha256"] == "deadbeef"
    assert written["provenance"]["backend"] == "native"
    assert b"12345" in seen["trim_in"] and b" 20.0 " in seen["trim_in"]
    assert str(out) in capsys.readouterr().out


def test_run_default_output_directory(srim_install, run_yaml, tmp_path, monkeypatch):
    work = tmp_path / "work"
    work.mkdir()
    monkeypatch.chdir(work)
    assert cli.main(["run", str(run_yaml)]) == 0
    (out,) = work.glob("srim-output-*Z")
    assert (out / "run.yaml").is_file()


def test_run_set_overrides_are_applied_and_recorded(srim_install, run_yaml, tmp_path):
    out = tmp_path / "out"
    code = cli.main(
        ["run", str(run_yaml), "--set", "ion.energy_keV=30", "seed=9", "--set", "number_ions=5"]
        + ["--output", str(out)]
    )
    assert code == 0
    spec = RunSpec.from_yaml(out / "run.yaml")
    assert (spec.ion.energy_keV, spec.seed, spec.number_ions) == (30.0, 9, 5)


def test_run_without_seed_records_the_picked_one(srim_install, tmp_path):
    data = yaml.safe_load(yaml.safe_dump(SPEC))
    del data["spec"]["seed"]
    path = tmp_path / "noseed.yaml"
    path.write_text(yaml.safe_dump(data))
    out = tmp_path / "out"
    assert cli.main(["run", str(path), "-o", str(out)]) == 0
    spec = RunSpec.from_yaml(out / "run.yaml")
    assert f" {spec.seed} " in (out / "TRIM.IN").read_bytes().decode()


def test_run_invalid_spec_exits_2_naming_key(srim_install, tmp_path, capsys):
    data = yaml.safe_load(yaml.safe_dump(SPEC))
    data["spec"]["target"][0]["thickness_nm"] = 0
    path = tmp_path / "bad.yaml"
    path.write_text(yaml.safe_dump(data))
    assert cli.main(["run", str(path), "-o", str(tmp_path / "out")]) == 2
    err = capsys.readouterr().err
    assert "spec.target[0].thickness_nm must be > 0" in err
    assert not (tmp_path / "out").exists()


def test_run_bad_set_and_missing_file_exit_2(srim_install, run_yaml, tmp_path, capsys):
    assert cli.main(["run", str(run_yaml), "--set", "ion.energy_keV=abc"]) == 2
    assert "ion.energy_keV must be a number" in capsys.readouterr().err
    assert cli.main(["run", str(tmp_path / "missing.yaml")]) == 2


def test_run_failure_exits_1(srim_install, run_yaml, tmp_path, monkeypatch, capsys):
    def boom(cmd, **kw):
        raise subprocess.CalledProcessError(3, cmd)

    monkeypatch.setattr(subprocess, "check_call", boom)
    assert cli.main(["run", str(run_yaml), "-o", str(tmp_path / "out")]) == 1
    assert "run failed" in capsys.readouterr().err
    assert not (tmp_path / "out").exists()


def test_run_without_trim_output_exits_1(srim_install, run_yaml, tmp_path, monkeypatch):
    monkeypatch.setattr(subprocess, "check_call", lambda cmd, **kw: None)
    assert cli.main(["run", str(run_yaml), "-o", str(tmp_path / "out")]) == 1


def test_import_writes_run_yaml(tmp_path, capsys):
    out = tmp_path / "run.yaml"
    assert cli.main(["import", str(TEST_FILES / "3" / "TRIM.IN"), "-o", str(out)]) == 0
    document = yaml.safe_load(out.read_text())
    assert document["provenance"]["imported_from"] == "TRIM.IN"
    spec = RunSpec.from_yaml(out)
    assert spec.ion.symbol == "B" and len(spec.target) == 3


def test_import_default_name_and_no_overwrite(tmp_path, monkeypatch, capsys):
    monkeypatch.chdir(tmp_path)
    source = str(TEST_FILES / "1" / "TRIM.IN")
    assert cli.main(["import", source]) == 0
    assert (tmp_path / "run.yaml").is_file()
    assert cli.main(["import", source]) == 1
    assert "exists" in capsys.readouterr().err
    assert cli.main(["import", source, "-o", "run.yaml"]) == 0


def test_import_bad_file_exits_2(tmp_path, capsys):
    bad = tmp_path / "TRIM.IN"
    bad.write_text("nonsense\n")
    assert cli.main(["import", str(bad), "-o", str(tmp_path / "r.yaml")]) == 2
    assert "not a TRIM.IN" in capsys.readouterr().err
    assert cli.main(["import", str(tmp_path / "nope"), "-o", str(tmp_path / "r.yaml")]) == 2


def test_run_set_before_the_spec_path(srim_install, run_yaml, tmp_path):
    # --set takes several values (nargs="+"), so the path after it is read as one of them
    out = tmp_path / "out"
    code = cli.main(["run", "--set", "ion.energy_keV=30", "seed=9", str(run_yaml), "-o", str(out)])
    assert code == 0
    spec = RunSpec.from_yaml(out / "run.yaml")
    assert (spec.ion.energy_keV, spec.seed) == (30.0, 9)


def test_run_without_a_spec_path_exits_2(srim_install, capsys):
    assert cli.main(["run", "--set", "ion.energy_keV=30"]) == 2
    assert "no run.yaml" in capsys.readouterr().err


def test_run_set_with_a_path_that_holds_an_equals_sign(srim_install, run_yaml, tmp_path):
    folder = tmp_path / "runs" / "x=1"
    folder.mkdir(parents=True)
    target = folder / "run.yaml"
    target.write_text(run_yaml.read_text(), encoding="utf-8")
    first = tmp_path / "first"
    assert cli.main(["run", "--set", "ion.energy_keV=30", str(target), "-o", str(first)]) == 0
    assert RunSpec.from_yaml(first / "run.yaml").ion.energy_keV == 30.0
    last = tmp_path / "last"
    assert cli.main(["run", "--set", "ion.energy_keV=31", str(target), "-o", str(last)]) == 0
    assert RunSpec.from_yaml(last / "run.yaml").ion.energy_keV == 31.0


@pytest.mark.parametrize(
    ("value", "overrides", "expected"),
    [
        (None, ["ion.energy_keV=30", "run.yaml"], ("run.yaml", ["ion.energy_keV=30"])),
        (None, ["a=1", "runs/x=1/run.yaml"], ("runs/x=1/run.yaml", ["a=1"])),
        ("given.yaml", ["a=1"], ("given.yaml", ["a=1"])),
        (None, ["a=1"], (None, ["a=1"])),
    ],
)
def test_spec_path_rules(value, overrides, expected):
    assert cli._spec_path(value, overrides) == expected


def test_run_without_a_path_says_to_put_it_before_set(srim_install, capsys):
    assert cli.main(["run", "--set", "ion.energy_keV=30"]) == 2
    assert "put the path before --set" in capsys.readouterr().err


# --- distributed specs (beams) ----------------------------------------------------

BEAM_RUNNER_MODULE = "fake_beam_for_run_cli"
BEAM_RUNNER_REF = f"{BEAM_RUNNER_MODULE}:run"
BEAM_RUNNER_SOURCE = f'''"""Stands in for SRIM in the ``pysrim2 run`` tests of beams."""

from pathlib import Path

from srim.output import Results

FIXTURE = Path({str(TEST_FILES / "1")!r})


def run(spec):
    return Results(str(FIXTURE))
'''


def beam_yaml(tmp_path, *, beam=None, number_ions=200, name="beam.yaml"):
    """A run file whose energy is a uniform distribution (10 to 20 keV)."""
    data = yaml.safe_load(yaml.safe_dump(SPEC))
    data["spec"]["ion"]["energy_keV"] = {"uniform": {"low": 10.0, "high": 20.0}}
    data["spec"]["number_ions"] = number_ions
    if beam is not None:
        data["spec"]["beam"] = beam
    path = tmp_path / name
    path.write_text(yaml.safe_dump(data, sort_keys=False))
    return path


@pytest.fixture
def beam_runner(tmp_path, monkeypatch):
    (tmp_path / f"{BEAM_RUNNER_MODULE}.py").write_text(BEAM_RUNNER_SOURCE, encoding="utf-8")
    monkeypatch.syspath_prepend(str(tmp_path))
    monkeypatch.setenv(cli.SCAN_RUNNER_VAR, BEAM_RUNNER_REF)
    monkeypatch.delenv("PYSRIM2_CACHE", raising=False)
    return tmp_path


def test_beam_exact_small_run_writes_trim_dat(srim_install, tmp_path, capsys):
    path = beam_yaml(tmp_path, number_ions=200)
    out = tmp_path / "out"
    assert cli.main(["run", str(path), "-o", str(out)]) == 0
    assert len(read_trim_dat(out / "TRIM.DAT")) == 200
    assert f"outputs and run.yaml: {out}" in capsys.readouterr().out


def test_beam_binned_run_prints_the_point_folder(beam_runner, tmp_path, capsys):
    path = beam_yaml(tmp_path, beam={"mode": "binned", "bins": {"energy_keV": 2}}, number_ions=10)
    out = tmp_path / "out"
    assert cli.main(["run", str(path), "-o", str(out)]) == 0
    point = out / "points" / spec_hash(RunSpec.from_yaml(path))[:16]
    assert (point / "units.csv").is_file() and (point / "beam_bins.csv").is_file()
    captured = capsys.readouterr()
    assert f"outputs and run.yaml: {point}" in captured.out
    assert f"using test runner {BEAM_RUNNER_REF}" in captured.err


def test_beam_exact_large_run_is_split_into_units(beam_runner, tmp_path, capsys):
    path = beam_yaml(tmp_path, number_ions=100_000)
    out = tmp_path / "out"
    assert cli.main(["run", str(path), "-o", str(out)]) == 0
    point = out / "points" / spec_hash(RunSpec.from_yaml(path))[:16]
    assert (point / "units.csv").is_file()
    assert f"outputs and run.yaml: {point}" in capsys.readouterr().out


def test_beam_invalid_spec_exits_2(tmp_path, capsys):
    path = beam_yaml(tmp_path)
    data = yaml.safe_load(path.read_text())
    data["spec"]["calculation"] = 3  # a distributed exact beam needs calculation 1 or 2
    path.write_text(yaml.safe_dump(data, sort_keys=False))
    assert cli.main(["run", str(path), "-o", str(tmp_path / "out")]) == 2
    assert "calculation" in capsys.readouterr().err


def test_beam_run_failure_exits_1(beam_runner, tmp_path, monkeypatch, capsys):
    def boom(*args, **kwargs):
        raise RuntimeError("boom")

    monkeypatch.setattr("srim.beam.run_beam", boom)
    path = beam_yaml(tmp_path, beam={"mode": "binned", "bins": {"energy_keV": 2}}, number_ions=10)
    assert cli.main(["run", str(path), "-o", str(tmp_path / "out")]) == 1
    assert "run failed: boom" in capsys.readouterr().err
