"""The notebook server process: start, readiness, errors, stop and the token (Qt-free).

The tests run a fake server (``fake_notebook_server.py``) in place of marimo or Jupyter, so no
notebook tool is started. The command lines are checked against the real tools' flags.
"""

import os
import subprocess
import sys
import time
from pathlib import Path

import pytest

from srim.app import notebooks
from srim.app.notebooks import (
    NotebookServer,
    NotebookServerError,
    notebook_url,
    server_command,
)
from srim.notebook import NOTEBOOK_DIR

FAKE = Path(__file__).with_name("fake_notebook_server.py")


def fake(mode: str):
    """A ``command_for`` that runs the fake server in ``mode`` (serve gets the port)."""

    def command_for(kind, path, port, token_file):
        extra = [str(port)] if mode == "serve" else []
        return [sys.executable, str(FAKE), mode, *extra]

    return command_for


def test_a_server_that_answers_is_ready_and_stops(tmp_path):
    server = NotebookServer("marimo", tmp_path / "notebook.py", command_for=fake("serve"))
    server.start()
    try:
        server.wait_ready(timeout=30)
        assert server.running
    finally:
        server.stop()
    assert not server.running
    assert server.process.returncode is not None


def test_a_server_that_exits_is_reported_with_its_log_tail(tmp_path):
    server = NotebookServer("marimo", tmp_path / "notebook.py", command_for=fake("exit"))
    server.start()
    started = time.monotonic()
    with pytest.raises(NotebookServerError) as info:
        server.wait_ready(timeout=30)
    assert time.monotonic() - started < 5
    message = str(info.value)
    assert "line 30" in message
    assert "line 5" not in message
    assert "exited with code 1" in message
    assert not server.running


def test_a_server_that_never_answers_times_out(tmp_path):
    server = NotebookServer("marimo", tmp_path / "notebook.py", command_for=fake("silent"))
    server.start()
    started = time.monotonic()
    with pytest.raises(NotebookServerError, match="did not answer"):
        server.wait_ready(timeout=1)
    assert time.monotonic() - started < 3
    assert not server.running
    assert server.process.returncode is not None


@pytest.mark.parametrize("kind", ["marimo", "jupyter"])
def test_the_token_is_not_on_the_command_line_and_its_file_is_private(tmp_path, monkeypatch, kind):
    seen = {}
    real_popen = subprocess.Popen

    class Spy(real_popen):
        def __init__(self, args, **kwargs):
            seen["args"] = list(args)
            seen["env"] = kwargs.get("env")
            super().__init__(args, **kwargs)

    monkeypatch.setattr(notebooks.subprocess, "Popen", Spy)
    server = NotebookServer(kind, tmp_path / "run" / "notebook", command_for=fake("silent"))
    server.start()
    try:
        assert server.token
        assert server.token not in " ".join(seen["args"])
        assert server.token_file.read_text(encoding="utf-8") == server.token
        assert server.token in server.url
        if kind == "jupyter":
            assert seen["env"]["JUPYTER_TOKEN"] == server.token
        else:
            assert seen["env"] is None
        if os.name == "posix":
            assert server.token_file.stat().st_mode & 0o777 == 0o600
    finally:
        server.stop()
    assert not server.token_file.exists()


def test_commands_and_urls_handle_spaces_and_non_ascii(tmp_path):
    folder = tmp_path / "run ä 1"
    marimo_path = folder / "notebook ä 1.py"
    command = server_command("marimo", marimo_path, 8123, folder / NOTEBOOK_DIR / "token")
    assert command[command.index("edit") + 1] == str(marimo_path)

    jupyter_path = folder / "notebook ä 1.ipynb"
    command = server_command("jupyter", jupyter_path, 8123, folder / NOTEBOOK_DIR / "token")
    assert f"--ServerApp.root_dir={folder}" in command
    url = notebook_url("jupyter", jupyter_path, 8123, "tok")
    assert url == "http://127.0.0.1:8123/lab/tree/notebook%20%C3%A4%201.ipynb?token=tok"


def test_server_command_matches_the_flags(tmp_path):
    path = tmp_path / "notebook.py"
    token_file = tmp_path / "token"
    assert server_command("marimo", path, 8123, token_file) == [
        sys.executable,
        "-m",
        "marimo",
        "edit",
        str(path),
        "--headless",
        "--host",
        "127.0.0.1",
        "--port",
        "8123",
        "--token-password-file",
        str(token_file),
        "--skip-update-check",
    ]
    assert server_command("jupyter", tmp_path / "notebook.ipynb", 8123, token_file) == [
        sys.executable,
        "-m",
        "jupyterlab",
        "--no-browser",
        "--ip=127.0.0.1",
        "--port=8123",
        "--ServerApp.port_retries=0",
        f"--ServerApp.root_dir={tmp_path}",
    ]
    assert notebook_url("marimo", path, 8123, "tok") == "http://127.0.0.1:8123/?access_token=tok"


def test_the_log_is_private_and_loses_the_token_when_stopped(tmp_path):
    def command_for(kind, path, port, token_file):
        return [sys.executable, str(FAKE), "echo", str(token_file)]

    server = NotebookServer("marimo", tmp_path / "notebook.py", command_for=command_for)
    server.start()
    server.process.wait(timeout=30)
    assert server.token in server.log_path.read_text(encoding="utf-8")
    assert server.token not in server.log_tail()
    assert "access_token=<token>" in server.log_tail()
    server.stop()
    text = server.log_path.read_text(encoding="utf-8")
    assert server.token not in text
    assert "access_token=<token>" in text
    if os.name == "posix":
        assert server.log_path.stat().st_mode & 0o777 == 0o600


def test_stop_twice_is_harmless(tmp_path):
    server = NotebookServer("marimo", tmp_path / "notebook.py", command_for=fake("serve"))
    server.stop()  # never started
    server.start()
    server.wait_ready(timeout=30)
    server.stop()
    server.stop()
    assert not server.running
