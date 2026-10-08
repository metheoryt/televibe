"""Reply chains: one session per conversation that follows replies (section 12, CHAIN)."""

from __future__ import annotations

import uuid
from dataclasses import dataclass

from aiogram.types import Message

from televibe.errors import TelevibeError
from televibe.telegram.store import ChainStore


@dataclass(frozen=True, slots=True)
class Chain:
    id: str


class Chains:
    """Finds chains and runs their turns. A bot keeps one Chains for its lifetime: the order of turns lives here."""

    def __init__(self, store: ChainStore) -> None:
        self.store = store

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
