"""Extractors: predefined values checked against the raw tables, registry, combine."""

import math
import os
import re
import shutil
from pathlib import Path
from types import SimpleNamespace

import numpy as np
import pytest

from srim.analysis import implant_summary as analysis_implant_summary
from srim.output import Range, Results
from srim.output.events import Backscat, Sputter, Transmit
from srim.scan import extract, run_batches
from srim.scan.extract import combine, get, register, resolve
from srim.spec import RunSpec

D = os.path.join("test_files")
SINGLE = os.path.join(D, "1")  # Ni 5 MeV, full set of profiles and RANGE_3D
SPUTTER = os.path.join(D, "outputs-0.8", "sputter")  # Ar 2 keV, 20 ions, SPUTTER.txt
THIN = os.path.join(D, "outputs-0.8", "thin")  # H 15 keV, 60 ions, BACKSCAT and TRANSMIT


@pytest.fixture(scope="module")
def single():
    return Results(SINGLE)


def stub(**tables):
    """A results-like object with only the given tables (for folders without IONIZ.txt)"""
    return SimpleNamespace(**{"range3d": None, "spec": None, **tables})


# -- registry -----------------------------------------------------------------------------


def test_predefined_names_are_registered():
    names = extract.available()
    for name in (
        "implant_summary",
        "vacancy_at",
        "peak_vacancy_depth",
        "sputter_yield",
        "backscattered_fraction",
        "transmitted_fraction",
        "ions_in_layer",
        "energy_to_recoils",
    ):
        assert name in names
    assert names == sorted(names)


def test_get_unknown_lists_known_names():
    with pytest.raises(KeyError) as info:
        get("no_such_extractor")
    message = str(info.value)
    assert "no_such_extractor" in message
    assert "sputter_yield" in message


def test_get_factory_returns_factory():
    assert get("vacancy_at") is extract.vacancy_at


def test_register_custom_and_duplicate(monkeypatch):
    monkeypatch.setitem(extract._REGISTRY, "test_custom_value", None)

    def custom(results, point=None):
        return {"answer": 42}

    register("test_custom_value", custom, replace=True)
    assert get("test_custom_value") is custom
    with pytest.raises(ValueError, match="already registered"):
        register("test_custom_value", custom)


def test_resolve_plain_name_and_parameters(single):
    assert resolve("sputter_yield") is extract.sputter_yield
    fn = resolve({"vacancy_at": {"depth_nm": 50}})
    keys = {"vacancies_per_A_per_ion_at_50nm", "knock_ons_per_A_per_ion_at_50nm"}
    assert fn(single, None).keys() == keys


def test_resolve_errors():
    with pytest.raises(ValueError, match="needs parameters"):
        resolve("vacancy_at")
    with pytest.raises(ValueError, match="takes no parameters"):
        resolve({"sputter_yield": {}})
    with pytest.raises(ValueError, match="mapping"):
        resolve({"vacancy_at": 50})


# -- implant_summary --------------------------------------------------------------------


def test_implant_summary_matches_analysis_and_raw_depths(single):
    row = extract.implant_summary(single)
    raw = single.range3d.df
    expected = analysis_implant_summary(raw)
    expected["n_ions_stopped"] = expected.pop("n_ions")
    assert row == pytest.approx(expected, nan_ok=True)
    assert row["n_ions_stopped"] == len(raw)
    assert "n_ions" not in row
    # hand check of the mean projected range from the raw depth column
    assert row["mean_depth_A"] == pytest.approx(raw["depth_A"].sum() / len(raw))


def test_implant_summary_needs_range3d():
    with pytest.raises(ValueError, match="ranges=1"):
        extract.implant_summary(stub())


# -- vacancy_at and peak_vacancy_depth ----------------------------------------------------


def bin_centres_and_widths(depth_a):
    """Centres and widths [A] of bins whose ``depth_A`` is the upper edge (first bin from 0)"""
    lower = np.concatenate([[0.0], depth_a[:-1]])
    return (lower + depth_a) / 2, depth_a - lower


