"""The plot catalogue for one run's results (Qt-free; matplotlib is never imported here).

Each :class:`PlotDef` has ``data(results)``, the frame behind the plot (what "copy data" gives),
and ``draw(frame, ax)``, which draws it and sets the title. :func:`missing_reason` says why a plot
cannot be drawn for a result (its text is the hover text) or gives ``None``; :func:`plot_data`
refuses a missing plot.

The per-ion outputs (``RANGE_3D.txt``, the event files, ``COLLISON.txt``) are never parsed whole:
they are streamed, and at most :data:`PLOT_ROW_CAP` rows are read. A frame that stopped at the
cap carries ``attrs["capped"] = True`` and its title says so. The beam plots read the TRIM.DAT
of an exact run the same way.

``results`` is a :class:`srim.output.Results`, a :class:`srim.scan.MergedResults` (its batches
are read in turn) or any object with the same attributes. Missing outputs are looked for in the
run folder (and its ``SRIM Outputs`` subfolder), not through the lazy attributes, so checking a
plot does not parse its file.
"""

import os
import re
from collections.abc import Callable, Iterator
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd
import yaml

from ..beam.exact import trim_dat_frame
from ..beam.plot import _spec_of
from ..beam.shapes import Shape
from ..beam.spec import effective
from ..output._chunks import concat_chunks, iter_lines, iter_row_chunks
from ..output._common import _find_output, _squeeze, _to_float, double_regex
from ..output.collision import EVENT_COLUMNS, Collision

#: the most rows a per-ion plot reads (the first rows of the files, in file order)
PLOT_ROW_CAP = 200_000

#: histogram bins of the energy and beam plots
_BINS = 50
#: points of the drawn defined density
_CURVE_POINTS = 400
#: a SRIM table has at most this many lines before its header line
_MAX_HEADER_LINES = 200
#: header lines of a TRIM.DAT file
_TRIM_DAT_HEADER_LINES = 10
_DEPTH_COLUMNS = ("depth_nm", "depth_A")

_NUMBER = double_regex.encode()

# --- streaming readers of the per-ion files (the same rows as the library's parsers) ----------

_RANGE3D_FILE = "RANGE_3D.txt"
_RANGE3D_HEADER = re.compile(rb"-+(?:[ \t]+-+)+[ \t]*")
_RANGE3D_ROW = re.compile(rb"[ \t]*(\d{7})" + (rb"[ \t]+(" + _NUMBER + rb")") * 3 + rb"[ \t]*")
_RANGE3D_COLUMNS = ["ion", "depth_A", "y_A", "z_A"]
_RANGE3D_DTYPES = [int, float, float, float]

_EVENT_HEADER = re.compile(rb"[ \t]*Numb[ \t]+Numb.*")
_EVENT_FIELDS = [rb"[SBT]", rb"\d+", rb"\d+", _NUMBER, rb"-?[ \t]*" + _NUMBER] + [_NUMBER] * 5
_EVENT_ROW = re.compile(rb"[ \t]*" + rb"[ \t]+".join(b"(" + f + b")" for f in _EVENT_FIELDS))
_EVENT_FRAME_COLUMNS = [
    "ion", "atomic_number", "energy_eV", "depth_A", "y_A", "z_A", "cos_x", "cos_y", "cos_z",
]  # fmt: skip
_EVENT_DTYPES = [int, int] + [float] * 7
#: the seven TRIM.DAT fields after the name and Z (the energy, depth, lateral and cosines)
_TRIM_FIELDS = ("energy_eV", "depth_A", "y_A", "z_A", "cos_x", "cos_y", "cos_z")

_COLLISION_DTYPES = [
    int if c == "ion" else object if c in ("kind", "atom") else float for c in EVENT_COLUMNS
]


def _number(token: bytes) -> float:
    return _to_float(_squeeze(token))


