"""Parser for the SRIM COLLISON.txt file"""

import os
import re
import warnings

import numpy as np

from ..core.elementdb import ElementDB
from ._chunks import concat_chunks, iter_lines, iter_row_chunks
from ._common import SRIMOutputParseError, _to_float, double_regex

#: SRIM writes ``COLLISON.txt`` in code page 437; its box-drawing characters separate columns
ENCODING = "cp437"
#: column separator of ion collision and cascade summary lines (cp437 byte 179)
_SEP = "│"
#: column separator of cascade recoil lines (cp437 byte 219)
_RECOIL_SEP = "█"
_SYMBOL = re.compile(r"[A-Z][a-z]?")
_NUMBER = re.compile(double_regex)

EVENT_COLUMNS = [
    "ion",
    "kind",
    "energy_keV",
    "depth_A",
    "y_A",
    "z_A",
    "atom",
    "recoil_energy_eV",
    "target_displacements",
    "target_vacancies",
    "target_replacements",
    "target_interstitials",
    "atomic_number",
    "electronic_stopping_eV_per_A",
    "vacancy",
    "replacement",
]
_EVENT_DTYPES = [int, object, float, float, float, float, object, float, float, float, float,
                 float, float, float, float, float]  # fmt: skip


def _parse_collision(tokens):
    """Fields of an ion collision line (already split on the column separator)"""
    if len(tokens) < 9:
        raise SRIMOutputParseError(f"unexpected collision line: {tokens!r}")
    cascade = re.match(r"\s*<== Start of New Cascade", tokens[8]) is not None
    return {
        "ion_number": int(tokens[0]),
        "kinetic_energy": _to_float(tokens[1]),
        "depth": _to_float(tokens[2]),
        "lat_y_dist": _to_float(tokens[3]),
        "lat_z_dist": _to_float(tokens[4]),
        "stopping_energy": _to_float(tokens[5]),
        "atom": _SYMBOL.search(tokens[6]).group(0),
        "recoil_energy": _to_float(tokens[7]),
        "cascade_start": cascade,
        # Kinchin-Pease files (no full cascades): the summary columns follow in this line
        "summary": None if cascade else [_to_float(t) for t in tokens[8:12] if t.strip()],
    }


def _parse_recoil(line):
    values = _NUMBER.findall(line.split(_RECOIL_SEP)[1])
    if len(values) != 8:
        raise SRIMOutputParseError(f"unexpected cascade recoil line: {line!r}")
    return {
        "recoil": int(values[0]),
        "atom": int(values[1]),
        "recoil_energy": _to_float(values[2]),
        "position": np.array([_to_float(values[3]), _to_float(values[4]), _to_float(values[5])]),
        "vac": int(values[6]),
        "repl": int(values[7]),
    }


def _parse_footer(first, lines):
    """Ion footer: ``first`` is the ``- For Ion`` line, ``lines`` the six lines after it"""
    values = [_to_float(v) for line in lines for v in _NUMBER.findall(line[1:])]
    if len(values) != 12:
        raise SRIMOutputParseError("unable to extract ion summary from file")
    names = ["displacements", "replacements", "vacancies", "interstitials"]
    names += ["sputtered_atoms", "transmitted_atoms"]
    footer = {"ion_number": int(_NUMBER.search(first[1:]).group(0).split(".")[0].lstrip("+"))}
    for i, name in enumerate(names):
        footer[name] = values[2 * i]
        footer["avg_" + name] = values[2 * i + 1]
    return footer