def raw_vacancy_profile(results):
    """``(depth_A, elements, knock_ons)`` from the raw VACANCY table, per A per ion"""
    df = results.vacancy.df
    elements = [c for c in df.columns if c.startswith("vacancies_")]
    return (
        df["depth_A"].to_numpy(float),
        df[elements].to_numpy(float).sum(axis=1),
        df["knock_ons_per_A_per_ion"].to_numpy(float),
    )


def header_total_vacancies(directory):
    """'Total Target Vacancies' [vacancies/ion] written in the VACANCY.txt header"""
    text = Path(directory, "VACANCY.txt").read_bytes().decode("latin-1")
    return float(re.search(r"Total Target Vacancies\s*=\s*([\d.]+)", text).group(1))


@pytest.mark.parametrize("folder", ["1", "SiC-C"])
def test_element_vacancies_integrate_to_header_total(folder):
    # the header total is the element vacancies only; knock-ons are not vacancies
    directory = os.path.join(D, folder)
    depth_a, elements, _ = raw_vacancy_profile(Results(directory))
    # depth_A is the upper edge of each bin, and the first bin starts at the surface
    lower = np.concatenate([[0.0], depth_a[:-1]])
    integral = float(np.sum(elements * (depth_a - lower)))
    assert integral == pytest.approx(header_total_vacancies(directory), rel=0.01)


def test_vacancy_at_gives_element_vacancies_and_knock_ons_separately(single):
    depth_a, elements, knock_ons = raw_vacancy_profile(single)
    centres, _ = bin_centres_and_widths(depth_a)
    row = extract.vacancy_at(50)(single, None)  # 500 A, between two bin centres
    assert row.keys() == {
        "vacancies_per_A_per_ion_at_50nm",
        "knock_ons_per_A_per_ion_at_50nm",
    }
    assert row["vacancies_per_A_per_ion_at_50nm"] == pytest.approx(
        np.interp(500.0, centres, elements)
    )
    assert row["knock_ons_per_A_per_ion_at_50nm"] == pytest.approx(
        np.interp(500.0, centres, knock_ons)
    )


def test_vacancy_at_first_bin_is_nonzero_and_equals_first_bin(single):
    # the first bin covers [0, first depth_A]: 10 nm = 100 A lies in it
    _, elements, _ = raw_vacancy_profile(single)
    row = extract.vacancy_at(10)(single, None)
    assert row["vacancies_per_A_per_ion_at_10nm"] == pytest.approx(elements[0])
    assert elements[0] > 0


def test_vacancy_at_outside_table_is_zero(single):
    depth_a, _, _ = raw_vacancy_profile(single)
    beyond_nm = (depth_a[-1] + 1000.0) / 10
    row = extract.vacancy_at(beyond_nm)(single, None)
    assert list(row.values()) == [0.0, 0.0]


def test_vacancy_at_rejects_bad_depth():
    with pytest.raises(ValueError):
        extract.vacancy_at(-1.0)
    with pytest.raises(ValueError):
        extract.vacancy_at(math.nan)


def test_peak_vacancy_depth_uses_element_vacancies(single):
    depth_a, elements, _ = raw_vacancy_profile(single)
    centres, _ = bin_centres_and_widths(depth_a)
    assert extract.peak_vacancy_depth(single) == {
        "peak_vacancy_depth_A": centres[int(np.argmax(elements))]
    }


# -- sputter_yield, backscattered_fraction, transmitted_fraction ----------------------------


def test_sputter_yield_counts_atoms_per_ion():
    results = stub(directory=SPUTTER, sputter=Sputter(SPUTTER), range=Range(SPUTTER))
    raw = results.sputter.df  # one row per sputtered atom
    expected = len(raw) / 20  # SPUTTER fixture: 20 Ar ions
    assert extract.sputter_yield(results) == {"sputter_yield_atoms_per_ion": expected}


