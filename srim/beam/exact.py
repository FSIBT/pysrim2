"""Exact mode: sample every ion of a distributed beam and write SRIM's TRIM.DAT.

:func:`sample_ions` draws each beam parameter from its own random stream, so the energies do
not change when a position is added. :func:`write_trim_dat` writes the ions as a TRIM.DAT file
(CRLF lines, 10 header lines, a trailing 0x1A); SRIM then runs exactly those ions when TRIM.IN
has calculation 4 (quick Kinchin-Pease damage) or 5 (full cascades). :func:`read_trim_dat`
reads such a file back.

Layout of a TRIM.DAT data line (SRIM reads the fields in this order)::

    I0001  5  20000  0  0  0  0.5000000  0.8660254  0.0000000

The name (exactly 5 characters: SRIM reads the first 5 characters of the line as the name,
so a shorter name followed by a single space makes TRIM fail with a division-by-zero
dialog), the ion's Z, the energy (eV), the start position x (depth, Angstrom), y and z
(Angstrom) and the direction cosines (cos x along the depth axis, cos y, cos z).
"""

import contextlib
import math
import os
from decimal import Decimal
from pathlib import Path

import numpy as np
import pandas as pd

from .shapes import Shape
from .spec import MAX_TRIM_DAT_IONS, effective

#: the parameters that are sampled, in the order of their random streams
STREAMS = ("energy_keV", "angle_deg", "azimuth_deg", "y_nm", "z_nm", "depth_nm")

#: the columns of :func:`sample_ions` and :func:`read_trim_dat`
COLUMNS = (
    "ion",
    *STREAMS,
    "energy_eV",
    "depth_A",
    "y_A",
    "z_A",
    "cos_x",
    "cos_y",
    "cos_z",
)

#: direction cosines of a written ion must satisfy |1 - sum of squares| below this
_NORMALISATION_TOLERANCE = 1e-6

#: the start of the first line of every TRIM.DAT written here (how pysrim2 knows its own file)
TRIM_DAT_MARKER = "pysrim2 exact beam:"

_COLUMN_HEADERS = (
    "Name  Z  Energy_eV  X_depth_A  Y_A  Z_A  cosX  cosY  cosZ",
    "(one ion per line; the file ends with 0x1A)",
)


def sample_ions(spec, n=None, seed=None) -> pd.DataFrame:
    """The ions of a distributed beam: one row per ion, columns :data:`COLUMNS`.

    The values come from :func:`srim.beam.spec.effective` of ``spec``. Each parameter is drawn
    from its own stream, ``SeedSequence(seed).spawn(6)`` in the order of :data:`STREAMS`; a
    number (fixed parameter) draws nothing. ``n`` defaults to ``spec.number_ions`` and
    ``seed`` to ``spec.seed``.

    The direction cosines are ``cos_x = cos θ``, ``cos_y = sin θ cos φ`` and
    ``cos_z = sin θ sin φ`` for the tilt θ (``angle_deg``) and azimuth φ (``azimuth_deg``).
    """
    n = spec.number_ions if n is None else int(n)
    seed = spec.seed if seed is None else int(seed)
    values = effective(spec)
    children = np.random.SeedSequence(seed).spawn(len(STREAMS))
    drawn = {}
    for name, child in zip(STREAMS, children, strict=True):
        value = values[name]
        if isinstance(value, Shape):
            rng = np.random.Generator(np.random.PCG64(child))
            drawn[name] = np.asarray(value.sample(rng, n), dtype=float)
        else:
            drawn[name] = np.full(n, float(value))

    theta = np.radians(drawn["angle_deg"])
    phi = np.radians(drawn["azimuth_deg"])
    frame = pd.DataFrame(
        {
            "ion": np.arange(1, n + 1),
            **drawn,
            "energy_eV": drawn["energy_keV"] * 1000.0,
            "depth_A": drawn["depth_nm"] * 10.0,
            "y_A": drawn["y_nm"] * 10.0,
            "z_A": drawn["z_nm"] * 10.0,
            "cos_x": np.cos(theta),
            "cos_y": np.sin(theta) * np.cos(phi),
            "cos_z": np.sin(theta) * np.sin(phi),
        }
    )
    return frame[list(COLUMNS)]


def max_energy_eV(ions: pd.DataFrame) -> float:
    """The largest energy (eV) of ``ions`` as TRIM.DAT writes it (``.7g``)."""
    return max(float(f"{e:.7g}") for e in ions["energy_eV"].to_numpy(dtype=float))


def trim_in_energy_keV(max_eV: float) -> float:
    """The TRIM.IN ion energy (keV) for a TRIM.DAT whose largest energy is ``max_eV``.

    It is ``max_eV / 1000`` written exactly in decimal, moved up by one float step if needed,
    so that ``energy_keV * 1000 >= max_eV`` holds for the parsed floats.
    """
    energy = float(Decimal(repr(float(max_eV))) / 1000)
    while energy * 1000.0 < max_eV:
        energy = math.nextafter(energy, math.inf)
    return energy


def _line(ion, atomic_number, energy, x, y, z, cos_x, cos_y, cos_z) -> str:
    return (
        f"I{int(ion) % 10000:04d}  {atomic_number}  {energy:.7g}  {x:.7g}  {y:.7g}  {z:.7g}  "
        f"{cos_x:.7f}  {cos_y:.7f}  {cos_z:.7f}"
    )


