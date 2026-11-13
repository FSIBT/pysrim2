"""The plot catalogue for one run's results: what each plot needs, the data behind it, and what
it draws (on an Agg figure). Runs without matplotlib for the data tests; the drawing tests skip.
"""

import shutil
from pathlib import Path
from types import SimpleNamespace

import numpy as np
import pytest

from srim.app import plots
from srim.app.plots import CATALOGUE, PLOT_ROW_CAP, missing_reason, plot_data
from srim.beam.exact import sample_ions, write_trim_dat
from srim.output import Results
from srim.output.range3d import Range3D
from srim.scan.batches import BatchInfo, MergedResults
from srim.spec import RunSpec

ROOT = Path(__file__).resolve().parents[2]
FIXTURES = ROOT / "test_files"
OUTPUTS = FIXTURES / "outputs-0.8"

BY_KEY = {plot.key: plot for plot in CATALOGUE}

RANGE3D_TEXT = "needs RANGE_3D.txt; enable 'Ranges (RANGE_3D.txt)' in Run settings"
COLLISION_TEXT = (
    "needs COLLISON.txt; choose 'Ions (COLLISON.txt)' or 'Ions and recoils (COLLISON.txt)' "
    "for 'Collisions' in Run settings"
)
BEAM_ENERGY_TEXT = "needs an exact-mode run with an energy distribution"
BEAM_ANGLE_TEXT = "needs an exact-mode run with an angle distribution"


@pytest.fixture
def ax():
    """A fresh Agg figure's axes (skips when matplotlib is not installed)."""
    matplotlib = pytest.importorskip("matplotlib")
    matplotlib.use("Agg")
    from matplotlib.figure import Figure

    return Figure().add_subplot()


