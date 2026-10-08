"""What every provider supplies: sessions, a command line, and a stream parser."""

from __future__ import annotations

import os
from abc import ABC, abstractmethod
from dataclasses import dataclass
from pathlib import Path
from typing import Any, ClassVar

from televibe.access import Access
from televibe.account import Account
from televibe.errors import TelevibeError
from televibe.events import Done, Event, FailReason
from televibe.session import Session, parse_dump


@dataclass(frozen=True, slots=True)
class TurnOptions:
    access: Access
    model: str | None
    instructions: str | None
    add_dirs: tuple[Path, ...]


@dataclass(frozen=True, slots=True)
class Failure:
    """How a provider says a turn failed; the runner turns it into `Failed`."""

    reason: FailReason
    detail: str


class Parser(ABC):
    """Reads one turn's records. `session` gains its id once the agent reports it."""

    def __init__(self, session: Session) -> None:
        self.session = session
        self.terminal: Done | Failure | None = None

    @abstractmethod
    def feed(self, record: dict[str, Any]) -> list[Event]:
        """Non-terminal events for one record; sets `terminal` on a terminal record."""

    @abstractmethod
    def finish(self, returncode: int, stderr_tail: str) -> Done | Failure:
        """The turn's outcome once the process has exited."""


class Provider(ABC):
    kind: ClassVar[str]
    binary: ClassVar[str]
    account_var: ClassVar[str]
    access_levels: ClassVar[frozenset[Access]]
    fixed_ids: ClassVar[bool]

    def __init__(self, account: Account) -> None:
        if not isinstance(account, Account):
            raise TelevibeError(f"{type(self).__name__} needs an Account, got {type(account).__name__}")
        self.account = account

    def __repr__(self) -> str:
        return f"{type(self).__name__}(account={self.account!r})"

    def new_session(self, cwd: str | os.PathLike[str], id: str | None = None) -> Session:
        if id is not None and not self.fixed_ids:
            raise TelevibeError(f"{self.kind} names its own sessions; new_session takes no id")
        if id is not None and (not isinstance(id, str) or not id):
            raise TelevibeError("a fixed session id must be a non-empty str")
        return self._session(cwd, id, started=False)

    def resume_session(self, cwd: str | os.PathLike[str], id: str) -> Session:
        if not isinstance(id, str) or not id:
            raise TelevibeError("resume_session needs the session's non-empty id")
        return self._session(cwd, id, started=True)

    def load_session(self, text: str) -> Session:
        data = parse_dump(text)
        if data["provider"] != self.kind:
            raise TelevibeError(f"this session belongs to {data['provider']!r}, not {self.kind!r}")
        if Path(data["home"]) != self.account.home:
            raise TelevibeError(f"this session belongs to account home {data['home']}, not {self.account.home}")
        return Session(self.kind, self.account.home, Path(data["cwd"]), data["id"], data["started"], _bound=self)

    def _session(self, cwd: str | os.PathLike[str], id: str | None, *, started: bool) -> Session:
        return Session(self.kind, self.account.home, Path(cwd).expanduser().resolve(), id, started, _bound=self)

    def check_access(self, access: Access) -> None:
        if access not in self.access_levels:
            supported = ", ".join(sorted(level.name for level in self.access_levels))
            raise TelevibeError(f"{self.kind} does not support Access.{access.name}; it supports {supported}")

    def prepare(self, session: Session) -> Session:
        """The session as the turn will run it."""
        return session

    @abstractmethod
    def argv(self, session: Session, options: TurnOptions) -> list[str]: ...

    @abstractmethod
    def parser(self, session: Session) -> Parser: ...
