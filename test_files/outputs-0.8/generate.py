"""Generate the event-output fixtures in test_files/outputs-0.8/ with real SRIM.

Run from the repository root inside the pysrim2 container (see README.md):

    python test_files/outputs-0.8/generate.py
"""

import shutil
from pathlib import Path

from srim import TRIM, Ion, Layer, Target
from srim.paths import srim_dir

HERE = Path(__file__).resolve().parent
EVENT_FILES = ("EXYZ.txt", "BACKSCAT.txt", "TRANSMIT.txt", "SPUTTER.txt", "RANGE.txt", "TRIM.IN")

RUNS = {
    # He 50 keV into 200 nm Si, EXYZ written every 1000 eV
    "exyz": dict(
        ion=Ion("He", energy=50e3),
        layers=[Layer.from_formula("Si", density=2.33, width=2000.0)],
        number_ions=10,
        calculation=1,
        settings=dict(exyz=1000),
    ),
    # H 15 keV into 100 nm Au: about a third of the ions are transmitted, some are backscattered
    "thin": dict(
        ion=Ion("H", energy=15e3),
        layers=[Layer.from_formula("Au", density=19.3, width=1000.0)],
        number_ions=60,
        calculation=1,
        settings=dict(backscattered=1, transmit=1),
    ),
    # Ar 2 keV into Cu with full cascades: sputtered atoms
    "sputter": dict(
        ion=Ion("Ar", energy=2e3),
        layers=[Layer.from_formula("Cu", density=8.92, width=500.0)],
        number_ions=20,
        calculation=2,
        settings=dict(sputtered=1),
    ),
}


def main():
    sdir = Path(srim_dir())
    for name, spec in RUNS.items():
        for folder in (sdir, sdir / "SRIM Outputs"):  # TRIM does not remove stale EXYZ.txt
            for fname in EVENT_FILES:
                (folder / fname).unlink(missing_ok=True)
        trim = TRIM(
            Target(spec["layers"]),
            spec["ion"],
            number_ions=spec["number_ions"],
            calculation=spec["calculation"],
            random_seed=12345,
            **spec["settings"],
        )
        trim.run(str(sdir))
        out = HERE / name
        out.mkdir(exist_ok=True)
        for fname in EVENT_FILES:
            for folder in (sdir, sdir / "SRIM Outputs"):
                if (folder / fname).is_file():
                    shutil.copy(folder / fname, out / fname)
                    break
        print(name, sorted(p.name for p in out.iterdir()))


if __name__ == "__main__":
    main()
