"""Parser for the SRIM COLLISON.txt file"""

import os
import re

import numpy as np

from ._common import SRIMOutputParseError, _to_float, double_regex

#: SRIM writes ``COLLISON.txt`` in code page 437; its box-drawing characters separate columns
ENCODING = "cp437"
#: column separator of ion collision and cascade summary lines (cp437 byte 179)
_SEP = "│"
#: column separator of cascade recoil lines (cp437 byte 219)
_RECOIL_SEP = "█"
_SYMBOL = re.compile(r"[A-Z][a-z]?")
_NUMBER = re.compile(double_regex)


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


def _records(lines):
    """Walk the lines of COLLISON.txt, yielding ``("ion", collision, recoils, summary)``
    for every ion collision (with the recoils of its cascade and the cascade summary line,
    ``None`` if SRIM wrote none) and ``("footer", dict)`` for every ion footer.

    Lines are consumed one at a time; only the cascade of one collision is held.
    """
    collision = None
    recoils = []
    summary = None
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
                summary = [_to_float(t) for t in tokens[1:6]] if len(tokens) >= 6 else None
                continue
            if collision is not None:
                yield pending()
            collision, recoils, summary = _parse_collision(tokens), [], None
        elif first == _RECOIL_SEP:
            # SRIM sometimes leaves a stray recoil line (recoil number 0) between two ions:
            # it belongs to no collision and is skipped
            if collision is not None:
                recoils.append(_parse_recoil(line))
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


class Collision:
    """Reads the SRIM Collisions file.

    This is the most important file in my opinion. It records every
    single collision and its energies. The file will get huge for
    simulations with many collisions, so ``collision[i]`` reads only the block
    of ion ``i``.

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

    def __getitem__(self, i):
        index = self._ion_index
        if i < 0:
            i += len(index) - 1
        if not 0 <= i < len(index) - 1:
            raise IndexError("ion index out of range")
        start = index[i]

        if i == len(index) - 1:
            end = os.path.getsize(self.filename)
        else:
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