def test_sputter_yield_empty_file_is_zero(tmp_path):
    shutil.copy(os.path.join(SPUTTER, "RANGE.txt"), tmp_path / "RANGE.txt")
    (tmp_path / "SPUTTER.txt").write_bytes(b"")
    with pytest.warns(UserWarning, match="empty"):
        results = stub(sputter=Sputter(str(tmp_path)), range=Range(str(tmp_path)))
    assert results.sputter.df.empty
    assert extract.sputter_yield(results) == {"sputter_yield_atoms_per_ion": 0.0}


def test_sputter_yield_requires_file():
    with pytest.raises(ValueError, match="sputtered=1"):
        extract.sputter_yield(stub(sputter=None, range=Range(SPUTTER)))


def test_fractions_use_requested_ions_from_trim_in():
    # the thin folder's TRIM.IN asks for 60 ions; RANGE.txt reports 59.93
    results = stub(
        directory=THIN,
        backscat=Backscat(THIN),
        transmit=Transmit(THIN),
        range=Range(THIN),
    )
    back = len(results.backscat.df) / 60
    trans = len(results.transmit.df) / 60
    assert extract.backscattered_fraction(results) == {"backscattered_fraction": back}
    assert extract.transmitted_fraction(results) == {"transmitted_fraction": trans}


def test_fractions_prefer_run_spec_number_ions():
    results = stub(
        directory=THIN,
        spec={"number_ions": 100},
        backscat=Backscat(THIN),
        transmit=Transmit(THIN),
        range=Range(THIN),
    )
    assert extract.transmitted_fraction(results) == {
        "transmitted_fraction": len(results.transmit.df) / 100
    }


def test_fractions_fall_back_to_rounded_ions_calculated():
    # no run spec and no folder: the ion count of RANGE.txt, rounded (59.93 -> 60)
    results = stub(backscat=Backscat(THIN), range=Range(THIN))
    assert extract.backscattered_fraction(results) == {
        "backscattered_fraction": len(results.backscat.df) / 60
    }


def test_fraction_requires_file():
    with pytest.raises(ValueError, match="backscattered=1"):
        extract.backscattered_fraction(stub(backscat=None, range=Range(SPUTTER)))
    with pytest.raises(ValueError, match="transmit=1"):
        extract.transmitted_fraction(stub(transmit=None, range=Range(SPUTTER)))


# -- ions_in_layer --------------------------------------------------------------------------


def test_ions_in_layer_from_boundaries(single):
    depth = single.range3d.df["depth_A"].to_numpy(float)
    row = extract.ions_in_layer(0, boundaries_nm=[0, 1000.0])(single, None)
    expected = np.mean((depth >= 0) & (depth < 10000.0))
    assert row == {"fraction_of_stopped_ions_in_layer_0": pytest.approx(expected)}


def test_ions_in_layer_from_spec_second_layer(single):
    single.spec = {"target": [{"thickness_nm": 100.0}, {"thickness_nm": 200.0}]}
    try:
        depth = single.range3d.df["depth_A"].to_numpy(float)
        row = extract.ions_in_layer(1)(single, None)
    finally:
        single.spec = None
    # layer 1 spans 100 nm .. 300 nm
    expected = np.mean((depth >= 1000.0) & (depth < 3000.0))
    assert row == {"fraction_of_stopped_ions_in_layer_1": pytest.approx(expected)}


def test_ions_in_layer_errors(single):
    with pytest.raises(ValueError, match="boundaries_nm"):
        extract.ions_in_layer(0)(single, None)
    with pytest.raises(ValueError, match="out of range"):
        extract.ions_in_layer(3, boundaries_nm=[0, 10, 20])(single, None)
    with pytest.raises(ValueError, match="increasing"):
        extract.ions_in_layer(0, boundaries_nm=[0, 10, 5])
    with pytest.raises(ValueError, match="ranges=1"):
        extract.ions_in_layer(0, boundaries_nm=[0, 10])(stub(), None)


# -- energy_to_recoils -----------------------------------------------------------------------


def test_energy_to_recoils_integrates_over_depth(single):
    df = single.etorecoils.df
    _, width = bin_centres_and_widths(df["depth_A"].to_numpy(float))
    expected = float(np.sum(df["ions_eV_per_A_per_ion"].to_numpy(float) * width))
    row = extract.energy_to_recoils(single)
    assert row == {"energy_to_recoils_eV_per_ion": pytest.approx(expected, rel=1e-12)}
    assert row["energy_to_recoils_eV_per_ion"] > 0


