"""Run headless coding agents one turn at a time, as resumable sessions with a typed event stream."""

import sys

if sys.platform == "win32":
    raise ImportError(
        "televibe supports Linux and macOS only: it kills an agent by its process group, "
        "which Windows does not have"
    )

from televibe.access import Access
from televibe.account import Account
from televibe.engine import Engine, Stranded
from televibe.errors import TelevibeError
from televibe.events import Done, Failed, FailReason, LimitWindow, Limits, Message, Queued, Started, ToolUse, Warning
from televibe.providers import ClaudeCode, Codex
from televibe.session import Session
from televibe.turn import Turn

__all__ = [
    "Access", "Account", "ClaudeCode", "Codex", "Done", "Engine", "FailReason", "Failed", "LimitWindow",
    "Limits", "Message",
    "Queued", "Session", "Started", "Stranded", "TelevibeError", "ToolUse", "Turn", "Warning",
]
