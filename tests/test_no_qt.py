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
        "import srim.app.quantity\n"
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
