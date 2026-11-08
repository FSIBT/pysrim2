import subprocess
import sys


def test_the_library_and_cli_never_import_qt():
    # A fresh interpreter, so earlier tests cannot have imported Qt.
    code = (
        "import sys, srim, srim.cli, srim.app, srim.installer.app\n"
        "from srim import cli\n"
        "cli.build_parser().parse_args(['doctor'])\n"
        "assert not [m for m in sys.modules if m.startswith('PySide6')], 'Qt imported'\n"
    )
    subprocess.run([sys.executable, "-c", code], check=True, timeout=120)
