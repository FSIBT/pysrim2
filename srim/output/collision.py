"""Parser for the SRIM COLLISON.txt file"""

import os
import re

import numpy as np

from ._common import (
    _to_float,
    double_regex,
    int_regex,
)


class Collision:
    """Reads the SRIM Collisions file.

    This is the most important file in my opinion. It records every
    single collision and its energies. The file will get huge for
    simulations with many collisions. Since the file can be larger
    than the amount of RAM it will read the file in sections
    (buffers).

    Parameters
    ----------
    directory : :obj:`str`
         directory of calculation
    filename : :obj:`str`, optional
         filename for Collisions. Default ``COLLISON.txt``

    """

    def __init__(self, directory, filename="COLLISON.txt"):
        self.filename = os.path.join(directory, filename)

        with open(self.filename, encoding="latin-1") as f:
            self._read_header(f)

        self._ion_index = buffered_findall(self.filename, b"  Ion    Energy")

    def _read_header(self, f):
        """Read Header of COLLISON.txt

        Currently we do nothing with the header
        """

        # Collect the header of the file
        header = []

        for line in f:
            if line == " \n":
                break
            header.append(line)
        return header

    def _read_ion(self, ion_str):
        """There are 2 types of files with and without cascades

        format:
           1 - Kinchin-Pease Theory (No full cascades)
           2 - full cascades
        """
        # Notice that lines is an generator!
        # This makes it so we can walk through lines
        # in multiple for loops
        lines = (line for line in ion_str.split("\n"))

        # Skip Ion Header
        for line in lines:
            if re.match(r"^-+\r$", line):
                break

        collisions = []

        # Reads collisions for an ion
        for line in lines:
            if re.match(r"^=+\r$", line):
                break

            tokens = line.split(chr(179))[1:-1]

            # Check if a full_cascades simulation
            # Read Cascade information
            if re.match(r"\s+<== Start of New Cascade\s+", tokens[-1]):
                (target_disp, target_vac, target_replac, target_inter, cascade) = (
                    self._read_cascade(lines)
                )
            else:
                target_disp = _to_float(tokens[8])
                target_vac = 0
                target_replac = 0
                target_inter = 0
                cascade = None

            collisions.append(
                {
                    "ion_number": int(tokens[0]),
                    "kinetic_energy": _to_float(tokens[1]),
                    "depth": _to_float(tokens[2]),
                    "lat_y_dist": _to_float(tokens[3]),
                    "lat_z_dist": _to_float(tokens[4]),
                    "stopping_energy": _to_float(tokens[5]),
                    "atom": re.search("([A-Z][a-z]?)", tokens[6]).group(1),
                    "recoil_energy": _to_float(tokens[7]),
                    "target_disp": target_disp,
                    "target_vac": target_vac,
                    "target_replac": target_replac,
                    "target_inter": target_inter,
                    "cascade": cascade,
                }
            )

            # Handles weird case where no summary of cascade
            if target_disp is None:
                break

        # Reads ion footer
        ion_number = re.search(int_regex, next(lines)).group(0)

        footer = ""
        for line in lines:
            if re.match(r"^=+\r$", line):
                break
            footer += line

        matches = re.findall(double_regex, footer)

        line = next(lines)

        return {
            "ion_number": int(ion_number),
            "displacements": _to_float(matches[0]),
            "avg_displacements": _to_float(matches[1]),
            "replacements": _to_float(matches[2]),
            "avg_replacements": _to_float(matches[3]),
            "vacancies": _to_float(matches[4]),
            "avg_vacancies": _to_float(matches[5]),
            "interstitials": _to_float(matches[6]),
            "avg_interstitials": _to_float(matches[7]),
            "sputtered_atoms": _to_float(matches[8]),
            "avg_sputtered_atoms": _to_float(matches[9]),
            "transmitted_atoms": _to_float(matches[10]),
            "avg_transmitted_atoms": _to_float(matches[11]),
            "collisions": collisions,
        }

    def _read_cascade(self, lines):
        line = next(lines)

        assert re.match(r"^=+\r$", line)

        line = next(lines)
        assert re.match(
            (
                r"  Recoil Atom Energy\(eV\)   X \(A\)      Y \(A\)      Z \(A\)"
                r"   Vac Repl Ion Numb \d+="
            ),
            line,
        )

        cascade = []
        for line in lines:
            if re.match(r"^=+\r$", line):
                break
            tokens = line.split()[1:-1]

            cascade.append(
                {
                    "recoil": int(tokens[0]),
                    "atom": int(tokens[1]),
                    "recoil_energy": _to_float(tokens[2]),
                    "position": np.array(
                        [_to_float(tokens[3]), _to_float(tokens[4]), _to_float(tokens[5])]
                    ),
                    "vac": int(tokens[6]),
                    "repl": int(tokens[7]),
                }
            )

        if line.count("=") > 100:
            return None, None, None, None, cascade

        line = next(lines)
        tokens = line.split(chr(179))[1:-1]

        if tokens:
            target_disp = _to_float(tokens[2])
            target_vac = _to_float(tokens[3])
            target_replac = _to_float(tokens[4])
            target_inter = _to_float(tokens[5])
        else:
            target_disp = None
            target_vac = None
            target_replac = None
            target_inter = None

        return target_disp, target_vac, target_replac, target_inter, cascade

    def __getitem__(self, i):
        start = self._ion_index[i]

        if i == len(self._ion_index):
            end = os.path.getsize(self.filename)
        else:
            end = self._ion_index[i + 1]

        with open(self.filename, "rb") as f:
            f.seek(start)
            # We assume that ion_str will fit in RAM
            ion_str = f.read(end - start)
            return self._read_ion(ion_str.decode("latin-1"))

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
