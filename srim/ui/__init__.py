"""Views of a scan run: event consumers for :func:`srim.scan.run_scan`'s ``on_event``.

A consumer is any callable taking one event (see :mod:`srim.scan.events`). They run in the
calling thread of the scan, so they must be quick:

- :class:`~srim.ui.plain.PlainProgress`: one status line when the state changes, at most
  one every few seconds; plain text, no escape codes (works over SSH, in tmux and in logs).
- :class:`~srim.ui.jsonl.JsonLines`: every event as one line of JSON.
"""

from .jsonl import JsonLines
from .plain import PlainProgress

__all__ = ["JsonLines", "PlainProgress"]
