"""An agent account: one directory with its login, settings and transcripts (section 3)."""

import os
from collections.abc import Mapping
from pathlib import Path
from types import MappingProxyType

from televibe.env import check_credentials
from televibe.errors import TelevibeError


class Account:
    __slots__ = ("_credentials", "home")

    def __init__(self, home: str | os.PathLike[str], credentials: Mapping[str, str] | None = None) -> None:
        if home is None:
            raise TelevibeError("Account needs a home directory; televibe never falls back to ~/.claude or ~/.codex")
        self.home = Path(home).expanduser().resolve()
        self._credentials = check_credentials(credentials or {})

    @property
    def credentials(self) -> Mapping[str, str]:
        return MappingProxyType(self._credentials)

    def __repr__(self) -> str:
        return f"Account(home={str(self.home)!r}, credentials=<{len(self._credentials)} hidden>)"
