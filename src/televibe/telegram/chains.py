"""Reply chains: one session per conversation that follows replies (section 12, CHAIN)."""

from __future__ import annotations

import asyncio
import logging
import os
import uuid
from collections.abc import AsyncIterator, Mapping
from dataclasses import dataclass
from typing import Any

from aiogram.types import Message

from televibe.engine import Engine
from televibe.errors import TelevibeError
from televibe.events import Done, Event, Failed, Started
from televibe.providers.base import Provider
from televibe.session import Session
from televibe.telegram.store import ChainStore
from televibe.turn import Turn

log = logging.getLogger(__name__)

_TAG_KEYS = ("chain", "chat_id", "message_id")


@dataclass(frozen=True, slots=True)
class Chain:
    id: str


class Chains:
    """Finds chains and runs their turns. A bot keeps one Chains for its lifetime: the order of turns lives here."""

    def __init__(self, store: ChainStore) -> None:
        self.store = store
        self._tails: dict[str, asyncio.Future[None]] = {}
        """Per chain, the future the next entering turn waits for: the end of the last turn in line."""

    async def find(self, message: Message) -> Chain | None:
        """The chain of the message `message` replies to (REQ-CHAIN-1)."""
        reply = message.reply_to_message
        if reply is None:
            return None
        chain_id = await self.store.chain_of(message.chat.id, reply.message_id)
        return Chain(chain_id) if chain_id is not None else None

    def new(self) -> Chain:
        """A chain with a fresh id. Nothing is stored until its first turn links a message."""
        return Chain(uuid.uuid4().hex)

    def fork(self, chain: Chain) -> Chain:
        raise TelevibeError("forking a chain needs session forks in the core, which v1 does not have (REQ-CHAIN-2)")

    async def link(self, chain: Chain, sent: Message) -> None:
        """Link the bot's answer, so a reply to it continues the chain (REQ-CHAIN-9)."""
        await self.store.link(sent.chat.id, sent.message_id, chain.id)

    def turn(
        self,
        engine: Engine,
        chain: Chain,
        message: Message,
        *,
        provider: Provider,
        cwd: str | os.PathLike[str],
        prompt: str,
        **kwargs: Any,
    ) -> ChainTurn:
        """A turn of `chain` started by `message`; the rest of kwargs goes to engine.turn (REQ-CHAIN-3)."""
        if "session" in kwargs:
            raise TelevibeError("a chain turn takes no session: the chain chooses it (REQ-CHAIN-5)")
        tag = kwargs.pop("tag", {})
        if not isinstance(tag, Mapping):
            raise TelevibeError("tag must be a mapping")
        clash = sorted(set(tag) & set(_TAG_KEYS))
        if clash:
            raise TelevibeError(f"tag keys {', '.join(clash)} are set by the chain (REQ-CHAIN-7)")
        tag = {**tag, "chain": chain.id, "chat_id": message.chat.id, "message_id": message.message_id}
        return ChainTurn(self, engine, chain, message, provider, cwd, prompt, tag, kwargs)

    def _enqueue(self, chain_id: str) -> tuple[asyncio.Future[None] | None, asyncio.Future[None]]:
        """Take the next place in the chain's line: (the end of the turn ahead, this turn's end)."""
        ahead = self._tails.get(chain_id)
        mine = asyncio.get_running_loop().create_future()
        self._tails[chain_id] = mine
        return ahead, mine

    def _leave(self, chain_id: str, ahead: asyncio.Future[None] | None, mine: asyncio.Future[None]) -> None:
        """Pass the chain on: at once if the turn ahead has ended, else when it does."""

        def release(_: object = None) -> None:
            if not mine.done():
                mine.set_result(None)
            if self._tails.get(chain_id) is mine:
                del self._tails[chain_id]

        if ahead is None or ahead.done():
            release()
        else:
            ahead.add_done_callback(release)


class ChainTurn:
    """One turn of a chain: waits for the chain's earlier turns, then runs a core turn (REQ-CHAIN-3 to 8)."""

    def __init__(
        self,
        chains: Chains,
        engine: Engine,
        chain: Chain,
        message: Message,
        provider: Provider,
        cwd: str | os.PathLike[str],
        prompt: str,
        tag: dict[str, Any],
        kwargs: dict[str, Any],
    ) -> None:
        self._chains, self._engine, self._chain, self._message = chains, engine, chain, message
        self._provider, self._cwd, self._prompt, self._tag, self._kwargs = provider, cwd, prompt, tag, kwargs
        self._turn: Turn | None = None
        self._place: tuple[asyncio.Future[None] | None, asyncio.Future[None]] | None = None

    async def __aenter__(self) -> ChainTurn:
        if self._place is not None:
            raise TelevibeError("a chain turn is entered once")
        store, chain_id = self._chains.store, self._chain.id
        ahead, mine = self._place = self._chains._enqueue(chain_id)  # before any await: entry order is line order
        try:
            await store.link(self._message.chat.id, self._message.message_id, chain_id)
            if ahead is not None:
                await asyncio.shield(ahead)  # shielded: cancelling this wait must not cancel the turn ahead
            dump = await store.session_of(chain_id)  # read now, not on entering (REQ-CHAIN-5)
            session = self._provider.load_session(dump) if dump is not None else self._provider.new_session(self._cwd)
            turn = self._engine.turn(self._prompt, session=session, tag=self._tag, **self._kwargs)
            await turn.__aenter__()
        except BaseException:
            self._chains._leave(chain_id, ahead, mine)
            raise
        self._turn = turn
        return self

    async def __aexit__(self, *exc_info: object) -> None:
        turn, place = self._entered(), self._place
        assert place is not None
        try:
            await turn.__aexit__(*exc_info)
        finally:
            self._chains._leave(self._chain.id, *place)

    @property
    def id(self) -> str:
        return self._entered().id

    def cancel(self) -> None:
        self._entered().cancel()

    def __aiter__(self) -> AsyncIterator[Event]:
        return self._events(self._entered().__aiter__())

    def _entered(self) -> Turn:
        if self._turn is None:
            raise TelevibeError("enter the chain turn with `async with` before using it")
        return self._turn

    async def _events(self, events: AsyncIterator[Event]) -> AsyncIterator[Event]:
        async for event in events:
            if isinstance(event, (Started, Done, Failed)) and event.session is not None:
                await self._save(event.session)  # before the caller sees the event (REQ-CHAIN-6)
            yield event

    async def _save(self, session: Session) -> None:
        try:
            await self._chains.store.save(self._chain.id, session.dump())
        except Exception:
            log.warning("televibe: could not save the session of chain %s", self._chain.id, exc_info=True)