# -- combine ----------------------------------------------------------------------------------


def test_combine_merges_and_runs_in_order(single):
    combined = combine(
        [
            "peak_vacancy_depth",
            {"vacancy_at": {"depth_nm": 50}},
            extract.energy_to_recoils,
        ]
    )
    row = combined(single, None)
    assert set(row) == {
        "peak_vacancy_depth_A",
        "vacancies_per_A_per_ion_at_50nm",
        "knock_ons_per_A_per_ion_at_50nm",
        "energy_to_recoils_eV_per_ion",
    }
    assert row["peak_vacancy_depth_A"] == extract.peak_vacancy_depth(single)["peak_vacancy_depth_A"]


def test_combine_duplicate_key_raises(single):
    combined = combine([extract.peak_vacancy_depth, extract.peak_vacancy_depth])
    with pytest.raises(ValueError, match="peak_vacancy_depth_A"):
        combined(single, None)


def test_predefined_values_are_plain_floats(single):
    row = combine([extract.peak_vacancy_depth, extract.energy_to_recoils])(single, None)
    assert all(type(v) is float for v in row.values())


# -- MergedResults (run_batches) ---------------------------------------------------------


class RealBatch:
    """Batch result for ``run_batches``: the real outputs of test_files/1, with ``close``."""

    def __init__(self, real):
        self._real = real

    def __getattr__(self, name):
        return getattr(self._real, name)

    def close(self):
        pass


def test_extractors_on_merged_results(single):
    spec = RunSpec.from_dict(
        {
            "ion": {"symbol": "Ni", "energy_keV": 5000.0},
            "number_ions": 10,
            "seed": 1,
            "target": [
                {
                    "name": "Si",
                    "thickness_nm": 100.0,
                    "density_g_cm3": 2.33,
                    "elements": [{"symbol": "Si", "stoich": 1.0}],
                }
            ],
        }
    )
    real = single  # Results of test_files/1 (the module fixture)
    with run_batches(
        spec, n_ions=2000, batch_ions=1000, seed=1, runner=lambda one: RealBatch(real)
    ) as merged:
        raw3d = real.range3d.df
        r3 = merged.range3d.df
        assert len(r3) == 2 * len(raw3d)

        # implant summary of both batches: the same ions twice
        assert extract.implant_summary(merged)["n_ions_stopped"] == 2 * len(raw3d)
        assert extract.implant_summary(merged)["mean_depth_A"] == pytest.approx(
            raw3d["depth_A"].mean()
        )

        # profiles: ion-weighted means of identical batches are the raw profile
        _, elements, _ = raw_vacancy_profile(real)
        centres, _ = bin_centres_and_widths(real.vacancy.df["depth_A"].to_numpy(float))
        row = extract.vacancy_at(10)(merged, None)
        assert row["vacancies_per_A_per_ion_at_10nm"] == pytest.approx(elements[0])
        assert extract.peak_vacancy_depth(merged)["peak_vacancy_depth_A"] == pytest.approx(
            centres[int(np.argmax(elements))]
        )
        _, width = bin_centres_and_widths(real.etorecoils.df["depth_A"].to_numpy(float))
        expected = float(np.sum(real.etorecoils.df["ions_eV_per_A_per_ion"] * width))
        assert extract.energy_to_recoils(merged)["energy_to_recoils_eV_per_ion"] == pytest.approx(
            expected
        )

        # layer of the merged run's spec (100 nm of Si): stopped-ion fraction in it
        depth = r3["depth_A"].to_numpy(float)
        layer = extract.ions_in_layer(0)(merged, None)
        key = "fraction_of_stopped_ions_in_layer_0"
        assert layer[key] == pytest.approx(np.mean(depth < 1000.0))

        # the merged results have no sputter output: a clear error, not a crash
        with pytest.raises(ValueError, match="sputtered=1"):
            extract.sputter_yield(merged)
