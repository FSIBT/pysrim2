import json

import pytest

from srim.app.settings import AppSettings, load_settings, save_settings, settings_path


@pytest.mark.parametrize(
    "text", ["", "{", "[]", '{"theme": 3}', '{"route": "vm", "theme": "pink"}']
)
def test_a_broken_settings_file_gives_defaults(tmp_path, text):
    path = tmp_path / "settings.json"
    path.write_text(text)
    assert load_settings(path) == AppSettings()


def test_a_missing_file_gives_defaults(tmp_path):
    assert load_settings(tmp_path / "nope.json") == AppSettings()


def test_good_fields_are_kept_and_unknown_keys_ignored(tmp_path):
    path = tmp_path / "settings.json"
    path.write_text(json.dumps({"route": "wine", "theme": "light", "future": [1]}))
    assert load_settings(path) == AppSettings(route="wine", theme="light")


def test_one_bad_field_keeps_the_other(tmp_path):
    path = tmp_path / "settings.json"
    path.write_text(json.dumps({"route": "vm", "theme": "dark"}))
    assert load_settings(path) == AppSettings(route=None, theme="dark")


def test_settings_round_trip(tmp_path):
    path = tmp_path / "app" / "settings.json"
    save_settings(AppSettings(route="container", theme="dark"), path)
    assert load_settings(path) == AppSettings(route="container", theme="dark")


def test_save_leaves_no_temp_files_behind(tmp_path):
    path = tmp_path / "app" / "settings.json"
    save_settings(AppSettings(), path)
    assert [p.name for p in path.parent.iterdir()] == ["settings.json"]


def test_settings_path_is_under_the_data_dir(monkeypatch, tmp_path):
    monkeypatch.setenv("PYSRIM2_HOME", str(tmp_path))
    assert settings_path() == tmp_path / "app" / "settings.json"
