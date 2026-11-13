import os
import subprocess
import sys


def test_the_library_and_cli_never_import_qt(tmp_path):
    # A fresh interpreter, so earlier tests cannot have imported Qt. The commands run with a
    # temporary home and data directory, and their output is discarded; only the imports matter.
    env = {
        **os.environ,
        "HOME": str(tmp_path / "home"),
        "PYSRIM2_HOME": str(tmp_path / "data"),
    }
    code = (
        "import contextlib, os, sys\n"
        "import srim, srim.cli, srim.app, srim.installer.app\n"
        "import srim.app.routes, srim.app.settings, srim.app.guidance, srim.app.model\n"
        "import srim.app.materials, srim.app.elements, srim.app.numbers, srim.app.drawing\n"
        "import srim.app.quantity, srim.app.scan_model, srim.app.runs, srim.app.plots\n"
        "import srim.app.scan_plots, srim.app.export_results, srim.app.recent\n"
        "import srim.notebook\n"
        # pandas imports pyarrow itself when it is installed; the export extras are not ours to load
        "heavy = [m for m in sys.modules if m in ('tables', 'pyarrow.parquet')]\n"
        "assert not heavy, heavy  # the export extras are imported only when a format is written\n"
        "import srim.core.isotopes, srim.core.compounds\n"
        "from srim import cli\n"
        "cli.build_parser().parse_args(['doctor'])\n"
        "with open(os.devnull, 'w') as sink, contextlib.redirect_stdout(sink), "
        "contextlib.redirect_stderr(sink):\n"
        "    for argv in (['install', '--dry-run', 'app'], ['doctor']):\n"
        "        try:\n"
        "            cli.main(argv)\n"
        "        except SystemExit:\n"
        "            pass  # the exit code is not what this test checks\n"
        "assert not [m for m in sys.modules if m.startswith('PySide6')], 'Qt imported'\n"
    )
    subprocess.run([sys.executable, "-c", code], check=True, timeout=300, env=env)


def test_the_plot_catalogue_does_not_import_matplotlib():
    # A fresh interpreter: the catalogue is Qt-free, and matplotlib is only for the drawing
    # functions of the GUI and the tests, which import it themselves.
    code = (
        "import sys\n"
        "import srim.app.plots\n"
        "loaded = [m for m in sys.modules if m == 'matplotlib' or m.startswith('matplotlib.')]\n"
        "assert not loaded, loaded\n"
    )
    subprocess.run([sys.executable, "-c", code], check=True, timeout=300)
