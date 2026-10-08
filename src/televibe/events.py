"""The events a turn yields (section 5)."""

from __future__ import annotations

import enum
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from televibe.session import Session


class FailReason(enum.StrEnum):
    TIMEOUT = "timeout"
    CANCELLED = "cancelled"
    SESSION_LOST = "session_lost"
    PROVIDER_ERROR = "provider_error"
    SPAWN_ERROR = "spawn_error"


@dataclass(frozen=True, slots=True)
class Queued:
    """The turn is waiting for a free slot, lane or session."""


@dataclass(frozen=True, slots=True)
class Started:
    """The agent process is running and reported its session id."""

    session: Session


@dataclass(frozen=True, slots=True)
class Message:
    """One complete assistant message."""

    text: str


@dataclass(frozen=True, slots=True)
class ToolUse:
    """The agent started a tool (a command, an edit, a search)."""

    name: str
    detail: str


@dataclass(frozen=True, slots=True)
class Warning:  # noqa: A001 - the spec's name; it shadows the builtin only inside this module
    """The agent reported a problem that did not end the turn."""

    text: str


@dataclass(frozen=True, slots=True)
class Done:
    """Terminal: the turn finished. `text` is the final answer."""

    session: Session
    text: str
    usage: dict[str, Any]


@dataclass(frozen=True, slots=True)
class Failed:
    """Terminal: the turn did not finish."""

    reason: FailReason
    session: Session | None
    detail: str
    partial: str


type Event = Queued | Started | Message | ToolUse | Warning | Done | Failed

TERMINAL = (Done, Failed)
