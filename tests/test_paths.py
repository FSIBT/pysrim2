import importlib
import inspect
import sys
from pathlib import Path

import pytest

import srim.config
from srim import paths
from srim.srim import SR, TRIM

ENV_VARS = (
    "PYSRIM2_HOME",
    "PYSRIM2_SRIM_DIR",
    "PYSRIM2_WINEPREFIX",
    "WINEPREFIX",
    "XDG_DATA_HOME",
    "LOCALAPPDATA",
)


@pytest.fixture
def home(tmp_path, monkeypatch):
    """Isolate every path lookup from the real home, env and /tmp/srim."""
    fake_home = tmp_path / "home"
    fake_home.mkdir()
    monkeypatch.setattr(Path, "home", lambda: fake_home)
    monkeypatch.setattr(paths, "LEGACY_SRIM_DIR", tmp_path / "legacy-srim")
    for var in ENV_VARS:
        monkeypatch.delenv(var, raising=False)
    monkeypatch.setattr(sys, "platform", "linux")
    return fake_home


def make_srim(directory: Path) -> Path:
    directory.mkdir(parents=True, exist_ok=True)
    (directory / "TRIM.exe").touch()
    return directory


# data_dir


def test_data_dir_honours_pysrim2_home(home, monkeypatch, tmp_path):
    monkeypatch.setenv("PYSRIM2_HOME", str(tmp_path / "custom"))
    assert paths.data_dir() == tmp_path / "custom"


def test_data_dir_windows(home, monkeypatch, tmp_path):
    monkeypatch.setattr(sys, "platform", "win32")
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "Local"))
    assert paths.data_dir() == tmp_path / "Local" / "pysrim2"


def test_data_dir_windows_without_localappdata(home, monkeypatch):
    monkeypatch.setattr(sys, "platform", "win32")
    assert paths.data_dir() == home / "AppData" / "Local" / "pysrim2"


def test_data_dir_macos(home, monkeypatch):
    monkeypatch.setattr(sys, "platform", "darwin")
    assert paths.data_dir() == home / "Library" / "Application Support" / "pysrim2"


def test_data_dir_linux_default(home):
    assert paths.data_dir() == home / ".local" / "share" / "pysrim2"


def test_data_dir_linux_xdg(home, monkeypatch, tmp_path):
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "xdg"))
    assert paths.data_dir() == tmp_path / "xdg" / "pysrim2"


def test_data_dir_empty_xdg_falls_back(home, monkeypatch):
    monkeypatch.setenv("XDG_DATA_HOME", "")
    assert paths.data_dir() == home / ".local" / "share" / "pysrim2"


def test_data_dir_path_with_space(home, monkeypatch, tmp_path):
    target = tmp_path / "my data"
    monkeypatch.setenv("PYSRIM2_HOME", str(target))
    assert paths.data_dir() == target


# srim_dir


def test_srim_dir_env_override(home, monkeypatch, tmp_path):
    make_srim(paths.data_dir() / "srim")
    monkeypatch.setenv("PYSRIM2_SRIM_DIR", str(tmp_path / "my srim"))
    assert paths.srim_dir() == tmp_path / "my srim"


def test_srim_dir_prefers_data_dir_with_install(home, monkeypatch, tmp_path):
    monkeypatch.setenv("PYSRIM2_HOME", str(tmp_path / "data"))
    make_srim(tmp_path / "data" / "srim")
    make_srim(paths.LEGACY_SRIM_DIR)
    assert paths.srim_dir() == tmp_path / "data" / "srim"


def test_srim_dir_uses_legacy_when_data_dir_empty(home, monkeypatch, tmp_path):
    monkeypatch.setenv("PYSRIM2_HOME", str(tmp_path / "data"))
    make_srim(paths.LEGACY_SRIM_DIR)
    assert paths.srim_dir() == paths.LEGACY_SRIM_DIR


