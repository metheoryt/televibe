"""Who may start: a slot limit, one turn per lane, one turn per session (section 6)."""

import asyncio
from collections.abc import Callable
from dataclasses import dataclass, field


@dataclass(eq=False)
class Ticket:
    lane: str | None
    session_key: tuple[str, str, str] | None
    held: bool = False
    future: asyncio.Future[bool] | None = field(default=None, repr=False)


class Scheduler:
    def __init__(self, max_concurrent: int) -> None:
        self._max = max_concurrent
        self._running = 0
        self._lanes: set[str] = set()
        self._sessions: set[tuple[str, str, str]] = set()
        self._waiting: list[Ticket] = []

    def _free(self, ticket: Ticket) -> bool:
        return (
            self._running < self._max
            and (ticket.lane is None or ticket.lane not in self._lanes)
            and (ticket.session_key is None or ticket.session_key not in self._sessions)
        )

    def _take(self, ticket: Ticket) -> None:
        self._running += 1
        if ticket.lane is not None:
            self._lanes.add(ticket.lane)
        if ticket.session_key is not None:
            self._sessions.add(ticket.session_key)
        ticket.held = True

    async def acquire(self, ticket: Ticket, on_queued: Callable[[], None]) -> bool:
        """True once the turn may start; False if it was withdrawn while waiting."""
        if self._free(ticket):
            self._take(ticket)
            return True
        ticket.future = asyncio.get_running_loop().create_future()
        self._waiting.append(ticket)
        on_queued()
        return await ticket.future

    def release(self, ticket: Ticket) -> None:
        if not ticket.held:
            return
        ticket.held = False
        self._running -= 1
        self._lanes.discard(ticket.lane)  # type: ignore[arg-type]
        self._sessions.discard(ticket.session_key)  # type: ignore[arg-type]
        self._dispatch()

    def withdraw(self, ticket: Ticket) -> None:
        if ticket in self._waiting:
            self._waiting.remove(ticket)
            if ticket.future is not None and not ticket.future.done():
                ticket.future.set_result(False)

    def _dispatch(self) -> None:
        for ticket in list(self._waiting):
            if self._free(ticket):
                self._waiting.remove(ticket)
                self._take(ticket)
                if ticket.future is not None and not ticket.future.done():
                    ticket.future.set_result(True)
