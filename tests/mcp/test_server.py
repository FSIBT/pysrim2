"""The MCP server, through the SDK's own client: the tools are listed and run, errors come back as
tool errors, the ask-first questions go through the client's elicitation (or the code flow), the
client's name reaches the activity log, the plot returns a picture, and the stdio server keeps
its standard output for the protocol.

Runs use the fake runner of ``tests/app/runners.py`` (no SRIM, no network). The in-memory client
connects the server in this process; the stdio test starts ``python -m srim.mcp.server``.
"""

import asyncio
import base64
import json
import os
import re
import subprocess
import sys
import time
from pathlib import Path

import anyio
import pytest

pytest.importorskip("mcp")  # the MCP SDK is an extra: the file is skipped without it

from mcp import Client  # noqa: E402
from mcp.client.stdio import StdioServerParameters  # noqa: E402
from mcp.types import ElicitResult, ErrorData, Implementation  # noqa: E402
from test_headless_status import fake_checks, n15_backend  # noqa: E402
from test_results import run_folder  # noqa: E402

from srim.mcp import server  # noqa: E402
from srim.mcp.activity import ActivityLog, activity_path  # noqa: E402
from srim.mcp.headless import HeadlessBackend  # noqa: E402

READY = fake_checks({"name": "trim-exe", "ok": True, "detail": "ok", "fix": ""})
#: a run of about two hours by the library's stand-in rate: over the 30 minute limit
LONG = ["number_ions=100000000"]
TIMEOUT = 60.0
#: a stdio exchange that takes longer than this has hung
STDIO_SECONDS = 60.0
CODE = re.compile(r'confirm_code="([2346789ABCDEFGHJKMNPQRTUVWXYZ]{6})"')


@pytest.fixture(autouse=True)
def home(tmp_path, monkeypatch):
    """Every test has its own data directory (the activity log lives there)."""
    monkeypatch.setenv("PYSRIM2_HOME", str(tmp_path / "home"))
    return tmp_path


@pytest.fixture
def fast(monkeypatch):
    """Runs that finish at once (the outputs of test_files/1)."""
    monkeypatch.setenv("PYSRIM2_SCAN_RUNNER", "tests.app.runners:fixture_runner")


def long_setup(tmp_path, checks=READY):
    """A saved N15 setup whose run is over the time limit."""
    backend = n15_backend(tmp_path, checks=checks)
    backend.set_values(LONG)
    backend.save_setup("n15.yaml")
    return backend


def server_for(tmp_path, backend=None):
    backend = backend if backend is not None else long_setup(tmp_path)
    return server.build_server(tmp_path, headless=backend), backend


def answering(answer, allow=True, asked=None):
    """An elicitation callback: ``answer`` is "accept", "decline" or "cancel"."""

    async def respond(context, params):
        if asked is not None:
            asked.append(params.message)
        if answer == "accept":
            return ElicitResult(action="accept", content={"allow": allow})
        return ElicitResult(action=answer)

    return respond


def call(srv, name, args=None, **client_options):
    """Call one tool through the SDK's in-memory client and return its result. Elicitation needs
    the handshake protocol (``mode="legacy"``): the 2026-07-28 protocol has no back channel."""

    async def go():
        async with Client(srv, **client_options) as client:
            return await client.call_tool(name, args or {})

    return asyncio.run(go())


def error_text(result) -> str:
    assert result.is_error, result
    return result.content[0].text


def wait_finished(backend):
    deadline = time.monotonic() + TIMEOUT
    while time.monotonic() < deadline:
        if backend.run_status()["status"] == "finished":
            return
        time.sleep(0.1)
    raise AssertionError("the run did not finish in time")


def test_the_tools_are_listed(tmp_path):
    srv, _ = server_for(tmp_path)

    async def names():
        async with Client(srv) as client:
            return {tool.name for tool in (await client.list_tools()).tools}

    found = asyncio.run(names())
    wanted = {
        "status",
        "get_setup",
        "set_section",
        "set_values",
        "start_run",
        "run_status",
        "summary",
        "plot",
        "undo",
        "export",
        "delete_run",
        "install_step",
    }
    assert wanted <= found