def _table_rows(path: str, header_end, row) -> Iterator[tuple]:
    """The regex groups of every data line of a SRIM table, streamed after its header line.

    Lines that do not match are skipped, as the library's table reader does.
    """
    found = False
    for index, raw in enumerate(iter_lines(path, None, keepends=True)):
        line = raw.rstrip(b"\r\n")
        if not found:
            if header_end.fullmatch(line):
                found = True
            elif index + 1 >= _MAX_HEADER_LINES:
                return
            continue
        match = row.fullmatch(line)
        if match:
            yield match.groups()


def _range3d_rows(folder: str) -> Iterator[list]:
    for g in _table_rows(os.path.join(folder, _RANGE3D_FILE), _RANGE3D_HEADER, _RANGE3D_ROW):
        yield [int(g[0]), _number(g[1]), _number(g[2]), _number(g[3])]


def _event_rows(folder: str, filename: str) -> Iterator[list]:
    for g in _table_rows(os.path.join(folder, filename), _EVENT_HEADER, _EVENT_ROW):
        yield [int(g[1]), int(g[2])] + [_number(g[i]) for i in range(3, 10)]


def _collision_rows(folder: str) -> Iterator[list]:
    for event in Collision(folder).iter_events():
        yield [event[c] for c in EVENT_COLUMNS]


def _trim_dat_lines(path: str) -> Iterator[list]:
    """The seven fields after the name and Z of every ion line of a TRIM.DAT file, streamed."""
    for index, line in enumerate(iter_lines(path, "latin-1")):
        if index < _TRIM_DAT_HEADER_LINES:
            continue
        text = line.replace("\x1a", "")
        if text.strip():
            yield [float(v) for v in text.split()[2:9]]


def _capped_frame(rows: Iterator[list], columns: list, dtypes: list) -> pd.DataFrame:
    """The frame of the first :data:`PLOT_ROW_CAP` rows of ``rows``, built in chunks.

    Reads at most one row past the cap; the frame is marked ``capped`` only when more rows
    existed. ``rows`` is closed at the end, so its file is closed too.
    """
    cap = PLOT_ROW_CAP
    state = {"capped": False}

    def first_rows():
        for index, row in enumerate(rows):
            if index == cap:
                state["capped"] = True
                return
            yield row

    try:
        frame = concat_chunks(iter_row_chunks(first_rows(), columns, dtypes), columns, dtypes)
    finally:
        rows.close()
    if state["capped"]:
        frame.attrs["capped"] = True
    return frame


def _chain(folders: list[str], rows_of: Callable[[str], Iterator[list]]) -> Iterator[list]:
    """The rows of each folder in turn (``rows_of`` gives a folder's rows)."""
    for folder in folders:
        yield from rows_of(folder)


# --- the run's outputs ----------------------------------------------------------------------


def _run_units(results) -> list[tuple[int, str | None]]:
    """``(batch index, folder)`` of each run folder: one for a run, one per batch when merged."""
    batches = getattr(results, "batches", None)
    if batches is not None:
        return [(b.index, b.directory) for b in batches]
    return [(0, getattr(results, "directory", None))]


def _output_folders(results, filename: str) -> list[str] | None:
    """The folder holding ``filename`` for every run folder, or ``None`` if one lacks it."""
    folders = []
    for _, folder in _run_units(results):
        found = _find_output(folder, filename) if folder is not None else None
        if found is None:
            return None
        folders.append(found)
    return folders or None


def _has_file(filename: str) -> Callable[[Any], bool]:
    return lambda results: _output_folders(results, filename) is not None


def _has_table(attribute: str) -> Callable[[Any], bool]:
    return lambda results: getattr(results, attribute, None) is not None


def _beam_spec(results):
    """The run spec of ``results``, or ``None`` when there is none."""
    try:
        return _spec_of(results)
    except (OSError, ValueError, yaml.YAMLError):
        return None


def _trim_dat_present(results) -> bool:
    units = _run_units(results)
    return bool(units) and all(
        folder is not None and (Path(folder) / "TRIM.DAT").is_file() for _, folder in units
    )


def _beam_available(parameter: str, results) -> bool:
    """An exact run whose ``parameter`` is distributed, with its TRIM.DAT file(s)."""
    if getattr(results, "beam_bins", None) is not None:
        return False
    spec = _beam_spec(results)
    if spec is None:
        return False
    mode = "exact" if spec.beam is None else spec.beam.mode
    if mode != "exact" or not isinstance(effective(spec)[parameter], Shape):
        return False
    return _trim_dat_present(results)


