import asyncio

from conftest import Kit, until
from televibe.engine import Engine
from televibe.events import Done, Queued, Started


async def _consume(engine, log, name, **kwargs):
    async with engine.turn(name, **kwargs) as turn:
        async for event in turn:
            log.append((name, type(event).__name__))


def _index(log, name, kind):
    return log.index((name, kind))


async def test_queued_only_when_blocked_and_once(kit: Kit):
    """REQ-TURN-2, REQ-QUEUE-1: Queued appears only if the turn cannot start at once, and at most once."""
    log: list = []
    async with Engine(kit.tmp / "state", max_concurrent=1) as engine:
        first = asyncio.create_task(
            _consume(engine, log, "a", session=kit.session(), env=kit.env(sleep=0.5, record="a.json"))
        )
        await until(lambda: ("a", "Started") in log or kit.tmp.joinpath("a.json").exists())
        await _consume(engine, log, "b", session=kit.session(), env=kit.env(record="b.json"))
        await first
    assert ("a", "Queued") not in log
    assert log.count(("b", "Queued")) == 1
    assert _index(log, "a", "Done") < _index(log, "b", "Started")


async def test_one_turn_per_session(kit: Kit):
    """REQ-QUEUE-3, REQ-SESSION-7: a second turn on a busy session waits, even with free slots."""
    log: list = []
    session = kit.provider.resume_session(kit.cwd, "same-session")
    async with Engine(kit.tmp / "state", max_concurrent=4) as engine:
        await asyncio.gather(
            _consume(engine, log, "a", session=session, env=kit.env("resume.jsonl", sleep=0.5, record="a.json")),
            _consume(engine, log, "b", session=session, env=kit.env("resume.jsonl", record="b.json")),
        )
    assert ("b", "Queued") in log
    assert _index(log, "a", "Done") < _index(log, "b", "Started")


async def test_one_turn_per_lane(kit: Kit):
    """REQ-QUEUE-2: turns of one lane never overlap, even on different sessions."""
    log: list = []
    async with Engine(kit.tmp / "state", max_concurrent=4) as engine:
        await asyncio.gather(
            _consume(engine, log, "a", session=kit.session(), lane="kb", env=kit.env(sleep=0.5, record="a.json")),
            _consume(engine, log, "b", session=kit.session(), lane="kb", env=kit.env(record="b.json")),
            _consume(engine, log, "c", session=kit.session(), env=kit.env(record="c.json")),
        )
    assert _index(log, "a", "Done") < _index(log, "b", "Started")
    assert ("c", "Queued") not in log


async def test_blocked_turn_does_not_hold_back_an_unrelated_one(kit: Kit):
    """REQ-QUEUE-4: when a slot frees, the oldest turn whose lane and session are free starts."""
    log: list = []
    async with Engine(kit.tmp / "state", max_concurrent=2) as engine:
        lane_holder = asyncio.create_task(
            _consume(engine, log, "a", session=kit.session(), lane="kb", env=kit.env(sleep=2, record="a.json"))
        )
        short = asyncio.create_task(_consume(engine, log, "x", session=kit.session(), env=kit.env(sleep=0.3, record="x.json")))
        await until(lambda: (kit.tmp / "a.json").exists() and (kit.tmp / "x.json").exists())
        blocked = asyncio.create_task(_consume(engine, log, "b", session=kit.session(), lane="kb", env=kit.env(record="b.json")))
        free = asyncio.create_task(_consume(engine, log, "c", session=kit.session(), env=kit.env(record="c.json")))
        await free
        assert ("a", "Done") not in log  # c started and finished while a still held the lane and b waited
        await asyncio.gather(lane_holder, short, blocked)
    assert _index(log, "c", "Done") < _index(log, "b", "Started")


async def test_timeout_counts_from_process_start(kit: Kit):
    """REQ-QUEUE-5: time spent queued does not count toward timeout_s."""
    log: list = []
    async with Engine(kit.tmp / "state", max_concurrent=1) as engine:
        first = asyncio.create_task(_consume(engine, log, "a", session=kit.session(), env=kit.env(sleep=1.5, record="a.json")))
        await until(lambda: (kit.tmp / "a.json").exists())
        await _consume(engine, log, "b", session=kit.session(), env=kit.env(record="b.json"), timeout_s=1)
        await first
    assert ("b", "Queued") in log and ("b", "Done") in log


async def test_session_id_learned_mid_turn_is_locked(kit: Kit):
    """REQ-QUEUE-3, REQ-SESSION-7: a turn on the id a new session just reported waits for the turn that reported it."""
    log: list = []
    env = kit.env(mode="hang", after=kit.started_after(), child_pid=kit.tmp / "child.pid", record="a.json")
    async with Engine(kit.tmp / "state", max_concurrent=4) as engine:
        async with engine.turn("a", session=kit.session(), env=env) as first:
            started = await anext(aiter(first))
            assert isinstance(started, Started)
            async with engine.turn("b", session=started.session, env=kit.env("resume.jsonl", record="b.json")) as second:
                assert isinstance(await anext(aiter(second)), Queued)
                second.cancel()
            first.cancel()
    assert not (kit.tmp / "b.json").exists()