def test_srim_dir_default_when_nothing_installed(home, monkeypatch, tmp_path):
    monkeypatch.setenv("PYSRIM2_HOME", str(tmp_path / "data"))
    assert paths.srim_dir() == tmp_path / "data" / "srim"


def test_srim_dir_ignores_directory_without_trim_exe(home, monkeypatch, tmp_path):
    monkeypatch.setenv("PYSRIM2_HOME", str(tmp_path / "data"))
    paths.LEGACY_SRIM_DIR.mkdir()
    assert paths.srim_dir() == tmp_path / "data" / "srim"


def test_legacy_srim_dir_is_tmp_srim():
    assert paths.LEGACY_SRIM_DIR == Path("/tmp/srim")


def test_srim_dir_with_space_in_data_dir(home, monkeypatch, tmp_path):
    target = tmp_path / "data dir"
    monkeypatch.setenv("PYSRIM2_HOME", str(target))
    make_srim(target / "srim")
    assert paths.srim_dir() == target / "srim"


# wine_prefix


def test_wine_prefix_default(home, tmp_path, monkeypatch):
    monkeypatch.setenv("PYSRIM2_HOME", str(tmp_path / "data"))
    assert paths.wine_prefix() == tmp_path / "data" / "wine"


def test_wine_prefix_env_override(home, monkeypatch, tmp_path):
    monkeypatch.setenv("PYSRIM2_WINEPREFIX", str(tmp_path / "prefix with space"))
    assert paths.wine_prefix() == tmp_path / "prefix with space"


def test_wine_prefix_ignores_user_wineprefix(home, monkeypatch, tmp_path):
    monkeypatch.setenv("PYSRIM2_HOME", str(tmp_path / "data"))
    monkeypatch.setenv("WINEPREFIX", str(tmp_path / "users-own"))
    assert paths.wine_prefix() == tmp_path / "data" / "wine"


def test_wine_prefix_ignores_user_wineprefix_with_legacy_srim(home, monkeypatch, tmp_path):
    monkeypatch.setenv("PYSRIM2_HOME", str(tmp_path / "data"))
    monkeypatch.setenv("WINEPREFIX", str(tmp_path / "users-own"))
    make_srim(paths.LEGACY_SRIM_DIR)
    assert paths.srim_dir() == paths.LEGACY_SRIM_DIR
    assert paths.wine_prefix() == tmp_path / "data" / "wine"


def test_wine_prefix_ignores_default_home_wine(home, monkeypatch):
    assert paths.wine_prefix() == home / ".local" / "share" / "pysrim2" / "wine"
    assert paths.wine_prefix() != home / ".wine"


# run() defaults and config compatibility


@pytest.mark.parametrize("cls", [TRIM, SR])
def test_run_defaults_to_none(cls):
    default = inspect.signature(cls.run).parameters["srim_directory"].default
    assert default is None


@pytest.fixture
def reload_config(home, monkeypatch, tmp_path):
    yield
    # Re-import with the real environment once the patched values are gone.
    monkeypatch.undo()
    importlib.reload(srim.config)


def test_default_srim_directory_is_str_of_srim_dir(home, monkeypatch, tmp_path, reload_config):
    monkeypatch.setenv("PYSRIM2_SRIM_DIR", str(tmp_path / "configured"))
    importlib.reload(srim.config)
    assert srim.config.DEFAULT_SRIM_DIRECTORY == str(tmp_path / "configured")


@pytest.mark.parametrize("cls", [TRIM, SR])
def test_run_resolves_srim_dir_at_call_time(cls, home, monkeypatch, tmp_path):
    missing = tmp_path / "missing-srim"
    monkeypatch.setattr("srim.srim.srim_dir", lambda: missing)
    # chdir fails before any instance state is used, so skip the constructor.
    with pytest.raises(FileNotFoundError) as excinfo:
        cls.run(object.__new__(cls))
    assert str(missing) in excinfo.value.filename
