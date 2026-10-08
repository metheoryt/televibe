import asyncio
import json

import pytest
from conftest import Kit, marker_files, until
from tgkit import message

from televibe.errors import TelevibeError
from televibe.events import Done, Failed, FailReason, Started
from televibe.telegram.chains import Chains
from televibe.telegram.store import MemoryChainStore


class RecordingStore(MemoryChainStore):
    """Records saves; can fail or slow down any method."""

    def __init__(self, *, fail: tuple[str, ...] = (), slow_link_for: int | None = None) -> None:
        super().__init__()
        self.saves: list[str] = []
        self.fail = set(fail)
        self.slow_link_for = slow_link_for

    async def link(self, chat_id, message_id, chain_id):
        if "link" in self.fail:
            raise OSError("store is down")
        if message_id == self.slow_link_for:
            await asyncio.sleep(0.3)
        await super().link(chat_id, message_id, chain_id)

    async def save(self, chain_id, session):
        self.saves.append(session)
        if "save" in self.fail:
            raise OSError("store is down")
        await super().save(chain_id, session)


def _resumed(kit: Kit, record: str) -> bool:
    argv = kit.record(record)["argv"]
    return "--resume" in argv if kit.kind == "claude" else "resume" in argv


def _started(events) -> Started:
    return next(e for e in events if isinstance(e, Started))


async def _run(chains, engine, kit: Kit, chain, message_id: int, name: str, order: list | None = None, **knobs):
    """One chain turn to its end; `name` names the fake's record file and the prompt."""
    async with chains.turn(
        engine, chain, message(message_id=message_id), provider=kit.provider, cwd=kit.cwd, prompt=name,
        env=kit.env(record=f"{name}.json", **knobs),
    ) as turn:
        events = [e async for e in turn]
    if order is not None:
        order.append(name)
    return events


async def test_turns_of_one_chain_run_in_order_and_resume_its_session(engine, kit: Kit):
    """REQ-CHAIN-4, REQ-CHAIN-5: one at a time in entry order; each waiting turn resumes the session the first started."""
    chains = Chains(MemoryChainStore())
    chain = chains.new()
    order: list[str] = []
    a = asyncio.create_task(_run(chains, engine, kit, chain, 1, "a", order, sleep=0.5))
    b = asyncio.create_task(_run(chains, engine, kit, chain, 2, "b", order))
    c = asyncio.create_task(_run(chains, engine, kit, chain, 3, "c", order))
    ea, eb, ec = await asyncio.gather(a, b, c)
    assert order == ["a", "b", "c"]
    assert all(isinstance(events[-1], Done) for events in (ea, eb, ec))
    assert _started(ea).session.id == _started(eb).session.id == _started(ec).session.id
    assert not _resumed(kit, "a.json") and _resumed(kit, "b.json") and _resumed(kit, "c.json")


async def test_different_chains_do_not_wait_for_each_other(engine, kit: Kit):
    """REQ-CHAIN-4: a slow chain does not hold up another chain."""
    chains = Chains(MemoryChainStore())
    order: list[str] = []
    slow = asyncio.create_task(_run(chains, engine, kit, chains.new(), 1, "x", order, sleep=1.0))
    fast = asyncio.create_task(_run(chains, engine, kit, chains.new(), 2, "y", order))
    await asyncio.gather(slow, fast)
    assert order == ["y", "x"]


async def test_entering_links_the_message_and_passes_kwargs_on(engine, kit: Kit):
    """REQ-CHAIN-3: the message is linked on entering; other kwargs reach engine.turn unchanged."""
    store = MemoryChainStore()
    chains = Chains(store)
    chain = chains.new()
    async with chains.turn(
        engine, chain, message(message_id=42), provider=kit.provider, cwd=kit.cwd, prompt="hi",
        env=kit.env(mode="hang", after=kit.started_after(), child_pid=kit.tmp / "child.pid"), timeout_s=0.5,
    ) as turn:
        assert await store.chain_of(-1001, 42) == chain.id
        events = [e async for e in turn]
    assert isinstance(events[-1], Failed) and events[-1].reason is FailReason.TIMEOUT


def test_session_kwarg_is_refused(kit: Kit):
    """REQ-CHAIN-3: the chain chooses the session; passing one raises at the call."""
    chains = Chains(MemoryChainStore())
    with pytest.raises(TelevibeError, match="session"):
        chains.turn(None, chains.new(), message(), provider=kit.provider, cwd=kit.cwd, prompt="hi", session=kit.session())


async def test_new_chain_starts_a_new_session_in_cwd(engine, kit: Kit):
    """REQ-CHAIN-5: a chain with no session gets provider.new_session(cwd)."""
    chains = Chains(MemoryChainStore())
    events = await _run(chains, engine, kit, chains.new(), 1, "a")
    assert _started(events).session.cwd == kit.cwd.resolve()
    assert not _resumed(kit, "a.json")


async def test_session_saved_on_started_and_on_terminal(engine, kit: Kit):
    """REQ-CHAIN-6: saved on Started and again on Done."""
    store = RecordingStore()
    chains = Chains(store)
    chain = chains.new()
    events = await _run(chains, engine, kit, chain, 1, "a")
    sid = _started(events).session.id
    assert len(store.saves) == 2
    assert all(json.loads(dump)["id"] == sid for dump in store.saves)
    assert json.loads(store.sessions[chain.id])["started"] is True


async def test_session_saved_on_started_survives_a_failed_turn(engine, kit: Kit):
    """REQ-CHAIN-6: a turn that fails after Started still leaves its session in the store."""
    store = RecordingStore()
    chains = Chains(store)
    chain = chains.new()
    events = await _run(chains, engine, kit, chain, 1, "a", after=kit.started_after(), exit=3)
    assert isinstance(events[-1], Failed)
    assert json.loads(store.sessions[chain.id])["id"] == _started(events).session.id


