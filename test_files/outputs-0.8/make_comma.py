"""Derive comma-decimal variants of the event files (numeric tokens only; bytes and CRLF kept).

Run from the repository root: python test_files/outputs-0.8/make_comma.py
"""

import re
from pathlib import Path

HERE = Path(__file__).resolve().parent
FILES = {
    "exyz": "EXYZ.txt",
    "thin": "BACKSCAT.txt TRANSMIT.txt",
    "sputter": "SPUTTER.txt",
}
# a decimal number standing alone between whitespace (or "=") and whitespace/end of line
NUMBER = re.compile(rb"(?<=[\s=])([-+]?\d*\.\d*(?:[eE][-+]?\d+)?)(?=\s|$)", re.MULTILINE)


def main():
    for folder, names in FILES.items():
        for name in names.split():
            data = (HERE / folder / name).read_bytes()
            out = HERE / folder / "comma"
            out.mkdir(exist_ok=True)
            (out / name).write_bytes(NUMBER.sub(lambda m: m.group(1).replace(b".", b","), data))


if __name__ == "__main__":
    main()
