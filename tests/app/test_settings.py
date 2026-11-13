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


def test_the_rates_and_the_refresh_interval_round_trip(tmp_path):
    path = tmp_path / "app" / "settings.json"
    settings = AppSettings(ions_per_s={"2": 150.5, "1": 2000.0}, refresh_seconds=30)
    save_settings(settings, path)
    assert load_settings(path) == settings


def test_a_file_without_the_new_fields_gives_their_defaults(tmp_path):
    path = tmp_path / "settings.json"
    path.write_text(json.dumps({"route": "wine", "theme": "dark"}))
    loaded = load_settings(path)
    assert loaded.ions_per_s == {}
    assert loaded.refresh_seconds == 5


def test_a_bad_rate_entry_is_dropped_and_the_others_kept(tmp_path):
    path = tmp_path / "settings.json"
    path.write_text(
        json.dumps(
            {
                "theme": "dark",
                "ions_per_s": {"2": -1, "x": 5, "3": 40.5, "4": True, "5": "fast"},
                "refresh_seconds": 0,
            }
        )
    )
    loaded = load_settings(path)
    assert loaded.ions_per_s == {"3": 40.5}
    assert loaded.refresh_seconds == 5
    assert loaded.theme == "dark"


def test_the_refresh_interval_is_kept_within_one_second_to_an_hour(tmp_path):
    path = tmp_path / "settings.json"
    for value, expected in ((1, 1), (3600, 3600), (3601, 5), (True, 5), (2.5, 5)):
        path.write_text(json.dumps({"refresh_seconds": value}))
        assert load_settings(path).refresh_seconds == expected


@pytest.mark.parametrize("rates", [[1, 2], "2", None])
def test_a_rates_section_of_the_wrong_type_gives_no_rates(tmp_path, rates):
    path = tmp_path / "settings.json"
    path.write_text(json.dumps({"ions_per_s": rates}))
    assert load_settings(path).ions_per_s == {}
