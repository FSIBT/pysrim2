"""The live terminal views of a scan (``pysrim2 scan run --ui plot|dashboard``).

:mod:`~srim.ui.tui.state` and :mod:`~srim.ui.tui.plot` are pure Python and import nothing
from Textual. :mod:`~srim.ui.tui.app` needs the optional ``tui`` extra and is imported only
when a view runs.
"""

from .state import ScanState

__all__ = ["ScanState"]