def test_the_instructions_give_the_run_flow_and_the_code_protocol(tmp_path):
    srv, _ = server_for(tmp_path)

    async def instructions():
        async with Client(srv) as client:
            return client.instructions

    text = asyncio.run(instructions())
    assert "start_run" in text and "confirm_code" in text and "lookup_element" in text


def test_the_skill_is_a_resource(tmp_path):
    srv, _ = server_for(tmp_path)

    async def read():
        async with Client(srv) as client:
            return (await client.read_resource("pysrim2://skill")).contents[0].text

    text = asyncio.run(read())
    assert "pysrim2" in text and "TRIM" in text


def test_a_backend_error_is_a_tool_error(tmp_path):
    srv, _ = server_for(tmp_path)
    text = error_text(call(srv, "set_values", {"assignments": ["ion.colour=red"]}))
    assert "unknown key" in text


def test_a_bad_value_is_refused_and_the_setup_is_unchanged(tmp_path):
    srv, backend = server_for(tmp_path)
    before = backend.get_setup()["setup"]
    text = error_text(call(srv, "set_values", {"assignments": ["ion.energy_keV=forty"]}))
    assert "must be a number" in text
    assert backend.get_setup()["setup"] == before


def test_an_allowed_long_run_starts_after_the_user_says_yes(tmp_path, fast):
    asked = []
    srv, backend = server_for(tmp_path)
    result = call(
        srv, "start_run", {}, mode="legacy", elicitation_callback=answering("accept", True, asked)
    )
    assert not result.is_error, result
    assert len(asked) == 1
    assert "start the run" in asked[0] and "120 min" in asked[0]
    wait_finished(backend)
    assert backend.list_runs("n15.yaml")[0]["status"] == "finished"


@pytest.mark.parametrize("answer", ["decline", "cancel"])
def test_a_declined_or_cancelled_long_run_starts_nothing(tmp_path, fast, answer):
    srv, backend = server_for(tmp_path)
    text = error_text(
        call(srv, "start_run", {}, mode="legacy", elicitation_callback=answering(answer))
    )
    assert "did not allow" in text
    assert backend.list_runs("n15.yaml") == []
    assert not (tmp_path / "n15-runs").exists()
    entry = ActivityLog(activity_path()).last(1)[0]
    assert entry.tool == "start_run" and entry.answer == "denied"


def test_an_unanswered_ask_times_out_and_starts_nothing(tmp_path, fast, monkeypatch):
    monkeypatch.setattr(server, "ASK_SECONDS", 0.2)

    async def silent(context, params):
        await anyio.sleep(30)
        return ElicitResult(action="decline")

    srv, backend = server_for(tmp_path)
    text = error_text(call(srv, "start_run", {}, mode="legacy", elicitation_callback=silent))
    assert "did not allow" in text
    assert backend.list_runs("n15.yaml") == []
    assert ActivityLog(activity_path()).last(1)[0].answer == "timed out"


def test_a_client_on_the_modern_protocol_is_asked_with_a_code(tmp_path, fast):
    """The 2026-07-28 protocol cannot carry an elicitation mid-call: the question falls back to
    the one-time code, even though the client declares elicitation."""
    asked = []
    srv, backend = server_for(tmp_path)
    text = error_text(
        call(srv, "start_run", {}, elicitation_callback=answering("accept", True, asked))
    )
    assert "needs the user's OK" in text and CODE.search(text)
    assert asked == []
    assert backend.list_runs("n15.yaml") == []


def test_without_elicitation_the_code_comes_back_and_works_once(tmp_path, fast):
    srv, backend = server_for(tmp_path)
    text = error_text(call(srv, "start_run", {}))
    assert "needs the user's OK" in text
    code = CODE.search(text).group(1)

    result = call(srv, "start_run", {"confirm_code": code})
    assert not result.is_error, result
    wait_finished(backend)

    again = error_text(call(srv, "start_run", {"confirm_code": code}))
    assert "needs the user's OK" in again  # a code is single use