def _records(lines, skipped=None):
    """Walk the lines of COLLISON.txt, yielding ``("ion", collision, recoils, summary)``
    for every ion collision (with the recoils of its cascade and the cascade summary line,
    ``None`` if SRIM wrote none) and ``("footer", dict)`` for every ion footer.

    Lines are consumed one at a time; only the cascade of one collision is held.
    ``skipped``, if given, is a dict whose ``"recoil"`` and ``"summary"`` counts are
    incremented for each stray recoil line / orphan summary line that is skipped.
    """
    collision = None
    recoils = []
    summary = None
    closed = True
    footer = None  # [first line, [lines]]

    def pending():
        return ("ion", collision, recoils, summary)

    for line in lines:
        if footer is not None:
            if line.startswith("- "):
                footer[1].append(line)
                if len(footer[1]) < 6:
                    continue
            yield ("footer", _parse_footer(*footer))
            footer = None
            if line.startswith("- "):
                continue
        first = line[:1]
        if first == _SEP:
            tokens = line.split(_SEP)[1:-1]
            if tokens and "Summary of Above Cascade" in tokens[0]:
                # a second summary for one collision (SRIM lost the header line of a
                # cascade) belongs to no collision and is skipped
                if collision is None or closed:
                    if skipped is not None:
                        skipped["summary"] += 1
                elif len(tokens) >= 6:
                    summary = [_to_float(t) for t in tokens[1:6]]
                closed = True
                continue
            if collision is not None:
                yield pending()
            collision, recoils, summary, closed = _parse_collision(tokens), [], None, False
        elif first == _RECOIL_SEP:
            # SRIM sometimes leaves stray recoil lines (recoil number 0) of a cascade whose
            # header is missing, after the summary of a cascade or between two ions: they
            # belong to no collision and are skipped
            if collision is not None and not closed:
                recoils.append(_parse_recoil(line))
            elif skipped is not None:
                skipped["recoil"] += 1
        elif line.startswith("- For Ion"):
            if collision is not None:
                yield pending()
                collision = None
            footer = [line, []]
    if collision is not None:
        yield pending()
    if footer is not None:
        yield ("footer", _parse_footer(*footer))


def _collision_dict(collision, recoils, summary):
    """Per-collision dict of the per-ion API"""
    if collision["cascade_start"]:
        cascade = recoils
        if summary is None:
            targets = [None] * 4
        else:
            targets = summary[1:5]
    else:
        cascade = None
        targets = collision["summary"] or []
        targets = [targets[0] if targets else None, 0, 0, 0]
    return {
        "ion_number": collision["ion_number"],
        "kinetic_energy": collision["kinetic_energy"],
        "depth": collision["depth"],
        "lat_y_dist": collision["lat_y_dist"],
        "lat_z_dist": collision["lat_z_dist"],
        "stopping_energy": collision["stopping_energy"],
        "atom": collision["atom"],
        "recoil_energy": collision["recoil_energy"],
        "target_disp": targets[0],
        "target_vac": targets[1],
        "target_replac": targets[2],
        "target_inter": targets[3],
        "cascade": cascade,
    }


_SYMBOLS = {}


def _symbol(atomic_number):
    if atomic_number not in _SYMBOLS:
        _SYMBOLS[atomic_number] = ElementDB.lookup(atomic_number)["symbol"]
    return _SYMBOLS[atomic_number]


def _as_int(value):
    return None if value is None else int(value)