def write_trim_dat(ions: pd.DataFrame, path, *, atomic_number: int, description: str, seed: int):
    """Write ``ions`` (from :func:`sample_ions`) to a TRIM.DAT file at ``path``.

    The file has 10 header lines (line 8 is ``description``, as SRIM copies it into every
    output file), one line per ion, CRLF line ends and a trailing 0x1A. Nothing is written
    when a line is refused.

    Returns
    -------
    float
        the largest energy (eV) as written

    Raises
    ------
    ValueError
        for more than :data:`~srim.beam.spec.MAX_TRIM_DAT_IONS` ions, an ion whose written
        ``cos_x`` is not positive, a written energy that is not positive, direction cosines that
        are not normalised after formatting, or a description with a line break
    """
    n = len(ions)
    if n == 0:
        raise ValueError("a TRIM.DAT file needs at least one ion")
    if n > MAX_TRIM_DAT_IONS:
        raise ValueError(
            f"{n} ions do not fit in one TRIM.DAT file (at most {MAX_TRIM_DAT_IONS}); "
            "use srim.beam.run_beam to split the beam"
        )
    if "\r" in description or "\n" in description:
        raise ValueError("the description of a TRIM.DAT file must be one line")

    header = [
        f"{TRIM_DAT_MARKER} TRIM.DAT sampled from the run spec",
        "Written by pysrim2; do not edit by hand.",
        f"Seed: {seed}",
        f"Ions: {n}",
        "Columns: name, Z, energy (eV), X depth (A), Y (A), Z (A), cosX, cosY, cosZ",
        "Direction cosines are normalised; cosX > 0 is into the target.",
        "Every ion is simulated by TRIM with the calculation given in TRIM.IN.",
        description,
        *_COLUMN_HEADERS,
    ]

    data = []
    energies = []
    for row in ions.itertuples(index=False):
        ion = row.ion
        energy = f"{row.energy_eV:.7g}"
        cos_x, cos_y, cos_z = (f"{c:.7f}" for c in (row.cos_x, row.cos_y, row.cos_z))
        parsed = [float(cos_x), float(cos_y), float(cos_z)]
        if parsed[0] <= 0.0:
            raise ValueError(f"ion {ion}: cos x = {cos_x} is not positive (the ion must go in)")
        if abs(1.0 - sum(c * c for c in parsed)) >= _NORMALISATION_TOLERANCE:
            raise ValueError(
                f"ion {ion}: direction cosines {cos_x} {cos_y} {cos_z} are not normalised"
            )
        if float(energy) <= 0.0:
            raise ValueError(f"ion {ion}: energy {row.energy_eV} eV is not positive")
        energies.append(float(energy))
        data.append(
            _line(
                ion,
                atomic_number,
                row.energy_eV,
                row.depth_A,
                row.y_A,
                row.z_A,
                row.cos_x,
                row.cos_y,
                row.cos_z,
            )
        )

    text = "".join(line + "\r\n" for line in header + data) + "\x1a"
    payload = text.encode("ascii")
    path = os.fspath(path)
    # remove first: a hard link to an installed file must not be written through
    with contextlib.suppress(FileNotFoundError):
        os.remove(path)
    with open(path, "wb") as f:
        f.write(payload)
    return max(energies)


def read_trim_dat(path) -> pd.DataFrame:
    """The ions of a TRIM.DAT file written by :func:`write_trim_dat`.

    Returns a frame with the columns of :func:`sample_ions`; the parameter columns are derived
    back from the file: ``angle_deg = degrees(arccos cos_x)`` and
    ``azimuth_deg = atan2(cos_z, cos_y) mod 360``. ``ion`` is the line order, 1..n.
    """
    raw = Path(path).read_bytes()
    if raw.endswith(b"\x1a"):
        raw = raw[:-1]
    lines = [line for line in raw.decode("ascii").split("\r\n")[10:] if line.strip()]
    rows = [line.split() for line in lines]
    table = np.array([[float(v) for v in r[2:9]] for r in rows], dtype=float).reshape(-1, 7)
    return trim_dat_frame(table)


def trim_dat_frame(table) -> pd.DataFrame:
    """The :func:`read_trim_dat` frame of a table of TRIM.DAT fields, one row per ion.

    ``table`` has one row per ion and the seven fields after the name and Z: energy (eV), depth
    X, Y, Z (Angstrom) and the direction cosines. ``ion`` is the row order, 1..n.
    """
    energy_eV, depth_A, y_A, z_A, cos_x, cos_y, cos_z = np.asarray(table, dtype=float).T
    frame = pd.DataFrame(
        {
            "ion": np.arange(1, len(energy_eV) + 1),
            "energy_keV": energy_eV / 1000.0,
            "angle_deg": np.degrees(np.arccos(np.clip(cos_x, -1.0, 1.0))),
            "azimuth_deg": np.mod(np.degrees(np.arctan2(cos_z, cos_y)), 360.0),
            "y_nm": y_A / 10.0,
            "z_nm": z_A / 10.0,
            "depth_nm": depth_A / 10.0,
            "energy_eV": energy_eV,
            "depth_A": depth_A,
            "y_A": y_A,
            "z_A": z_A,
            "cos_x": cos_x,
            "cos_y": cos_y,
            "cos_z": cos_z,
        }
    )
    return frame[list(COLUMNS)]