def test_undo_needs_the_app(tmp_path):
    srv, _ = server_for(tmp_path)
    assert "pysrim2 app" in error_text(call(srv, "undo", {}))


def test_the_client_name_reaches_the_activity_log(tmp_path):
    srv, _ = server_for(tmp_path)
    info = Implementation(name="agent-x", version="2.1")
    call(srv, "get_setup", {}, client_info=info)
    entry = ActivityLog(activity_path()).last(1)[0]
    assert entry.client == "agent-x 2.1"
    assert entry.tool == "get_setup" and entry.mode == "headless"


def test_plot_returns_the_picture_and_the_numbers(tmp_path):
    pytest.importorskip("matplotlib")
    folder = run_folder(tmp_path)
    backend = HeadlessBackend(tmp_path, checks=READY)
    srv = server.build_server(tmp_path, headless=backend)
    key = next(p["key"] for p in backend.available_plots(str(folder)) if p["available"])

    result = call(srv, "plot", {"folder": str(folder), "key": key})

    assert not result.is_error, result
    (image,) = [c for c in result.content if c.type == "image"]
    assert image.mime_type == "image/png"
    assert base64.b64decode(image.data).startswith(b"\x89PNG")
    (text,) = [c for c in result.content if c.type == "text"]
    details = json.loads(text.text)
    assert details["mode"] == "headless" and Path(details["path"]).is_file()


def test_the_stdio_server_keeps_its_output_for_the_protocol(tmp_path):
    """``python -m srim.mcp.server`` over stdio: the SDK's client handshakes, lists the tools,
    and reads a setup. Anything else on stdout would break the stream and fail the calls."""
    params = StdioServerParameters(
        command=sys.executable,
        args=["-m", "srim.mcp.server"],
        cwd=str(tmp_path),
        env={**os.environ, "PYSRIM2_HOME": str(tmp_path / "home")},
    )

    async def exchange():
        async with Client(params) as client:
            tools = (await client.list_tools()).tools
            setup = await client.call_tool("get_setup", {})
            return client.instructions, {tool.name for tool in tools}, setup

    instructions, names, setup = asyncio.run(asyncio.wait_for(exchange(), STDIO_SECONDS))
    assert instructions and "start_run" in instructions
    assert {"get_setup", "start_run", "plot"} <= names
    assert not setup.is_error


def test_the_stdio_server_exits_with_0_when_stdin_closes(tmp_path):
    """With no client (stdin at end of file) the server returns 0 and writes nothing to stdout."""
    done = subprocess.run(
        [sys.executable, "-m", "srim.mcp.server"],
        stdin=subprocess.DEVNULL,
        capture_output=True,
        cwd=str(tmp_path),
        env={**os.environ, "PYSRIM2_HOME": str(tmp_path / "home")},
        timeout=STDIO_SECONDS,
        check=False,
    )
    assert done.returncode == 0, done.stderr
    assert done.stdout == b""


def test_a_client_error_on_the_question_is_a_refusal(tmp_path, fast):
    async def broken(context, params):
        return ErrorData(code=-32603, message="the form is not available")

    srv, backend = server_for(tmp_path)
    text = error_text(call(srv, "start_run", {}, mode="legacy", elicitation_callback=broken))
    assert "did not allow" in text
    assert backend.list_runs("n15.yaml") == []


def test_an_unexpected_error_is_a_short_tool_error_and_the_traceback_goes_to_stderr(
    tmp_path, capsys
):
    class Broken(HeadlessBackend):
        def status(self):
            raise RuntimeError("boom")

    srv = server.build_server(tmp_path, headless=Broken(tmp_path, checks=READY))
    text = error_text(call(srv, "status", {}))
    assert text.endswith("RuntimeError: boom")  # the SDK prefixes the tool's name
    assert "Traceback" not in text
    assert "Traceback" in capsys.readouterr().err
