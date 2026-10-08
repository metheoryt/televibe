"""Sessions: one agent conversation, pinned to a provider, an account and a directory (section 4)."""

from __future__ import annotations

import dataclasses
import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any

from televibe.errors import TelevibeError

if TYPE_CHECKING:
    from televibe.providers.base import Provider

_KEYS = {"provider": str, "home": str, "cwd": str, "id": (str, type(None)), "started": bool}


@dataclass(frozen=True, slots=True)
class Session:
    provider: str
    home: Path
    cwd: Path
    id: str | None
    started: bool
    _bound: Provider | None = field(default=None, compare=False, repr=False)

    def dump(self) -> str:
        return json.dumps(
            {"provider": self.provider, "home": str(self.home), "cwd": str(self.cwd), "id": self.id, "started": self.started}
        )


def parse_dump(text: str) -> dict[str, Any]:
    try:
        data = json.loads(text)
    except (TypeError, ValueError) as exc:
        raise TelevibeError(f"not a session dump: {exc}") from None
    if not isinstance(data, dict) or data.keys() != _KEYS.keys():
        raise TelevibeError("not a session dump: expected keys " + ", ".join(sorted(_KEYS)))
    for key, kind in _KEYS.items():
        if not isinstance(data[key], kind):
            raise TelevibeError(f"not a session dump: {key!r} has the wrong type")
    if data["started"] and not data["id"]:
        raise TelevibeError("not a session dump: a started session needs an id")
    return data


def unbound(text: str) -> Session:
    """A session read back from disk with no provider to run it (REQ-STATE-3)."""
    data = parse_dump(text)
    return Session(data["provider"], Path(data["home"]), Path(data["cwd"]), data["id"], data["started"])


def reported(session: Session, id: str) -> Session:
    """The session as the agent reported it: the id is known and the agent has it."""
    return dataclasses.replace(session, id=id, started=True)
