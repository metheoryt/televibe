"""Codex: `codex exec --json` (REQ-CODEX-1..3)."""

from __future__ import annotations

from typing import Any

from televibe.access import Access
from televibe.events import Done, Event, FailReason, Message, Started, ToolUse, Warning
from televibe.providers.base import Failure, Parser, Provider, TurnOptions
from televibe.session import Session, reported

_ACCESS = {
    Access.READ_ONLY: ["--sandbox", "read-only"],
    Access.WORKSPACE_WRITE: ["--sandbox", "workspace-write"],
    Access.FULL: ["--dangerously-bypass-approvals-and-sandbox"],
}
_LOST = "no rollout found for thread id"
_NOT_TOOLS = frozenset({"agent_message", "reasoning", "error"})
_ESCAPES = {'"': '\\"', "\\": "\\\\", "\n": "\\n", "\t": "\\t", "\r": "\\r", "\b": "\\b", "\f": "\\f"}


def toml_basic_string(value: str) -> str:
    out = []
    for ch in value:
        if ch in _ESCAPES:
            out.append(_ESCAPES[ch])
        elif ord(ch) < 0x20 or ord(ch) == 0x7F:
            out.append(f"\\u{ord(ch):04x}")
        else:
            out.append(ch)
    return '"' + "".join(out) + '"'


class _CodexParser(Parser):
    def __init__(self, session: Session) -> None:
        super().__init__(session)
        self._thread_seen = False
        self._last_message = ""

    def feed(self, record: dict[str, Any]) -> list[Event]:
        kind = record.get("type")
        if kind == "thread.started":
            thread_id = record.get("thread_id")
            if isinstance(thread_id, str) and thread_id:
                self._thread_seen = True
                self.session = reported(self.session, thread_id)
                return [Started(self.session)]
            return []
        item = record.get("item") if isinstance(record.get("item"), dict) else {}
        item_type = item.get("type")
        if kind == "item.completed" and item_type == "agent_message":
            text = str(item.get("text") or "")
            if not text:
                return []
            self._last_message = text
            return [Message(text)]
        if kind == "item.completed" and item_type == "error":
            return [Warning(str(item.get("message") or ""))]
        if kind == "item.started" and isinstance(item_type, str) and item_type not in _NOT_TOOLS:
            detail = str(item.get("command") or "") if item_type == "command_execution" else ""
            return [ToolUse(item_type, detail)]
        if kind == "turn.completed":
            usage = record.get("usage")
            self.terminal = Done(self.session, self._last_message, dict(usage) if isinstance(usage, dict) else {})
        return []

    def finish(self, returncode: int, stderr_tail: str) -> Done | Failure:
        if isinstance(self.terminal, Done):
            return self.terminal
        if returncode != 0 and not self._thread_seen and _LOST in stderr_tail:
            return Failure(FailReason.SESSION_LOST, stderr_tail.strip())
        return Failure(FailReason.PROVIDER_ERROR, stderr_tail.strip() or f"exited with code {returncode} and no result")


class Codex(Provider):
    kind = "codex"
    binary = "codex"
    account_var = "CODEX_HOME"
    access_levels = frozenset(_ACCESS)
    fixed_ids = False

    def argv(self, session: Session, options: TurnOptions) -> list[str]:
        argv = [self.binary, "exec", "--json", "--skip-git-repo-check", "-C", str(session.cwd), *_ACCESS[options.access]]
        if options.model:
            argv += ["-m", options.model]
        for directory in options.add_dirs:
            argv += ["--add-dir", str(directory)]
        if options.instructions:
            argv += ["-c", "developer_instructions=" + toml_basic_string(options.instructions)]
        if session.started:
            assert session.id is not None
            argv += ["resume", session.id]
        argv.append("-")
        return argv

    def parser(self, session: Session) -> Parser:
        return _CodexParser(session)