def _beam_rows(results) -> Iterator[list]:
    for batch, folder in _run_units(results):
        for values in _trim_dat_lines(os.path.join(folder, "TRIM.DAT")):
            yield [batch, *values]


# --- data -----------------------------------------------------------------------------------


def _with_depth_nm(frame: pd.DataFrame) -> pd.DataFrame:
    """``depth_nm`` (the depth in nm, from ``depth_A``) as the first column."""
    frame.insert(0, "depth_nm", frame["depth_A"].to_numpy(dtype=float) / 10.0)
    return frame


def _with_lateral(frame: pd.DataFrame) -> pd.DataFrame:
    """``depth_nm`` and ``lateral_nm`` (the lateral distance sqrt(y^2 + z^2), in nm)."""
    frame["depth_nm"] = frame["depth_A"].to_numpy(dtype=float) / 10.0
    frame["lateral_nm"] = (
        np.hypot(frame["y_A"].to_numpy(float), frame["z_A"].to_numpy(float)) / 10.0
    )
    return frame


def _profile(attribute: str) -> Callable[[Any], pd.DataFrame]:
    """A depth profile of the run: the table's own frame, with ``depth_nm`` first."""

    def data(results):
        return _with_depth_nm(getattr(results, attribute).df.copy())

    return data


def _vacancies(results) -> pd.DataFrame:
    df = results.vacancy.df
    per_element = [c for c in df.columns if c.startswith("vacancies_")]
    frame = pd.DataFrame(
        {
            "depth_A": df["depth_A"].to_numpy(),
            "vacancies_total_per_A_per_ion": df[per_element].sum(axis=1).to_numpy(),
        }
    )
    return _with_depth_nm(frame)


def _damage_energy(results) -> pd.DataFrame:
    """The damage energy by depth, with the scaling of :func:`srim.plot.plot_damage_energy`."""
    phonons = results.phonons
    df = phonons.df
    depth = df["depth_A"].to_numpy(dtype=float)
    ions = df["ions_phonons_per_A_per_ion"].to_numpy(dtype=float)
    recoils = df["recoils_phonons_per_A_per_ion"].to_numpy(dtype=float)
    num_ions = getattr(phonons, "num_ions", None)  # merged tables have none: the run's count
    if num_ions is None:
        num_ions = results.n_ions
    energy = (ions + recoils) * (depth.max() / 100.0)
    frame = pd.DataFrame({"depth_A": depth, "damage_energy_per_ion": energy / num_ions})
    return _with_depth_nm(frame)


def _range3d(results) -> pd.DataFrame:
    frame = _capped_frame(
        _chain(_output_folders(results, _RANGE3D_FILE), _range3d_rows),
        _RANGE3D_COLUMNS,
        _RANGE3D_DTYPES,
    )
    return _with_lateral(frame)


def _events(filename: str) -> Callable[[Any], pd.DataFrame]:
    def data(results):
        folders = _output_folders(results, filename)
        rows = _chain(folders, lambda folder: _event_rows(folder, filename))
        return _capped_frame(rows, _EVENT_FRAME_COLUMNS, _EVENT_DTYPES)

    return data


def _collision(results) -> pd.DataFrame:
    frame = _capped_frame(
        _chain(_output_folders(results, "COLLISON.txt"), _collision_rows),
        EVENT_COLUMNS,
        _COLLISION_DTYPES,
    )
    return _with_lateral(frame)


def _beam(parameter: str) -> Callable[[Any], pd.DataFrame]:
    def data(results):
        density = effective(_beam_spec(results))[parameter]
        frame = _capped_frame(
            _beam_rows(results),
            ["batch", *_TRIM_FIELDS],
            [int] + [float] * 7,
        )
        table = frame[list(_TRIM_FIELDS)].to_numpy(dtype=float).reshape(-1, 7)
        ions = trim_dat_frame(table)
        ions.insert(1, "batch", frame["batch"].to_numpy(dtype=int))
        ions.attrs["density"] = density
        if frame.attrs.get("capped"):
            ions.attrs["capped"] = True
        return ions

    return data


