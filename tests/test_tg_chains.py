import pytest

from televibe.errors import TelevibeError
from televibe.telegram.chains import Chain, Chains
from televibe.telegram.store import MemoryChainStore
from tgkit import message


async def test_find_follows_the_reply():
    """REQ-CHAIN-1: find returns the chain of the replied-to message, None otherwise."""
    store = MemoryChainStore()
    chains = Chains(store)
    await store.link(-1001, 10, "c1")
    assert await chains.find(message(message_id=11, reply_to=message(message_id=10))) == Chain("c1")
    assert await chains.find(message(message_id=12)) is None
    assert await chains.find(message(message_id=13, reply_to=message(message_id=99))) is None


async def test_find_is_per_chat():
    """REQ-CHAIN-1: the same message id in another chat is another message."""
    store = MemoryChainStore()
    await store.link(-1001, 10, "c1")
    other = message(chat_id=-2002, message_id=11, reply_to=message(chat_id=-2002, message_id=10))
    assert await Chains(store).find(other) is None


async def test_new_chain_has_a_fresh_id_and_stores_nothing():
    """REQ-CHAIN-1: new() returns a chain with a fresh id and writes nothing to the store."""
    store = MemoryChainStore()
    chains = Chains(store)
    a, b = chains.new(), chains.new()
    assert a.id and b.id and a != b
    assert store.links == {} and store.sessions == {}


def test_fork_raises():
    """REQ-CHAIN-2: forking needs core support that v1 lacks."""
    chains = Chains(MemoryChainStore())
    with pytest.raises(TelevibeError, match="fork"):
        chains.fork(chains.new())


async def test_link_the_answer_so_a_reply_to_it_continues():
    """REQ-CHAIN-9: link(chain, sent) makes a reply to the bot's answer find the chain."""
    chains = Chains(MemoryChainStore())
    chain = chains.new()
    sent = message(message_id=500)
    await chains.link(chain, sent)
    assert await chains.find(message(message_id=501, reply_to=sent)) == chain


async def test_memory_store_round_trip():
    """REQ-CHAIN-1: MemoryChainStore keeps links and sessions."""
    store = MemoryChainStore()
    assert await store.chain_of(1, 2) is None and await store.session_of("c") is None
    await store.link(1, 2, "c")
    await store.save("c", "dump")
    assert await store.chain_of(1, 2) == "c" and await store.session_of("c") == "dump"
