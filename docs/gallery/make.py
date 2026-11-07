"""Build the pictures of the documentation gallery.

Every ``docs/gallery/<name>.py`` (except this file and names starting with ``_``) is an
entry: it has ``TITLE``, ``NEEDS_SRIM``, ``OUTPUT`` and ``make(outdir)``. See README.md.

Usage::

    python docs/gallery/make.py [--list] [--out DIR] [name ...]
"""

import argparse
import importlib.util
import sys
from pathlib import Path

import matplotlib

matplotlib.use("Agg")

from srim.paths import srim_dir  # noqa: E402

HERE = Path(__file__).resolve().parent
DEFAULT_OUT = HERE / "images"


def entries():
    """Return the sorted names of the gallery entries."""
    return sorted(
        p.stem for p in HERE.glob("*.py") if p.name != "make.py" and not p.name.startswith("_")
    )


def load(name):
    """Import the entry ``name`` from its file and return the module."""
    path = HERE / f"{name}.py"
    spec = importlib.util.spec_from_file_location(f"pysrim2_gallery_{name}", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def srim_available():
    """Return True if the SRIM install has ``TRIM.exe``."""
    return (srim_dir() / "TRIM.exe").is_file()


def main(argv=None):
    parser = argparse.ArgumentParser(description="Build the documentation gallery pictures.")
    parser.add_argument("--list", action="store_true", help="list the entries and exit")
    parser.add_argument(
        "--out", type=Path, default=DEFAULT_OUT, help="directory for the pictures (default: %(default)s)"
    )
    parser.add_argument("names", nargs="*", metavar="name", help="entries to build (default: all)")
    args = parser.parse_args(argv)

    known = entries()
    if args.list:
        for name in known:
            module = load(name)
            if module.NEEDS_SRIM:
                print(f"{name}  [SRIM]")
            else:
                print(f"{name}  {module.TITLE}")
        return 0

    unknown = [name for name in args.names if name not in known]
    if unknown:
        print(f"unknown entry: {', '.join(unknown)} (entries: {', '.join(known)})", file=sys.stderr)
        return 2

    failed = False
    for name in args.names or known:
        try:
            module = load(name)
            if module.NEEDS_SRIM and not srim_available():
                print(f"skip {name}: needs SRIM ({srim_dir() / 'TRIM.exe'} is missing)")
                continue
            args.out.mkdir(parents=True, exist_ok=True)
            path = module.make(args.out)
        except Exception as exc:
            print(f"FAILED {name}: {exc}")
            failed = True
            continue
        print(f"{name}: {path}")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
