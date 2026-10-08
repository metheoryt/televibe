"""The Engine: ordering, recovery, and the factory for turns."""

from __future__ import annotations

import asyncio
import json
import logging
import math
import os
import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from types import MappingProxyType
from typing import Any

from televibe.access import Access
from televibe.env import check_turn_env
from televibe.errors import TelevibeError
from televibe.markers import Markers
from televibe.procinfo import kill_group, start_time
from televibe.providers.base import TurnOptions
from televibe.scheduler import Scheduler
from televibe.session import Session, unbound
from televibe.turn import Runner, Turn, TurnSpec

log = logging.getLogger("televibe")

_EMPTY: Mapping[str, Any] = MappingProxyType({})
_TURN_ID = re.compile(r"[0-9a-f]{32}")


@dataclass(frozen=True, slots=True)
class Stranded:
    """A turn an earlier process left in flight (REQ-STATE-3).

    `session` is not bound to a provider; run it again with
    `provider.load_session(stranded.session.dump())`.
    """

    turn_id: str
    tag: dict[str, Any]
    session: Session
    started_at: datetime
    killed: bool


def _kill_if_same(pgid: object, recorded: object) -> bool:
    """Kill the group only if its leader exists and started when recorded (REQ-STATE-4)."""
    if not isinstance(pgid, int) or not isinstance(recorded, str):
        return False
    if start_time(pgid) != recorded:
        return False
    kill_group(pgid)
    return True


class Engine:
    def __init__(self, state_dir: str | os.PathLike[str], max_concurrent: int = 1) -> None:
        if isinstance(max_concurrent, bool) or not isinstance(max_concurrent, int) or max_concurrent < 1:
            raise TelevibeError("max_concurrent must be a positive int")
        self._markers = Markers(Path(state_dir).expanduser())
        self._scheduler = Scheduler(max_concurrent)
        self._runners: dict[str, Runner] = {}
        self._state = "new"

    async def __aenter__(self) -> Engine:
        if self._state != "new":
            raise TelevibeError("an Engine is entered once")
        self._markers.open()
        self._state = "open"
        return self

    async def __aexit__(self, *exc_info: object) -> None:
        self._state = "closed"
        runners = list(self._runners.values())
        for runner in runners:
            runner.cancel("engine closed")  # REQ-CANCEL-7
        await asyncio.gather(*(runner.wait_ended() for runner in runners), return_exceptions=True)

    def _require_open(self) -> None:
        if self._state != "open":
            raise TelevibeError("the Engine is not open: use it inside `async with Engine(...)`")

    def turn(
        self,
        prompt: str,
        *,
        session: Session,
        access: Access = Access.READ_ONLY,
        lane: str | None = None,
        model: str | None = None,
        instructions: str | None = None,
        env: Mapping[str, str] = _EMPTY,
        add_dirs: Sequence[str | os.PathLike[str]] = (),
        timeout_s: float = 900,
        tag: Mapping[str, Any] = _EMPTY,
    ) -> Turn:
        self._require_open()
        if not isinstance(prompt, str) or not prompt:
            raise TelevibeError("prompt must be a non-empty str")
        if not isinstance(session, Session):
            raise TelevibeError(f"session must be a Session, got {type(session).__name__}")
        provider = session._bound
        if provider is None:
            raise TelevibeError("this session is not bound to a provider; use provider.load_session(session.dump())")
        if not isinstance(access, Access):
            raise TelevibeError(f"access must be an Access, got {access!r}")
        provider.check_access(access)
        for name, value in (("lane", lane), ("model", model), ("instructions", instructions)):
            if value is not None and not isinstance(value, str):
                raise TelevibeError(f"{name} must be a str or None")
        if isinstance(timeout_s, bool) or not isinstance(timeout_s, (int, float)) or not math.isfinite(timeout_s) or timeout_s <= 0:
            raise TelevibeError("timeout_s must be a positive, finite number of seconds")
        if isinstance(add_dirs, (str, bytes, os.PathLike)):
            raise TelevibeError("add_dirs must be a sequence of paths, not one path")
        dirs = tuple(Path(d).expanduser().resolve() for d in add_dirs)
        turn_env = check_turn_env(env)
        try:
            tag_copy = json.loads(json.dumps(dict(tag)))
        except (TypeError, ValueError) as exc:
            raise TelevibeError(f"tag must be a JSON-serializable mapping: {exc}") from None
        spec = TurnSpec(prompt, TurnOptions(access, model, instructions, dirs), lane, turn_env, float(timeout_s), tag_copy)
        return Turn(Runner(self, provider, provider.prepare(session), spec))

    async def stranded(self) -> list[Stranded]:
        """Every marker an earlier process left; kills its agent if still alive (REQ-STATE-3, REQ-STATE-4)."""
        self._require_open()
        found = []
        for marker in self._markers.all():
            turn_id = marker.get("turn_id")
            if not isinstance(turn_id, str) or turn_id in self._runners:
                continue
            try:
                session = unbound(marker["session"])
                started_at = datetime.fromisoformat(marker["started_at"])
            except (KeyError, TypeError, ValueError, TelevibeError):
                log.warning("televibe: marker %s is malformed; skipping it", turn_id)
                continue
            killed = await asyncio.to_thread(_kill_if_same, marker.get("pgid"), marker.get("proc_start"))
            found.append(Stranded(turn_id, dict(marker.get("tag") or {}), session, started_at, killed))
        return found

    def forget(self, turn_id: str) -> None:
        """Remove a stranded marker (REQ-STATE-5)."""
        self._require_open()
        if not isinstance(turn_id, str) or not _TURN_ID.fullmatch(turn_id):
            raise TelevibeError(f"not a turn id: {turn_id!r}")
        if turn_id in self._runners:
            raise TelevibeError(f"turn {turn_id} is live in this engine, not stranded")
        self._markers.remove(turn_id)