async def test_tag_carries_the_chain_and_the_message(engine, kit: Kit):
    """REQ-CHAIN-7: the turn's tag gets chain, chat_id and message_id next to the caller's keys."""
    chains = Chains(MemoryChainStore())
    chain = chains.new()
    async with chains.turn(
        engine, chain, message(message_id=42), provider=kit.provider, cwd=kit.cwd, prompt="hi",
        env=kit.env(mode="hang", after=kit.started_after(), child_pid=kit.tmp / "child.pid"), tag={"who": "ann"},
    ):
        await until(lambda: bool(marker_files(kit.tmp)))
        tag = json.loads(marker_files(kit.tmp)[0].read_text())["tag"]
    assert tag == {"who": "ann", "chain": chain.id, "chat_id": -1001, "message_id": 42}


@pytest.mark.parametrize("key", ["chain", "chat_id", "message_id"])
def test_tag_key_clash_raises(kit: Kit, key):
    """REQ-CHAIN-7: a caller tag that already has one of the chain's keys raises."""
    chains = Chains(MemoryChainStore())
    with pytest.raises(TelevibeError, match=key):
        chains.turn(None, chains.new(), message(), provider=kit.provider, cwd=kit.cwd, prompt="hi", tag={key: 1})


async def test_store_error_before_the_turn_propagates_and_frees_the_chain(engine, kit: Kit):
    """REQ-CHAIN-8: a store error on entering reaches the caller; the chain is not left locked."""
    store = RecordingStore(fail=("link",))
    chains = Chains(store)
    chain = chains.new()
    with pytest.raises(OSError, match="store is down"):
        await _run(chains, engine, kit, chain, 1, "a")
    store.fail.clear()
    events = await asyncio.wait_for(_run(chains, engine, kit, chain, 2, "b"), 10)
    assert isinstance(events[-1], Done)


async def test_failed_saves_do_not_end_the_turn_and_the_next_starts_over(engine, kit: Kit, caplog):
    """REQ-CHAIN-8: a save error is logged, the answer still arrives, and the next turn starts a new session."""
    store = RecordingStore(fail=("save",))
    chains = Chains(store)
    chain = chains.new()
    first = await _run(chains, engine, kit, chain, 1, "a")
    assert isinstance(first[-1], Done)
    assert "could not save" in caplog.text
    store.fail.clear()
    second = await _run(chains, engine, kit, chain, 2, "b")
    assert not _resumed(kit, "b.json")
    assert _started(second).session.id != _started(first).session.id


async def test_bot_error_inside_the_turn_frees_the_chain(engine, kit: Kit):
    """REQ-CHAIN-4 (review focus 1): the bot's own exception inside the block ends the turn and frees the chain."""
    chains = Chains(MemoryChainStore())
    chain = chains.new()
    with pytest.raises(RuntimeError, match="bot bug"):
        async with chains.turn(
            engine, chain, message(message_id=1), provider=kit.provider, cwd=kit.cwd, prompt="a",
            env=kit.env(mode="hang", after=kit.started_after(), child_pid=kit.tmp / "child.pid"),
        ):
            raise RuntimeError("bot bug")
    events = await asyncio.wait_for(_run(chains, engine, kit, chain, 2, "b"), 10)
    assert isinstance(events[-1], Done)


async def test_cancelled_waiting_turn_keeps_the_line_moving(engine, kit: Kit):
    """REQ-CHAIN-4, REQ-CHAIN-5 (review focus 2): cancelling a waiting turn neither stalls nor reorders the rest."""
    chains = Chains(MemoryChainStore())
    chain = chains.new()
    order: list[str] = []
    a = asyncio.create_task(_run(chains, engine, kit, chain, 1, "a", order, sleep=0.5))
    b = asyncio.create_task(_run(chains, engine, kit, chain, 2, "b", order))
    c = asyncio.create_task(_run(chains, engine, kit, chain, 3, "c", order))
    await asyncio.sleep(0.1)
    b.cancel()
    await asyncio.gather(a, c)
    with pytest.raises(asyncio.CancelledError):
        await b
    assert order == ["a", "c"]
    assert _resumed(kit, "c.json")


async def test_slow_link_does_not_reorder_the_chain(engine, kit: Kit):
    """REQ-CHAIN-4 (review focus 3): the turn that entered first runs first, even if its link is slow."""
    chains = Chains(RecordingStore(slow_link_for=1))
    chain = chains.new()
    order: list[str] = []
    a = asyncio.create_task(_run(chains, engine, kit, chain, 1, "a", order))
    b = asyncio.create_task(_run(chains, engine, kit, chain, 2, "b", order))
    await asyncio.gather(a, b)
    assert order == ["a", "b"]
    assert _resumed(kit, "b.json")


async def test_corrupt_session_propagates_and_frees_the_chain(engine, kit: Kit):
    """REQ-CHAIN-5, REQ-CHAIN-8 (review focus 4): a bad stored session raises TelevibeError; the chain is not left locked."""
    store = MemoryChainStore()
    chains = Chains(store)
    chain = chains.new()
    store.sessions[chain.id] = "not a session"
    with pytest.raises(TelevibeError, match="not a session dump"):
        await _run(chains, engine, kit, chain, 1, "a")
    del store.sessions[chain.id]
    events = await asyncio.wait_for(_run(chains, engine, kit, chain, 2, "b"), 10)
    assert isinstance(events[-1], Done)
