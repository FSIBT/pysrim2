"""Config snippets that connect an AI agent to ``pysrim2 mcp``.

Qt-free and SDK-free: ``pysrim2 mcp --print-config CLIENT`` and the app's Connect dialog both use
it. Every snippet starts the same command, the absolute path of the installed ``pysrim2``
executable (or ``python -m srim`` when the executable is not on the PATH) with the ``mcp``
argument.

Formats, checked against the clients' documentation:

- Claude Code: ``claude mcp add --scope user pysrim2 -- <command>`` (the ``--`` separates the
  server's command from the options of ``claude``).
- Codex: ``[mcp_servers.pysrim2]`` in ``~/.codex/config.toml`` with ``command`` and ``args``.
- Gemini CLI: ``mcpServers`` in ``~/.gemini/settings.json``.
- Cursor: ``mcpServers`` in ``~/.cursor/mcp.json``.
- VS Code: ``servers`` in ``.vscode/mcp.json``, each with ``"type": "stdio"``.
"""

import json
import os
import shlex
import shutil
import subprocess
import sys

#: the clients ``--print-config`` knows; ``generic`` is a plain command for any other client
CLIENTS = ("claude", "codex", "gemini", "cursor", "vscode", "generic")

CLIENT_LABELS = {
    "claude": "Claude Code",
    "codex": "Codex",
    "gemini": "Gemini CLI",
    "cursor": "Cursor",
    "vscode": "VS Code",
    "generic": "Any other client (stdio)",
}

#: the name the server has in every client's list of servers
SERVER_NAME = "pysrim2"

_LOCATIONS = {
    "claude": "run in a terminal",
    "codex": "~/.codex/config.toml",
    "gemini": "~/.gemini/settings.json",
    "cursor": "~/.cursor/mcp.json",
    "vscode": ".vscode/mcp.json",
    "generic": "the configuration of your client, where it starts a stdio server",
}


def _is_windows() -> bool:
    return sys.platform == "win32"


def pysrim2_command() -> list[str]:
    """The command that starts the server: the ``pysrim2`` executable and ``mcp``.

    The executable's absolute path when it is on the PATH, else the module of the running
    interpreter (``python -m srim mcp``), so the snippet starts the same install as this one.
    """
    exe = shutil.which("pysrim2")
    if exe:
        return [os.path.abspath(exe), "mcp"]
    return [sys.executable, "-m", "srim", "mcp"]


def snippet_location(client: str) -> str:
    """Where the snippet goes: a file, or ``run in a terminal``."""
    return _LOCATIONS[_check(client)]


def snippet(client: str, command: list[str]) -> str:
    """The config text for ``client`` that starts ``command`` (``command[0]`` and its args)."""
    _check(client)
    if client == "claude":
        return _claude_line(command)
    if client == "codex":
        return _codex_toml(command)
    if client == "gemini":
        return _json_text({"mcpServers": {SERVER_NAME: _entry(command)}})
    if client == "cursor":
        return _json_text({"mcpServers": {SERVER_NAME: _entry(command)}})
    if client == "vscode":
        entry = {"type": "stdio", **_entry(command)}
        return _json_text({"servers": {SERVER_NAME: entry}})
    return _generic_lines(command)


def _check(client: str) -> str:
    if client not in CLIENTS:
        raise ValueError(f"unknown client {client!r}; choose one of {', '.join(CLIENTS)}")
    return client


def _entry(command: list[str]) -> dict:
    return {"command": command[0], "args": list(command[1:])}


def _json_text(data: dict) -> str:
    return json.dumps(data, indent=2)


def _claude_line(command: list[str]) -> str:
    words = ["claude", "mcp", "add", "--scope", "user", SERVER_NAME, "--", *command]
    if _is_windows():
        # cmd.exe and CreateProcess quote words this way; shlex would mangle the backslashes
        return subprocess.list2cmdline(words)
    return shlex.join(words)


def _toml_string(text: str) -> str:
    """A TOML basic string: backslash, quote and control characters escaped."""
    out = ['"']
    for char in text:
        if char == "\\":
            out.append("\\\\")
        elif char == '"':
            out.append('\\"')
        elif char == "\n":
            out.append("\\n")
        elif char == "\t":
            out.append("\\t")
        elif ord(char) < 0x20 or ord(char) == 0x7F:
            out.append(f"\\u{ord(char):04X}")
        else:
            out.append(char)
    out.append('"')
    return "".join(out)


def _codex_toml(command: list[str]) -> str:
    args = ", ".join(_toml_string(arg) for arg in command[1:])
    return f"[mcp_servers.{SERVER_NAME}]\ncommand = {_toml_string(command[0])}\nargs = [{args}]\n"


def _generic_lines(command: list[str]) -> str:
    lines = [f"command: {command[0]}", "args:"]
    lines += [f"  - {arg}" for arg in command[1:]]
    return "\n".join(lines) + "\n"