class Collision:
    """Reads the SRIM Collisions file.

    This is the most important file in my opinion. It records every
    single collision and its energies. The file will get huge for
    simulations with many collisions, so it is never read as a whole:
    :meth:`iter_events` and :attr:`df` stream it line by line, and ``collision[i]``
    reads only the block of ion ``i``.

    The file is decoded as ``cp437`` and split at SRIM's column separators (the
    box-drawing characters), not at white space.

    Parameters
    ----------
    directory : :obj:`str`
         directory of calculation
    filename : :obj:`str`, optional
         filename for Collisions. Default ``COLLISON.txt``

    Notes
    -----
    ``len(collision)`` is the number of ions and ``collision[i]`` a :obj:`dict` with
    the ion's ``collisions`` and its totals (``displacements``, ``avg_displacements``,
    ``replacements``, ``vacancies``, ``interstitials``, ``sputtered_atoms``,
    ``transmitted_atoms``, each also with an ``avg_`` variant).

    """

    def __init__(self, directory, filename="COLLISON.txt"):
        self.filename = os.path.join(directory, filename)
        self._index = None
        self._skipped = None

        with open(self.filename, encoding=ENCODING) as f:
            self._read_header(f)

    @property
    def _ion_index(self):
        """Byte offsets of the ion blocks (found on first use, scanning the file once)"""
        if self._index is None:
            self._index = buffered_findall(self.filename, b"  Ion    Energy")
        return self._index

    def _read_header(self, f):
        """Read Header of COLLISON.txt

        Currently we do nothing with the header
        """

        # Collect the header of the file
        header = []

        for line in f:
            if line.strip() == "":
                break
            header.append(line)
        return header

    def _read_ion(self, ion_str):
        """Parse the text of one ion block (collisions, cascades and the ion footer).

        There are 2 types of files with and without cascades

        format:
           1 - Kinchin-Pease Theory (No full cascades)
           2 - full cascades
        """
        collisions = []
        footer = None
        for record in _records(ion_str.splitlines()):
            if record[0] == "ion":
                collisions.append(_collision_dict(*record[1:]))
            else:
                footer = record[1]
        if footer is None:
            raise SRIMOutputParseError("unable to extract ion summary from file")
        footer["collisions"] = collisions
        return footer

    def iter_events(self):
        """Generate every event in the file as a flat :obj:`dict`, streaming the file.

        Each ion collision is yielded (``kind="ion"``) followed by the recoils of its
        cascade (``kind="recoil"``), in file order. Keys, and the part of the file they
        come from:

        ====================================  =============================================
        ``ion``                               ion number (int) of the collision line
        ``kind``                              ``"ion"`` for a line of the collision table,
                                              ``"recoil"`` for a line of a cascade block
        ``energy_keV``                        ion: ion energy [keV]; recoil: ``None``
        ``depth_A``                           ion: depth; recoil: ``X (A)`` of the cascade line
        ``y_A``, ``z_A``                      lateral position [Ang] (both kinds)
        ``atom``                              ion: symbol of the atom hit; recoil: symbol of
                                              the recoil (from its atomic number)
        ``recoil_energy_eV``                  ion: energy given to the atom hit;
                                              recoil: ``Atom Energy(eV)``
        ``target_displacements``              ion: totals of the ``Summary of Above Cascade``
        ``target_vacancies``                  line of that collision (in files without full
        ``target_replacements``               cascades: the columns of the collision line);
        ``target_interstitials``              ``None`` if SRIM wrote none; recoil: ``None``
        ``atomic_number``                     recoil: Z; ion: ``None``
        ``electronic_stopping_eV_per_A``      ion: ``Se (eV/A)``; recoil: ``None``
        ``vacancy``, ``replacement``          recoil: the ``Vac`` and ``Repl`` flags
                                              (0 or 1); ion: ``None``
        ====================================  =============================================

        Recoil lines that belong to no collision (SRIM writes some with recoil number 0
        when the header line of a cascade is lost) and summary lines without a collision
        are skipped; their number is in :attr:`skipped_lines`, and a warning is issued once
        per :class:`Collision` after the first complete pass.

        Cascade positions are printed by SRIM with fewer digits than the collision
        lines (the depth ``2718.E+00`` against ``27179.E-01``).
        """
        skipped = {"recoil": 0, "summary": 0}
        for record in _records(iter_lines(self.filename, ENCODING), skipped):
            if record[0] != "ion":
                continue
            _, c, recoils, summary = record
            if c["cascade_start"]:
                targets = summary[1:5] if summary else [None] * 4
            else:
                values = (c["summary"] or []) + [None] * 4
                targets = values[:4]
            yield {
                "ion": c["ion_number"],
                "kind": "ion",
                "energy_keV": c["kinetic_energy"],
                "depth_A": c["depth"],
                "y_A": c["lat_y_dist"],
                "z_A": c["lat_z_dist"],
                "atom": c["atom"],
                "recoil_energy_eV": c["recoil_energy"],
                "target_displacements": _as_int(targets[0]),
                "target_vacancies": _as_int(targets[1]),
                "target_replacements": _as_int(targets[2]),
                "target_interstitials": _as_int(targets[3]),
                "atomic_number": None,
                "electronic_stopping_eV_per_A": c["stopping_energy"],
                "vacancy": None,
                "replacement": None,
            }
            for r in recoils:
                yield {
                    "ion": c["ion_number"],
                    "kind": "recoil",
                    "energy_keV": None,
                    "depth_A": float(r["position"][0]),
                    "y_A": float(r["position"][1]),
                    "z_A": float(r["position"][2]),
                    "atom": _symbol(r["atom"]),
                    "recoil_energy_eV": r["recoil_energy"],
                    "target_displacements": None,
                    "target_vacancies": None,
                    "target_replacements": None,
                    "target_interstitials": None,
                    "atomic_number": r["atom"],
                    "electronic_stopping_eV_per_A": None,
                    "vacancy": r["vac"],
                    "replacement": r["repl"],
                }

        first_pass = self._skipped is None
        self._skipped = skipped
        if first_pass and any(skipped.values()):
            warnings.warn(
                f"{os.path.basename(self.filename)}: skipped {skipped['recoil']} stray recoil "
                f"line(s) and {skipped['summary']} cascade summary line(s) that belong to no "
                "collision (see Collision.skipped_lines)",
                stacklevel=2,
            )

    @property
    def metadata(self):
        """:obj:`dict` with the ``source`` file of the events"""
        return {"source": self.filename}

    def to_parquet(self, path, chunk_rows=100_000, spec=None):
        """Stream all events (see :meth:`iter_events`) into a Parquet file

        Needs ``pip install pysrim2[parquet]``. Events are written ``chunk_rows`` at a
        time (one row group each), so memory does not grow with the file. The schema is
        fixed: ``ion`` int64, ``kind`` and ``atom`` strings, all other columns float64;
        fields that do not apply to an event are null. The pysrim2 metadata is stored
        under the schema key ``pysrim2`` (see :mod:`srim.export`).

        Returns
        -------
        :obj:`int`
            number of events written
        """
        from ..export import collision_to_parquet

        return collision_to_parquet(self, path, chunk_rows, self.metadata, spec)

    def to_csv(self, path, chunk_rows=100_000):
        """Stream all events into a CSV file, ``chunk_rows`` at a time

        Not-applicable fields are empty. Returns the number of events written.
        """
        from ..export import collision_to_csv

        return collision_to_csv(self, path, chunk_rows)

    @property
    def skipped_lines(self):
        """:obj:`dict` with the number of lines skipped as stray: ``"recoil"`` (recoil lines
        outside a cascade) and ``"summary"`` (cascade summaries without a collision).

        Counted over the whole file; the first access streams it once if
        :meth:`iter_events` has not run to the end yet.
        """
        if self._skipped is None:
            for _ in self.iter_events():
                pass
        return dict(self._skipped)

    @property
    def df(self):
        """:class:`pandas.DataFrame` of :meth:`iter_events`, one row per event, built chunk-wise
        from the stream (empty if the file has no collisions)"""
        rows = ([event[c] for c in EVENT_COLUMNS] for event in self.iter_events())
        return concat_chunks(
            iter_row_chunks(rows, EVENT_COLUMNS, _EVENT_DTYPES), EVENT_COLUMNS, _EVENT_DTYPES
        )

    def __getitem__(self, i):
        index = self._ion_index
        if i < 0:
            i += len(index) - 1
        if not 0 <= i < len(index) - 1:
            raise IndexError("ion index out of range")
        start = index[i]

        end = index[i + 1]

        with open(self.filename, "rb") as f:
            f.seek(start)
            # We assume that one ion block will fit in RAM
            ion_str = f.read(end - start)
            return self._read_ion(ion_str.decode(ENCODING))

    def __len__(self):
        return len(self._ion_index) - 1


def buffered_findall(filename, string, start=0):
    """A method of reading a file in buffered pieces (needed for HUGE files)"""
    with open(filename, "rb") as f:
        filesize = os.path.getsize(filename)
        BUFFERSIZE = 4096
        overlap = len(string) - 1
        buffer = None
        positions = []

        if start > 0:
            f.seek(start)

        while True:
            if f.tell() >= overlap and f.tell() < filesize:
                f.seek(f.tell() - overlap)
            buffer = f.read(BUFFERSIZE)
            if buffer:
                buffer_positions = [m.start() for m in re.finditer(string, buffer)]

                for position in buffer_positions:
                    if position >= 0:
                        positions.append(f.tell() - len(buffer) + position)
            else:
                return positions
