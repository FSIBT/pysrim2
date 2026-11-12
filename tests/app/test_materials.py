"""The user-materials file: save, replace, delete, and a broken file reads as no materials.

Nothing here imports PySide6, so these tests run without the app extra.
"""

import json

from srim.app.materials import (
    UserMaterial,
    delete_user_material,
    load_user_materials,
    materials_path,
    save_user_material,
)
from srim.app.model import new_element
from srim.paths import data_dir


def glass(name="SiO2", density=2.2):
    return UserMaterial(name, density, 0, [new_element("Si"), new_element("O", None)])


def test_the_file_lives_in_the_app_folder_of_the_data_dir():
    assert materials_path() == data_dir() / "app" / "materials.json"


def test_no_file_means_no_materials(tmp_path):
    assert load_user_materials(tmp_path / "materials.json") == []


def test_saved_materials_come_back_in_order(tmp_path):
    path = tmp_path / "app" / "materials.json"
    save_user_material(glass("SiO2"), path)
    save_user_material(glass("Kapton", 1.42), path)
    assert load_user_materials(path) == [glass("SiO2"), glass("Kapton", 1.42)]


def test_saving_the_same_name_replaces_it_in_place(tmp_path):
    path = tmp_path / "materials.json"
    save_user_material(glass("SiO2"), path)
    save_user_material(glass("Kapton"), path)
    save_user_material(glass("SiO2", 2.65), path)
    assert load_user_materials(path) == [glass("SiO2", 2.65), glass("Kapton")]


def test_delete_removes_one_material(tmp_path):
    path = tmp_path / "materials.json"
    save_user_material(glass("SiO2"), path)
    save_user_material(glass("Kapton"), path)
    delete_user_material("SiO2", path)
    assert load_user_materials(path) == [glass("Kapton")]
    delete_user_material("missing", path)  # no error
    assert load_user_materials(path) == [glass("Kapton")]


def test_a_file_that_is_not_json_reads_as_no_materials(tmp_path):
    path = tmp_path / "materials.json"
    path.write_text("{ not json", encoding="utf-8")
    assert load_user_materials(path) == []


def test_a_file_that_is_not_a_list_reads_as_no_materials(tmp_path):
    path = tmp_path / "materials.json"
    path.write_text(json.dumps({"name": "x"}), encoding="utf-8")
    assert load_user_materials(path) == []


def test_broken_entries_are_skipped_and_the_rest_kept(tmp_path):
    path = tmp_path / "materials.json"
    good = {
        "name": "SiO2",
        "density_g_cm3": 2.2,
        "phase": 0,
        "elements": [new_element("Si"), new_element("O")],
    }
    no_density = {"name": "Broken", "phase": 0, "elements": []}
    entries = ["not a mapping", no_density, good]
    path.write_text(json.dumps(entries), encoding="utf-8")
    assert load_user_materials(path) == [glass("SiO2")]


def test_an_isotope_keeps_its_mass(tmp_path):
    path = tmp_path / "materials.json"
    si28 = UserMaterial("Si-28", 2.33, 0, [new_element("Si", 27.9769265)])
    save_user_material(si28, path)
    (loaded,) = load_user_materials(path)
    assert loaded.elements[0]["mass_amu"] == 27.9769265
    assert loaded == si28


def test_the_file_is_written_atomically_and_leaves_no_temporary_files(tmp_path):
    path = tmp_path / "app" / "materials.json"
    save_user_material(glass(), path)
    assert [p.name for p in path.parent.iterdir()] == ["materials.json"]
