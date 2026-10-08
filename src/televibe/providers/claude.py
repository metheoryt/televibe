"""Claude Code: `claude -p --output-format stream-json` (REQ-CLAUDE-1, REQ-CLAUDE-2)."""

from __future__ import annotations

import dataclasses
import uuid
from typing import Any

from televibe.access import Access
from televibe.events import Done, Event, FailReason, Message, Started, ToolUse
from televibe.providers.base import Failure, Parser, Provider, TurnOptions
from televibe.session import Session, reported

_MODES = {Access.READ_ONLY: "plan", Access.FULL: "bypassPermissions"}
_LOST = "No conversation found with session ID"
_DETAIL_KEYS = ("command", "file_path", "path", "pattern", "url", "query", "description")


def _tool_detail(tool_input: Any) -> str:
    if isinstance(tool_input, dict):
        for key in _DETAIL_KEYS:
            value = tool_input.get(key)
            if isinstance(value, str) and value:
                return value
    return ""


class _ClaudeParser(Parser):
    def feed(self, record: dict[str, Any]) -> list[Event]:
        kind = record.get("type")
        if kind == "system" and record.get("subtype") == "init":
            session_id = record.get("session_id")
            if isinstance(session_id, str) and session_id:
                self.session = reported(self.session, session_id)
                return [Started(self.session)]
            return []
        if kind == "assistant":
            message = record.get("message")
            content = message.get("content") if isinstance(message, dict) else None
            texts: list[str] = []
            tools: list[Event] = []
            for block in content if isinstance(content, list) else []:
                if not isinstance(block, dict):
                    continue
                if block.get("type") == "text" and isinstance(block.get("text"), str) and block["text"]:
                    texts.append(block["text"])
                elif block.get("type") == "tool_use":
                    tools.append(ToolUse(str(block.get("name") or ""), _tool_detail(block.get("input"))))
            return ([Message("\n\n".join(texts))] if texts else []) + tools
        if kind == "result":
            if record.get("is_error"):
                errors = record.get("errors")
                detail = "; ".join(str(e) for e in errors) if isinstance(errors, list) and errors else ""
                self.terminal = Failure(
                    FailReason.PROVIDER_ERROR,
                    detail or str(record.get("result") or record.get("subtype") or "the agent reported an error"),
                )
            else:
                session_id = record.get("session_id")
                if isinstance(session_id, str) and session_id:
                    self.session = reported(self.session, session_id)
                usage = record.get("usage")
                self.terminal = Done(self.session, str(record.get("result") or ""), dict(usage) if isinstance(usage, dict) else {})
        return []

    def finish(self, returncode: int, stderr_tail: str) -> Done | Failure:
        if isinstance(self.terminal, Done):
            return self.terminal
        if isinstance(self.terminal, Failure):
            if _LOST in stderr_tail:
                return Failure(FailReason.SESSION_LOST, self.terminal.detail)
            return self.terminal
        return Failure(FailReason.PROVIDER_ERROR, stderr_tail.strip() or f"exited with code {returncode} and no result")


class ClaudeCode(Provider):
    kind = "claude"
    binary = "claude"
    account_var = "CLAUDE_CONFIG_DIR"
    access_levels = frozenset(_MODES)
    fixed_ids = True

    def prepare(self, session: Session) -> Session:
        if session.id is None:
            return dataclasses.replace(session, id=str(uuid.uuid4()))
        return session

    def argv(self, session: Session, options: TurnOptions) -> list[str]:
        argv = [self.binary, "-p", "--output-format", "stream-json", "--verbose", "--permission-mode", _MODES[options.access]]
        if options.model:
            argv += ["--model", options.model]
        if options.instructions:
            argv += ["--append-system-prompt", options.instructions]
        for directory in options.add_dirs:
            argv += ["--add-dir", str(directory)]
        assert session.id is not None, "prepare() gives every Claude Code session an id"
        argv += ["--resume" if session.started else "--session-id", session.id]
        return argv

    def parser(self, session: Session) -> Parser:
        return _ClaudeParser(session)
