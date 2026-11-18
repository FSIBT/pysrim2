"""The agent's tools: one method per MCP tool, with the ask-first rules (Qt-free, no MCP SDK).

Every method calls the backend (the headless one, or the app's, which has the same methods) and
returns its result with ``"mode"`` added, so the agent can say which one answered. A list result
comes back as ``{"mode", "items"}``. Every call appends exactly one :class:`ActivityEntry` to the
log, errors included, with what was asked and answered.

The ask-first rules are the ones of the spec: a run estimated over the time limit or wanting more
than the core limit, replacing a file this session did not write, discarding unsaved changes,
and every installer step need the user's OK. The client's elicitation (``ask``) answers when it
can. When it cannot (``ask`` is None or answers None), the call is refused with
:class:`ApprovalNeeded`, which carries a one-time code bound to exactly that action; the agent
asks the user in the chat and repeats the call with the code.
"""

import time
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any, Literal

from srim.app.settings import AppSettings
from srim.mcp import lookup
from srim.mcp.activity import ActivityEntry, ActivityLog
from srim.mcp.approval import ApprovalCodes, action_hash
from srim.mcp.backend import Backend, BackendError
from srim.mcp.limits import RunRequest, reasons_to_ask

#: what the user answered, as the client's elicitation reports it
Answer = Literal["allowed", "denied", "timed out"]
#: asks the user about ``text``; None means the client cannot be asked
Ask = Callable[[str], Answer | None]


class ApprovalNeeded(BackendError):
    """The action needs the user's OK, and the client could not be asked for it.

    ``code`` is the one-time code: the agent repeats the same call with ``confirm_code=code``
    once the user agrees. The message (the agent's text) says what to do.
    """

    def __init__(self, message: str, code: str):
        super().__init__(message)
        self.code = code


@dataclass
class _Record:
    """What one call asked and was answered, for its activity entry."""

    ask: str | None = None
    answer: str | None = None