# --- drawing --------------------------------------------------------------------------------


def _caption(title: str, frame: pd.DataFrame) -> str:
    if frame.attrs.get("capped"):
        return f"{title} (first {PLOT_ROW_CAP:,} rows)"
    return title


def _titled(title: str, draw: Callable[[pd.DataFrame, Any], None]):
    def drawn(frame, ax):
        draw(frame, ax)
        ax.set_title(_caption(title, frame))

    return drawn


def _draw_profile(frame, ax) -> None:
    depth = frame["depth_nm"].to_numpy(dtype=float)
    for column in frame.columns:
        if column not in _DEPTH_COLUMNS:
            ax.plot(depth, frame[column].to_numpy(dtype=float), label=column)
    ax.set_xlabel("depth (nm)")
    ax.legend()


def _draw_map(frame, ax) -> None:
    if len(frame) == 0:
        ax.text(0.5, 0.5, "no rows", transform=ax.transAxes, ha="center")
        return
    _, _, _, mesh = ax.hist2d(
        frame["depth_nm"].to_numpy(dtype=float),
        frame["lateral_nm"].to_numpy(dtype=float),
        bins=60,
    )
    ax.figure.colorbar(mesh, ax=ax, label="count")
    ax.set_xlabel("depth (nm)")
    ax.set_ylabel("lateral distance (nm)")


def _draw_energies(frame, ax) -> None:
    values = frame["energy_eV"].to_numpy(dtype=float)
    if values.size:
        ax.hist(values, bins=_BINS)
    ax.set_xlabel("energy (eV)")
    ax.set_ylabel("count")


def _beam_draw(parameter: str):
    def draw(frame, ax) -> None:
        values = frame[parameter].to_numpy(dtype=float)
        if values.size:
            ax.hist(values, bins=_BINS, density=True, label="sampled ions")
        shape = frame.attrs["density"]
        low, high = shape.support()
        curve = np.linspace(low, high, _CURVE_POINTS)
        ax.plot(curve, np.asarray(shape.pdf(curve), dtype=float), label="defined density")
        ax.set_xlabel(parameter)
        ax.set_ylabel("probability density")
        ax.legend()

    return draw


# --- the catalogue --------------------------------------------------------------------------


@dataclass(frozen=True)
class PlotDef:
    """One plot: its ``key``, ``group``, ``title``, the ``needs`` text (the hover text when the
    output is missing), and the ``data(results)`` and ``draw(frame, ax)`` functions."""

    key: str
    group: str
    title: str
    needs: str
    data: Callable[[Any], pd.DataFrame]
    draw: Callable[[pd.DataFrame, Any], None]


def _plot(key, group, title, needs, data, draw) -> PlotDef:
    return PlotDef(key, group, title, needs, data, _titled(title, draw))


_RANGE3D_TEXT = "needs RANGE_3D.txt; enable 'Ranges (RANGE_3D.txt)' in Run settings"
_COLLISION_TEXT = (
    "needs COLLISON.txt; choose 'Ions (COLLISON.txt)' or 'Ions and recoils (COLLISON.txt)' "
    "for 'Collisions' in Run settings"
)
_BEAM_ENERGY_TEXT = "needs an exact-mode run with an energy distribution"
_BEAM_ANGLE_TEXT = "needs an exact-mode run with an angle distribution"