def beam_spec(*, energy=None, angle=7.0, number_ions=200, mode="exact"):
    """Boron in silicon, 10-20 keV by default, with a beam section of ``mode``."""
    return RunSpec.from_dict(
        {
            "ion": {
                "symbol": "B",
                "energy_keV": energy
                if energy is not None
                else {"uniform": {"low": 10.0, "high": 20.0}},
                "angle_deg": angle,
            },
            "number_ions": number_ions,
            "calculation": 1,
            "seed": 1,
            "settings": {"ranges": 1},
            "beam": {"mode": mode},
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


def beam_results(tmp_path, spec):
    """A real Results of ``spec``: test_files/1 with the TRIM.DAT and run.yaml of an exact run."""
    folder = tmp_path / "beam"
    shutil.copytree(FIXTURES / "1", folder)
    write_trim_dat(
        sample_ions(spec),
        folder / "TRIM.DAT",
        atomic_number=5,
        description="beam test",
        seed=spec.seed,
    )
    spec.to_yaml(folder / "run.yaml")
    return Results(str(folder))


def test_the_catalogue_has_the_planned_plots_in_order():
    assert [(p.group, p.key, p.title) for p in CATALOGUE] == [
        ("Depth profiles", "ionization", "Ionization (ions and recoils)"),
        ("Depth profiles", "vacancies", "Vacancies (summed over elements)"),
        ("Depth profiles", "phonons", "Phonons"),
        ("Depth profiles", "recoil_energy", "Energy to recoils"),
        ("Depth profiles", "range_profile", "Ion range profile"),
        ("Ion distributions", "stopping_map", "Where the ions stop (depth × lateral)"),
        ("Ion distributions", "backscattered", "Energies of backscattered ions"),
        ("Ion distributions", "transmitted", "Energies of transmitted ions"),
        ("Ion distributions", "sputtered", "Energies of sputtered atoms"),
        ("Damage", "collision_map", "Collision cascades (depth × lateral)"),
        ("Damage", "damage_energy", "Damage energy"),
        ("Beam", "beam_energy", "Sampled energies over the distribution"),
        ("Beam", "beam_angle", "Sampled angles over the distribution"),
    ]


def test_the_row_cap_is_200_000():
    assert PLOT_ROW_CAP == 200_000


def test_depth_profiles_are_drawable_on_full_cascades_with_depth_in_nm():
    results = Results(str(FIXTURES / "2"))
    for key in ("ionization", "vacancies", "phonons", "recoil_energy", "range_profile"):
        assert missing_reason(BY_KEY[key], results) is None, key
        df = plot_data(BY_KEY[key], results)
        assert df.columns[0] == "depth_nm"
        np.testing.assert_allclose(df["depth_nm"], df["depth_A"] / 10.0)


def test_vacancies_are_summed_over_the_elements():
    results = Results(str(FIXTURES / "2"))
    df = plot_data(BY_KEY["vacancies"], results)
    per_element = [c for c in results.vacancy.df.columns if c.startswith("vacancies_")]
    expected = results.vacancy.df[per_element].sum(axis=1).to_numpy()
    np.testing.assert_allclose(df["vacancies_total_per_A_per_ion"], expected)


def test_stopping_map_needs_range3d_when_the_run_wrote_none():
    results = Results(str(FIXTURES / "2"))
    assert results.range3d is None
    assert missing_reason(BY_KEY["stopping_map"], results) == RANGE3D_TEXT


def test_per_ion_plots_say_which_run_setting_writes_them():
    results = Results(str(FIXTURES / "2"))  # full cascades, no output files of the per-ion kind
    assert missing_reason(BY_KEY["collision_map"], results) == COLLISION_TEXT
    expected = {
        "backscattered": (
            "needs BACKSCAT.txt; enable 'Backscattered ions (BACKSCAT.txt)' in Run settings"
        ),
        "transmitted": (
            "needs TRANSMIT.txt; enable 'Transmitted ions (TRANSMIT.txt)' in Run settings"
        ),
        "sputtered": "needs SPUTTER.txt; enable 'Sputtered atoms (SPUTTER.txt)' in Run settings",
    }
    folder = SimpleNamespace(directory=str(FIXTURES / "2"))
    for key, text in expected.items():
        assert missing_reason(BY_KEY[key], folder) == text


def test_stopping_map_data_is_in_nm_with_lateral_distance():
    results = Results(str(FIXTURES / "1"))
    assert missing_reason(BY_KEY["stopping_map"], results) is None
    df = plot_data(BY_KEY["stopping_map"], results)
    assert len(df) == results.range3d.num_ions
    np.testing.assert_allclose(df["depth_nm"], results.range3d.depth / 10.0)
    np.testing.assert_allclose(df["lateral_nm"], np.hypot(df["y_A"], df["z_A"]) / 10.0)
    assert "capped" not in df.attrs


def test_range3d_stream_is_capped_and_marked(monkeypatch):
    monkeypatch.setattr(plots, "PLOT_ROW_CAP", 100)
    df = plot_data(BY_KEY["stopping_map"], Results(str(FIXTURES / "1")))
    assert len(df) == 100
    assert df.attrs["capped"] is True


def test_a_stream_of_exactly_the_cap_is_not_capped(monkeypatch):
    count = Results(str(FIXTURES / "1")).range3d.num_ions  # SRIM leaves out a few ions
    monkeypatch.setattr(plots, "PLOT_ROW_CAP", count)
    df = plot_data(BY_KEY["stopping_map"], Results(str(FIXTURES / "1")))
    assert len(df) == count
    assert "capped" not in df.attrs


def test_the_per_ion_plots_do_not_parse_their_whole_file(monkeypatch):
    def refuse(self, *args, **kwargs):
        raise AssertionError("the whole file must not be parsed for a plot")

    count = Results(str(FIXTURES / "1")).range3d.num_ions
    monkeypatch.setattr(Range3D, "__init__", refuse)
    df = plot_data(BY_KEY["stopping_map"], Results(str(FIXTURES / "1")))
    assert len(df) == count


def test_collision_map_is_capped_at_the_row_cap(monkeypatch):
    monkeypatch.setattr(plots, "PLOT_ROW_CAP", 100)
    results = Results(str(FIXTURES / "SiC-C"))
    df = plot_data(BY_KEY["collision_map"], results)
    assert len(df) <= 100
    assert df.attrs["capped"] is True
    assert set(df["kind"]) <= {"ion", "recoil"}
    assert {"depth_nm", "lateral_nm"} <= set(df.columns)


def test_collision_map_without_the_cap_is_at_most_the_cap():
    df = plot_data(BY_KEY["collision_map"], Results(str(FIXTURES / "SiC-C")))
    assert 0 < len(df) <= PLOT_ROW_CAP


@pytest.mark.parametrize(
    ("key", "folder"),
    [("backscattered", "thin"), ("transmitted", "thin"), ("sputtered", "sputter")],
)
def test_event_plots_read_their_fixture(key, folder):
    results = SimpleNamespace(directory=str(OUTPUTS / folder))
    assert missing_reason(BY_KEY[key], results) is None
    df = plot_data(BY_KEY[key], results)
    assert len(df) > 0
    assert {"energy_eV", "atomic_number", "ion"} <= set(df.columns)


def test_every_drawable_plot_draws_on_an_agg_figure(ax):
    for results in (Results(str(FIXTURES / "1")), Results(str(FIXTURES / "SiC-C"))):
        for plot in CATALOGUE:
            if missing_reason(plot, results) is not None:
                continue
            fresh = ax.figure.add_subplot()
            plot.draw(plot_data(plot, results), fresh)
            assert fresh.get_title()


def test_event_plots_draw_on_their_fixtures(ax):
    for key, folder in (
        ("backscattered", "thin"),
        ("transmitted", "thin"),
        ("sputtered", "sputter"),
    ):
        fresh = ax.figure.add_subplot()
        plot = BY_KEY[key]
        plot.draw(plot_data(plot, SimpleNamespace(directory=str(OUTPUTS / folder))), fresh)
        assert fresh.get_title() == plot.title


def test_a_capped_frame_says_so_in_the_title(ax, monkeypatch):
    monkeypatch.setattr(plots, "PLOT_ROW_CAP", 100)
    df = plot_data(BY_KEY["collision_map"], Results(str(FIXTURES / "SiC-C")))
    BY_KEY["collision_map"].draw(df, ax)
    assert ax.get_title() == "Collision cascades (depth × lateral) (first 100 rows)"


def test_a_plot_without_its_output_refuses_its_data():
    with pytest.raises(ValueError, match="RANGE_3D.txt"):
        plot_data(BY_KEY["stopping_map"], Results(str(FIXTURES / "2")))


def test_merged_results_are_streamed_over_their_batches(monkeypatch):
    monkeypatch.setattr(plots, "PLOT_ROW_CAP", 1500)
    folder = str(FIXTURES / "1")
    merged = MergedResults(
        [Results(folder), Results(folder)],
        [
            BatchInfo(0, 1, 1000, directory=folder),
            BatchInfo(1, 2, 1000, directory=folder),
        ],
        spec=None,
    )
    assert missing_reason(BY_KEY["stopping_map"], merged) is None
    df = plot_data(BY_KEY["stopping_map"], merged)
    assert len(df) == 1500
    assert df.attrs["capped"] is True


def test_beam_energy_is_the_sampled_beam_of_an_exact_run(tmp_path):
    spec = beam_spec()
    results = beam_results(tmp_path, spec)
    assert missing_reason(BY_KEY["beam_energy"], results) is None
    df = plot_data(BY_KEY["beam_energy"], results)
    reference = sample_ions(spec)
    assert len(df) == 200
    np.testing.assert_allclose(df["energy_keV"], reference["energy_keV"], rtol=1e-6)
    assert df.attrs["density"] is not None


def test_beam_angle_is_missing_when_the_angle_is_fixed(tmp_path):
    results = beam_results(tmp_path, beam_spec())
    assert missing_reason(BY_KEY["beam_angle"], results) == BEAM_ANGLE_TEXT


def test_beam_energy_is_missing_when_the_energy_is_fixed(tmp_path):
    spec = beam_spec(energy=15.0, angle={"uniform": {"low": 5.0, "high": 9.0}})
    results = beam_results(tmp_path, spec)
    assert missing_reason(BY_KEY["beam_energy"], results) == BEAM_ENERGY_TEXT
    assert missing_reason(BY_KEY["beam_angle"], results) is None


def test_beam_plots_need_an_exact_run_with_a_spec(tmp_path):
    assert missing_reason(BY_KEY["beam_energy"], Results(str(FIXTURES / "1"))) == BEAM_ENERGY_TEXT
    binned = beam_results(tmp_path, beam_spec(mode="binned"))
    assert missing_reason(BY_KEY["beam_energy"], binned) == BEAM_ENERGY_TEXT


def test_beam_data_is_capped_at_the_row_cap(tmp_path, monkeypatch):
    monkeypatch.setattr(plots, "PLOT_ROW_CAP", 50)
    results = beam_results(tmp_path, beam_spec())
    df = plot_data(BY_KEY["beam_energy"], results)
    assert len(df) == 50
    assert df.attrs["capped"] is True
    assert list(df["ion"]) == list(range(1, 51))


def test_beam_energy_draws_the_sampled_beam_and_the_density(tmp_path, ax):
    results = beam_results(tmp_path, beam_spec())
    plot = BY_KEY["beam_energy"]
    plot.draw(plot_data(plot, results), ax)
    assert ax.get_title() == plot.title
    assert ax.get_xlabel() == "energy_keV"
    assert len(ax.lines) == 1  # the defined density