class Tools:
    """The tools of one agent session, over one backend."""

    def __init__(
        self,
        backend: Backend,
        log: ActivityLog,
        approvals: ApprovalCodes,
        settings: AppSettings,
        client: str,
        ask: Ask | None = None,
        cores: int | None = None,
    ):
        self.backend = backend
        self.log = log
        self.approvals = approvals
        self.settings = settings
        self.client = client
        self._ask = ask
        self._cores = cores  # None: the cores the backend estimates with

    # --- status and reading -------------------------------------------------------------------

    def status(self) -> dict:
        return self._call("status", "read the status", lambda _: self.backend.status())

    def get_setup(self) -> dict:
        return self._call("get_setup", "read the setup", lambda _: self.backend.get_setup())

    def lookup_element(self, text: str) -> dict:
        return self._call(
            "lookup_element", f"look up the element {text}", lambda _: lookup.lookup_element(text)
        )

    def find_material(self, text: str) -> dict:
        return self._call(
            "find_material", f"look up the material {text}", lambda _: lookup.find_material(text)
        )

    def estimate(self, workers: int | None = None, batch_ions: int | None = None) -> dict:
        return self._call(
            "estimate",
            "estimate a run",
            lambda _: self.backend.estimate(workers, batch_ions),
        )

    def run_status(self, folder: str | None = None) -> dict:
        return self._call(
            "run_status", "read the run status", lambda _: self.backend.run_status(folder)
        )

    def list_runs(self, setup_path: str | None = None) -> dict:
        return self._call(
            "list_runs", "list the runs", lambda _: self.backend.list_runs(setup_path)
        )

    def open_run(self, folder: str) -> dict:
        return self._call(
            "open_run", f"open the run {folder}", lambda _: self.backend.open_run(folder)
        )

    def summary(self, folder: str | None = None, point: int | None = None) -> dict:
        return self._call(
            "summary", "read the summary", lambda _: self.backend.summary(folder, point)
        )

    def available_plots(self, folder: str | None = None, point: int | None = None) -> dict:
        return self._call(
            "available_plots",
            "list the plots",
            lambda _: self.backend.available_plots(folder, point),
        )

    def table(
        self,
        folder: str | None = None,
        point: int | None = None,
        key: str = "",
        max_rows: int = 500,
    ) -> dict:
        return self._call(
            "table",
            f"read the table of {key}",
            lambda _: self.backend.table(folder, point, key, max_rows),
        )

    def plot(self, folder: str | None = None, point: int | None = None, key: str = "") -> dict:
        return self._call("plot", f"plot {key}", lambda _: self.backend.plot(folder, point, key))

    def install_plan(self, components: list[str] | None = None) -> dict:
        return self._call(
            "install_plan", "plan the install", lambda _: self.backend.install_plan(components)
        )

    # --- editing and controlling (no ask) ------------------------------------------------

    def set_values(self, assignments: list[str]) -> dict:
        return self._call(
            "set_values",
            f"set {_text(assignments)}",
            lambda _: self.backend.set_values(assignments),
        )

    def set_section(self, name: str, value: Any) -> dict:
        return self._call(
            "set_section",
            f"set the {name} section",
            lambda _: self.backend.set_section(name, value),
        )

    def undo(self) -> dict:
        return self._call("undo", "undo the last change", lambda _: self.backend.undo())

    def redo(self) -> dict:
        return self._call("redo", "redo the last undone change", lambda _: self.backend.redo())

    def control_run(self, action: str, folder: str | None = None) -> dict:
        return self._call(
            "control_run",
            f"{action} the run",
            lambda _: self.backend.control_run(action, folder),
        )

    # --- ask first: files and the setup ---------------------------------------------------

    def new_setup(self, confirm_code: str | None = None) -> dict:
        def act(record: _Record) -> dict:
            self._discard_unsaved(record, "new_setup", {}, confirm_code)
            return self.backend.new_setup()

        return self._call("new_setup", "start a new setup", act)

    def open_setup(self, path: str, confirm_code: str | None = None) -> dict:
        def act(record: _Record) -> dict:
            self._discard_unsaved(record, "open_setup", {"path": path}, confirm_code)
            return self.backend.open_setup(path)

        return self._call("open_setup", f"open {path}", act)

    def save_setup(self, path: str | None = None, confirm_code: str | None = None) -> dict:
        def act(record: _Record) -> dict:
            target = path if path is not None else self.backend.get_setup()["path"]
            if target is not None and self.backend.would_replace(target):
                self._approve(
                    record,
                    "save_setup",
                    {"path": path},
                    f"replace {target}, a file this session did not create",
                    confirm_code=confirm_code,
                )
            return self.backend.save_setup(path)

        return self._call("save_setup", f"save the setup to {path or 'its file'}", act)

    def export(
        self,
        folder: str | None,
        fmt: str,
        dest: str,
        include_collision: bool = False,
        point: int | None = None,
        confirm_code: str | None = None,
    ) -> dict:
        args = {
            "folder": folder,
            "fmt": fmt,
            "dest": dest,
            "include_collision": include_collision,
            "point": point,
        }

        def act(record: _Record) -> dict:
            files = self.backend.export_files(folder, fmt, dest, include_collision, point)
            replaced = [name for name in files if self.backend.would_replace(name)]
            if replaced:
                self._approve(
                    record,
                    "export",
                    args,
                    f"replace {', '.join(replaced)} (files this session did not write)",
                    confirm_code=confirm_code,
                )
            return self.backend.export(folder, fmt, dest, include_collision, point)

        return self._call("export", f"export {fmt} tables to {dest}", act)

    # --- ask first: runs and installs -----------------------------------------------------

    def start_run(
        self,
        workers: int | None = None,
        batch_ions: int | None = None,
        confirm_code: str | None = None,
    ) -> dict:
        args = {"workers": workers, "batch_ions": batch_ions}
        shown = workers if workers is not None else "default"
        return self._call(
            "start_run",
            f"start a run (workers {shown})",
            lambda record: self._start_run(record, args, batch_ions, confirm_code),
        )

    def _start_run(
        self,
        record: _Record,
        args: dict,
        batch_ions: int | None,
        confirm_code: str | None,
    ) -> dict:
        status = self.backend.status()
        if not status["srim_ready"]:
            raise BackendError(f"SRIM is not ready: {status['srim_problem']}. See install_plan().")
        setup_path = self.backend.get_setup()["path"]
        if setup_path is None:
            raise BackendError("save the setup first: save_setup(path)")
        estimate = self.backend.estimate(args["workers"], batch_ions)
        # the override can only tighten the limit: it never counts more cores than the backend
        cores = estimate["cores"] if self._cores is None else min(self._cores, estimate["cores"])
        request = RunRequest(
            seconds=estimate["seconds"],
            workers=estimate["workers"],
            cores=cores,
            units=estimate["units"],
        )
        reasons = reasons_to_ask(request, self.settings)
        # starting saves unsaved changes into the setup's own file: that is a replacement when
        # this session did not create the file, so it is one of the reasons to ask
        if self.backend.has_unsaved_changes() and self.backend.would_replace(setup_path):
            reasons.append(
                f"save the unsaved changes into {setup_path}, a file this session did not create"
            )
        if reasons:
            self._approve(
                record,
                "start_run",
                args,
                "start the run: " + "; ".join(reasons),
                workers=estimate["workers"],
                confirm_code=confirm_code,
            )
        return self.backend.start_run(workers=estimate["workers"], batch_ions=batch_ions)

    def delete_run(self, folder: str, confirm_code: str | None = None) -> dict:
        text = f"delete the run folder {folder}"

        def act(record: _Record) -> dict:
            self._approve(record, "delete_run", {"folder": folder}, text, confirm_code=confirm_code)
            return self.backend.delete_run(folder)

        return self._call("delete_run", text, act)

    def install_step(self, component: str, confirm_code: str | None = None) -> dict:
        def act(record: _Record) -> dict:
            # the steps that would run (no admin ones, none already installed), with their
            # commands: the user is asked about these, and they are part of the approved action
            would_run = [
                {
                    "step": row["step"],
                    "description": row["description"],
                    "commands": row["commands"],
                }
                for row in self.backend.install_plan([component])
                if not row["admin"] and not row["done"]
            ]
            listing = "; ".join(
                f"{row['step']} ({' ; '.join(row['commands'])})" for row in would_run
            )
            text = (
                f"run these install steps of {component}: {listing or 'none'} "
                "(admin steps are only shown as commands)"
            )
            self._approve(
                record,
                "install_step",
                {"component": component, "steps": would_run},
                text,
                confirm_code=confirm_code,
            )
            return self.backend.install_step(component, expected=would_run)

        return self._call("install_step", f"install {component}", act)

    # --- the ask-first machinery ------------------------------------------------------------

    def _discard_unsaved(
        self, record: _Record, tool: str, args: dict, confirm_code: str | None
    ) -> None:
        """Ask before the unsaved changes of the setup are dropped (none when there are none)."""
        if not self.backend.has_unsaved_changes():
            return
        current = self.backend.get_setup()["path"] or "the new setup"
        self._approve(
            record,
            tool,
            args,
            f"discard the unsaved changes of {current}",
            confirm_code=confirm_code,
        )

    def _approve(
        self,
        record: _Record,
        tool: str,
        args: dict,
        text: str,
        *,
        workers: int | None = None,
        confirm_code: str | None = None,
    ) -> None:
        """Return once the user allowed ``text`` for exactly this action.

        A valid code for this action passes. Otherwise the client is asked: "allowed" passes,
        any other answer raises :class:`BackendError`. With no answer possible, a code is issued
        and :class:`ApprovalNeeded` raised.
        """
        current = self.backend.get_setup()
        # the file is part of the action: a code for one file is no use for another
        setup = {"path": current["path"], "setup": current["setup"]}
        action = action_hash(tool, args, setup, workers)
        record.ask = text
        if confirm_code is not None and self.approvals.redeem(str(confirm_code), action):
            record.answer = "allowed"
            return
        answer = self._ask(text) if self._ask is not None else None
        if answer is None:
            code = self.approvals.issue(action)
            record.answer = "code issued"
            raise ApprovalNeeded(self._needs_ok(tool, text, code), code)
        if answer == "allowed":
            record.answer = "allowed"
            return
        record.answer = "timed out" if answer == "timed out" else "denied"
        raise BackendError(f"the user did not allow this: {text}")

    def _needs_ok(self, tool: str, text: str, code: str) -> str:
        minutes = round(self.approvals.ttl / 60)
        return (
            f"This needs the user's OK: {text}. Ask the user. If they agree, call {tool} again "
            f'with the same arguments and confirm_code="{code}" (valid for {minutes} minutes).'
        )

    # --- one call, one activity entry -------------------------------------------------------

    def _call(self, tool: str, summary: str, action: Callable[[_Record], Any]) -> dict:
        record = _Record()
        try:
            result = action(record)
        except BaseException as error:
            self._record(tool, summary, record, _error_text(error, record))
            raise
        self._record(tool, summary, record, None)
        return _with_mode(result, self.backend.mode)

    def _record(self, tool: str, summary: str, record: _Record, error: str | None) -> None:
        self.log.append(
            ActivityEntry(
                time=time.time(),
                mode=self.backend.mode,
                client=self.client,
                tool=tool,
                summary=summary,
                ask=record.ask,
                answer=record.answer,
                error=error,
            )
        )


def _error_text(error: BaseException, record: _Record) -> str:
    """The error as the log keeps it: an approval request is logged without its one-time code."""
    if isinstance(error, ApprovalNeeded):
        return f"approval needed: {record.ask}"
    return str(error) or type(error).__name__


def _with_mode(result: Any, mode: str) -> dict:
    """The result with ``mode`` added; a list comes back under ``items``."""
    if isinstance(result, dict):
        return {**result, "mode": mode}
    return {"mode": mode, "items": result}


def _text(value: Any) -> str:
    """A short text for an activity entry, whatever the agent sent."""
    if isinstance(value, list):
        return "; ".join(str(item) for item in value)
    return str(value)