CATALOGUE: tuple[PlotDef, ...] = (
    _plot(
        "ionization",
        "Depth profiles",
        "Ionization (ions and recoils)",
        "needs IONIZ.txt",
        _profile("ioniz"),
        _draw_profile,
    ),  # fmt: skip
    _plot(
        "vacancies",
        "Depth profiles",
        "Vacancies (summed over elements)",
        "needs VACANCY.txt",
        _vacancies,
        _draw_profile,
    ),  # fmt: skip
    _plot(
        "phonons",
        "Depth profiles",
        "Phonons",
        "needs PHONON.txt",
        _profile("phonons"),
        _draw_profile,
    ),  # fmt: skip
    _plot(
        "recoil_energy",
        "Depth profiles",
        "Energy to recoils",
        "needs E2RECOIL.txt",
        _profile("etorecoils"),
        _draw_profile,
    ),  # fmt: skip
    _plot(
        "range_profile",
        "Depth profiles",
        "Ion range profile",
        "needs RANGE.txt",
        _profile("range"),
        _draw_profile,
    ),  # fmt: skip
    _plot(
        "stopping_map",
        "Ion distributions",
        "Where the ions stop (depth × lateral)",
        _RANGE3D_TEXT,
        _range3d,
        _draw_map,
    ),  # fmt: skip
    _plot(
        "backscattered",
        "Ion distributions",
        "Energies of backscattered ions",
        "needs BACKSCAT.txt; enable 'Backscattered ions (BACKSCAT.txt)' in Run settings",
        _events("BACKSCAT.txt"),
        _draw_energies,
    ),  # fmt: skip
    _plot(
        "transmitted",
        "Ion distributions",
        "Energies of transmitted ions",
        "needs TRANSMIT.txt; enable 'Transmitted ions (TRANSMIT.txt)' in Run settings",
        _events("TRANSMIT.txt"),
        _draw_energies,
    ),  # fmt: skip
    _plot(
        "sputtered",
        "Ion distributions",
        "Energies of sputtered atoms",
        "needs SPUTTER.txt; enable 'Sputtered atoms (SPUTTER.txt)' in Run settings",
        _events("SPUTTER.txt"),
        _draw_energies,
    ),  # fmt: skip
    _plot(
        "collision_map",
        "Damage",
        "Collision cascades (depth × lateral)",
        _COLLISION_TEXT,
        _collision,
        _draw_map,
    ),  # fmt: skip
    _plot(
        "damage_energy",
        "Damage",
        "Damage energy",
        "needs PHONON.txt",
        _damage_energy,
        _draw_profile,
    ),  # fmt: skip
    _plot(
        "beam_energy",
        "Beam",
        "Sampled energies over the distribution",
        _BEAM_ENERGY_TEXT,
        _beam("energy_keV"),
        _beam_draw("energy_keV"),
    ),  # fmt: skip
    _plot(
        "beam_angle",
        "Beam",
        "Sampled angles over the distribution",
        _BEAM_ANGLE_TEXT,
        _beam("angle_deg"),
        _beam_draw("angle_deg"),
    ),  # fmt: skip
)

#: whether each plot's output is present, by key (checked without reading any file)
_AVAILABLE: dict[str, Callable[[Any], bool]] = {
    "ionization": _has_table("ioniz"),
    "vacancies": _has_table("vacancy"),
    "phonons": _has_table("phonons"),
    "recoil_energy": _has_table("etorecoils"),
    "range_profile": _has_table("range"),
    "stopping_map": _has_file(_RANGE3D_FILE),
    "backscattered": _has_file("BACKSCAT.txt"),
    "transmitted": _has_file("TRANSMIT.txt"),
    "sputtered": _has_file("SPUTTER.txt"),
    "collision_map": _has_file("COLLISON.txt"),
    "damage_energy": _has_table("phonons"),
    "beam_energy": lambda results: _beam_available("energy_keV", results),
    "beam_angle": lambda results: _beam_available("angle_deg", results),
}


def missing_reason(plot: PlotDef, results) -> str | None:
    """``None`` when ``plot`` can be drawn for ``results``, else the text saying what it needs."""
    return None if _AVAILABLE[plot.key](results) else plot.needs


def plot_data(plot: PlotDef, results) -> pd.DataFrame:
    """The data behind ``plot`` for ``results`` (the frame that ``plot.draw`` draws).

    Raises
    ------
    ValueError
        with the :func:`missing_reason` text when the plot cannot be drawn for ``results``
    """
    reason = missing_reason(plot, results)
    if reason is not None:
        raise ValueError(reason)
    return plot.data(results)
