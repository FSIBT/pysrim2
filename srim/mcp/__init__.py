"""The MCP server of ``pysrim2 mcp``: lets an AI agent set up and run pysrim2 runs.

It needs the ``mcp`` extra (the official MCP Python SDK). Only ``srim.mcp.server`` imports the
SDK, and it is imported only once :func:`mcp_available` says the SDK is there, so the library
and the command line work without it.
"""

import importlib.util

INSTALL_HINT = (
    'pysrim2 mcp needs the MCP extra: uv tool install "pysrim2[mcp]" '
    '(or pip install "pysrim2[mcp]")'
)


def mcp_available() -> bool:
    """True when the MCP SDK can be imported (it is found, not imported)."""
    return importlib.util.find_spec("mcp") is not None
