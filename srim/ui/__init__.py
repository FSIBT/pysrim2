"""Views of a scan run: event consumers for :func:`srim.scan.run_scan`'s ``on_event``.

A consumer is any callable taking one event (see :mod:`srim.scan.events`). Those that run in
the scan's thread (the terminal ones) must be quick:

- :class:`~srim.ui.plain.PlainProgress`: one status line when the state changes, at most
  one every few seconds; plain text, no escape codes (works over SSH, in tmux and in logs).
- :class:`~srim.ui.jsonl.JsonLines`: every event as one line of JSON.
- :class:`~srim.ui.notebook.LiveView`: the status and the plot of a scan in a Jupyter cell,
  updated at most every second (needs IPython and matplotlib).

The live views of the terminal and the browser (``pysrim2[tui]``) are in :mod:`srim.ui.tui`
and :mod:`srim.ui.web`.
"""

from .jsonl import JsonLines
from .notebook import LiveView
from .plain import PlainProgress

__all__ = ["JsonLines", "LiveView", "PlainProgress"]
