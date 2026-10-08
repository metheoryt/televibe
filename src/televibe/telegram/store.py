"""Where chains live: the bot's own storage behind a protocol (section 12, CHAIN)."""

from __future__ import annotations

from typing import Protocol


class ChainStore(Protocol):
    """Links messages to chains and chains to sessions. The bot implements it on its own storage."""

    async def chain_of(self, chat_id: int, message_id: int) -> str | None: ...

    async def link(self, chat_id: int, message_id: int, chain_id: str) -> None: ...

    async def session_of(self, chain_id: str) -> str | None:
        """The chain's session as `Session.dump()` text, or None if it has none yet."""
        ...

    async def save(self, chain_id: str, session: str) -> None: ...


class MemoryChainStore:
    """A ChainStore in memory: for tests, and for bots that may forget their chains on restart."""

    def __init__(self) -> None:
        self.links: dict[tuple[int, int], str] = {}
        self.sessions: dict[str, str] = {}

    async def chain_of(self, chat_id: int, message_id: int) -> str | None:
        return self.links.get((chat_id, message_id))

    async def link(self, chat_id: int, message_id: int, chain_id: str) -> None:
        self.links[(chat_id, message_id)] = chain_id

    async def session_of(self, chain_id: str) -> str | None:
        return self.sessions.get(chain_id)

    async def save(self, chain_id: str, session: str) -> None:
        self.sessions[chain_id] = session
