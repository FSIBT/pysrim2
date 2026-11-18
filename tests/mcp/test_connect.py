"""The connect snippets: each client's config, parsed back, holds the same command.

The snippets are plain text, so the tests parse them with the parsers of their formats (json,
tomllib, shlex) and check that a path with spaces and backslashes comes back unchanged.
"""

import json
import shlex
import subprocess
import sys
import tomllib

import pytest

from srim import cli
from srim.mcp import connect

WINDOWS_EXE = "C:\\Program Files\\Some Dir\\pysrim2.exe"
PLAIN_EXE = "/opt/my tools/bin/pysrim2"


def command_for(exe: str) -> list[str]:
    return [exe, "mcp"]


@pytest.mark.parametrize("client", ["gemini", "cursor"])
def test_the_json_clients_hold_the_command(client):
    parsed = json.loads(connect.snippet(client, command_for(WINDOWS_EXE)))
    server = parsed["mcpServers"]["pysrim2"]
    assert server["command"] == WINDOWS_EXE
    assert server["args"] == ["mcp"]


def test_vscode_uses_servers_and_the_stdio_type():
    parsed = json.loads(connect.snippet("vscode", command_for(WINDOWS_EXE)))
    server = parsed["servers"]["pysrim2"]
    assert server == {"type": "stdio", "command": WINDOWS_EXE, "args": ["mcp"]}


def test_codex_is_toml_with_the_table_name():
    text = connect.snippet("codex", command_for(WINDOWS_EXE))
    assert "[mcp_servers.pysrim2]" in text
    parsed = tomllib.loads(text)
    assert parsed["mcp_servers"]["pysrim2"] == {"command": WINDOWS_EXE, "args": ["mcp"]}


def test_claude_line_round_trips_on_posix(monkeypatch):
    monkeypatch.setattr(connect, "_is_windows", lambda: False)
    line = connect.snippet("claude", command_for(PLAIN_EXE))
    words = shlex.split(line)
    assert words[:6] == ["claude", "mcp", "add", "--scope", "user", "pysrim2"]
    assert words[6:] == ["--", PLAIN_EXE, "mcp"]


def test_claude_line_uses_list2cmdline_on_windows(monkeypatch):
    monkeypatch.setattr(connect, "_is_windows", lambda: True)
    line = connect.snippet("claude", command_for(WINDOWS_EXE))
    expected = subprocess.list2cmdline(
        ["claude", "mcp", "add", "--scope", "user", "pysrim2", "--", WINDOWS_EXE, "mcp"]
    )
    assert line == expected
    assert f'"{WINDOWS_EXE}"' in line  # the path with spaces is quoted, backslashes kept


def test_a_windows_path_round_trips_in_every_format(monkeypatch):
    monkeypatch.setattr(connect, "_is_windows", lambda: True)
    assert (
        json.loads(connect.snippet("gemini", command_for(WINDOWS_EXE)))["mcpServers"]["pysrim2"][
            "command"
        ]
        == WINDOWS_EXE
    )
    assert (
        tomllib.loads(connect.snippet("codex", command_for(WINDOWS_EXE)))["mcp_servers"]["pysrim2"][
            "command"
        ]
        == WINDOWS_EXE
    )
    generic = connect.snippet("generic", command_for(WINDOWS_EXE))
    assert f"command: {WINDOWS_EXE}" in generic.splitlines()


def test_quotes_and_control_characters_in_a_path_are_escaped():
    odd = 'C:\\tmp\\say "hi"\tnow'
    text = connect.snippet("codex", command_for(odd))
    assert tomllib.loads(text)["mcp_servers"]["pysrim2"]["command"] == odd


def test_generic_lists_the_command_and_the_args_as_plain_lines():
    text = connect.snippet("generic", command_for(PLAIN_EXE))
    assert text.splitlines() == [
        f"command: {PLAIN_EXE}",
        "args:",
        "  - mcp",
    ]


def test_every_client_has_a_location_and_a_label():
    for client in connect.CLIENTS:
        assert connect.CLIENT_LABELS[client]
        assert connect.snippet_location(client)
    assert connect.snippet_location("codex") == "~/.codex/config.toml"
    assert connect.snippet_location("gemini") == "~/.gemini/settings.json"
    assert connect.snippet_location("cursor") == "~/.cursor/mcp.json"
    assert connect.snippet_location("vscode") == ".vscode/mcp.json"
    assert connect.snippet_location("claude") == "run in a terminal"


def test_an_unknown_client_is_rejected_by_the_snippet():
    with pytest.raises(ValueError):
        connect.snippet("emacs", command_for(PLAIN_EXE))


def test_pysrim2_command_uses_the_installed_executable(monkeypatch):
    monkeypatch.setattr(connect.shutil, "which", lambda name: "/opt/x/bin/pysrim2")
    assert connect.pysrim2_command() == ["/opt/x/bin/pysrim2", "mcp"]


def test_pysrim2_command_falls_back_to_the_module(monkeypatch):
    monkeypatch.setattr(connect.shutil, "which", lambda name: None)
    assert connect.pysrim2_command() == [sys.executable, "-m", "srim", "mcp"]


def test_the_module_command_starts_the_mcp_command(monkeypatch):
    # `python -m srim mcp` reaches the mcp subcommand: without the SDK it says how to install it
    # and exits 2, which it can only do after the dispatch.
    monkeypatch.setattr(connect.shutil, "which", lambda name: None)
    code = (
        "import sys\n"
        "sys.modules['mcp'] = None\n"
        "sys.argv = ['srim', 'mcp']\n"
        "import runpy\n"
        "runpy.run_module('srim', run_name='__main__')\n"
    )
    result = subprocess.run(
        [sys.executable, "-c", code], capture_output=True, text=True, timeout=120
    )
    assert result.returncode == 2
    assert "pysrim2[mcp]" in result.stderr


def test_print_config_prints_the_snippet(capsys):
    assert cli.main(["mcp", "--print-config", "codex"]) == 0
    captured = capsys.readouterr()
    assert "[mcp_servers.pysrim2]" in captured.out
    assert "~/.codex/config.toml" in captured.err


def test_print_config_output_is_only_the_snippet(capsys):
    # stdout is the snippet alone, so `... > ~/.gemini/settings.json` gives a valid file
    assert cli.main(["mcp", "--print-config", "gemini"]) == 0
    json.loads(capsys.readouterr().out)


def test_print_config_rejects_an_unknown_client(capsys):
    with pytest.raises(SystemExit) as exit_info:
        cli.main(["mcp", "--print-config", "emacs"])
    assert exit_info.value.code == 2
    assert "invalid choice" in capsys.readouterr().err


def test_print_config_works_without_the_sdk_and_does_not_start_the_server():
    code = (
        "import sys\n"
        "sys.modules['mcp'] = None\n"
        "from srim import cli\n"
        "assert cli.main(['mcp', '--print-config', 'vscode']) == 0\n"
        "assert 'srim.mcp.server' not in sys.modules, 'server imported'\n"
    )
    result = subprocess.run(
        [sys.executable, "-c", code], capture_output=True, text=True, timeout=120
    )
    assert result.returncode == 0, result.stderr
    assert '"servers"' in result.stdout
