import asyncio
import contextlib
import gc
import time

import pytest

from conftest import Kit, collect, marker_files, run, until, wait_dead, wait_file
from televibe.engine import Engine
from televibe.events import Done, Failed, FailReason, Queued, Started
from televibe.procinfo import group_alive


def _hang(kit: Kit, **extra) -> dict:
    return kit.env(mode="hang", after=kit.started_after(), child_pid=kit.tmp / "child.pid", **extra)


async def test_timeout_kills_the_process_group(engine, kit: Kit):
    """REQ-CANCEL-4, REQ-CANCEL-1, REQ-TURN-1: past timeout_s the whole group is killed; Failed(timeout)."""
    events = await run(engine, kit, env=_hang(kit), timeout_s=1)
    assert isinstance(events[0], Started)
    assert events[-1].reason is FailReason.TIMEOUT and events[-1].session == events[0].session
    assert await wait_dead(int((kit.tmp / "child.pid").read_text()))


async def test_cancel_while_running(engine, kit: Kit):
    """REQ-CANCEL-2: cancel is idempotent; a running turn is killed and ends Failed(cancelled)."""
    async with engine.turn("hi", session=kit.session(), env=_hang(kit)) as turn:
        events = []
        async for event in turn:
            events.append(event)
            if isinstance(event, Started):
                await wait_file(kit.tmp / "child.pid")
                turn.cancel()
                turn.cancel()
    assert events[-1].reason is FailReason.CANCELLED
    assert await wait_dead(int((kit.tmp / "child.pid").read_text()))
    turn.cancel()  # after the end: still safe


async def test_cancel_while_queued(kit: Kit):
    """REQ-CANCEL-2: before the process starts, cancel removes the turn from the queue."""
    async with Engine(kit.tmp / "state", max_concurrent=1) as engine:
        async with engine.turn("a", session=kit.session(), env=_hang(kit, record="a.json")) as first:
            assert isinstance(await anext(aiter(first)), Started)
            async with engine.turn("b", session=kit.session(), env=kit.env(record="b.json")) as second:
                events = []
                async for event in second:
                    events.append(event)
                    if isinstance(event, Queued):
                        second.cancel()
            assert [type(e) for e in events] == [Queued, Failed] and events[-1].reason is FailReason.CANCELLED
            assert not (kit.tmp / "b.json").exists()


async def test_cancel_before_entering(engine, kit: Kit):
    """REQ-CANCEL-2: a turn cancelled before it is entered ends Failed(cancelled) and spawns nothing."""
    turn = engine.turn("hi", session=kit.session(), env=kit.env())
    turn.cancel()
    async with turn:
        events = await collect(turn)
    assert [type(e) for e in events] == [Failed] and events[0].reason is FailReason.CANCELLED
    assert not (kit.tmp / "record.json").exists()


async def test_cancel_during_spawn(engine, kit: Kit, monkeypatch):
    """REQ-CANCEL-3: a cancel that lands while the process is being spawned kills it as soon as it exists."""
    real_spawn = asyncio.create_subprocess_exec
    seen = {}

    async def spawn_then_cancel(*args, **kwargs):
        proc = await real_spawn(*args, **kwargs)
        seen["pgid"] = proc.pid
        seen["turn"].cancel()  # televibe has not seen the pid yet
        return proc

    monkeypatch.setattr(asyncio, "create_subprocess_exec", spawn_then_cancel)
    turn = engine.turn("hi", session=kit.session(), env=_hang(kit))
    seen["turn"] = turn
    async with turn:
        events = await collect(turn)
    assert events[-1].reason is FailReason.CANCELLED
    await until(lambda: not group_alive(seen["pgid"]))


@pytest.mark.parametrize("how", ["break", "exception", "task_cancel", "no_iteration"])
async def test_leaving_the_context_kills_the_turn(engine, kit: Kit, how):
    """REQ-CANCEL-5, REQ-STATE-2: break, exception, cancelled task, or no iteration at all — the agent dies."""
    pid_file = kit.tmp / "child.pid"

    async def body():
        async with engine.turn("hi", session=kit.session(), env=_hang(kit)) as turn:
            if how == "no_iteration":
                await wait_file(pid_file)
                return
            async for event in turn:
                if isinstance(event, Started):
                    await wait_file(pid_file)
                    if how == "break":
                        break
                    if how == "exception":
                        raise RuntimeError("caller bug")
                    await asyncio.sleep(3600)

    task = asyncio.create_task(body())
    if how == "task_cancel":
        await wait_file(pid_file)
        await asyncio.sleep(0.1)
        task.cancel()
    with contextlib.suppress(RuntimeError, asyncio.CancelledError):
        await task
    assert await wait_dead(int(pid_file.read_text()))
    await until(lambda: marker_files(kit.tmp) == [])


async def test_garbage_collected_turn_is_killed(engine, kit: Kit, caplog):
    """REQ-CANCEL-6: a turn entered without `async with` and dropped is killed by a finalizer, with a warning."""
    pid_file = kit.tmp / "child.pid"
    turn = engine.turn("hi", session=kit.session(), env=_hang(kit))
    await turn.__aenter__()
    await wait_file(pid_file)
    del turn
    gc.collect()
    assert await wait_dead(int(pid_file.read_text()))
    assert "garbage-collected" in caplog.text
    await until(lambda: marker_files(kit.tmp) == [])


async def test_closing_the_engine_cancels_everything(kit: Kit):
    """REQ-CANCEL-7: closing the engine ends every queued and running turn with Failed(cancelled), 'engine closed'."""
    engine = Engine(kit.tmp / "state", max_concurrent=1)
    await engine.__aenter__()
    seen: dict[str, list] = {"a": [], "b": []}

    async def consume(name, env):
        async with engine.turn(name, session=kit.session(), env=env) as turn:
            async for event in turn:
                seen[name].append(event)

    first = asyncio.create_task(consume("a", _hang(kit, record="a.json")))
    await until(lambda: any(isinstance(e, Started) for e in seen["a"]))
    second = asyncio.create_task(consume("b", kit.env(record="b.json")))
    await until(lambda: any(isinstance(e, Queued) for e in seen["b"]))
    await engine.__aexit__(None, None, None)
    await asyncio.gather(first, second)
    for events in seen.values():
        assert isinstance(events[-1], Failed) and events[-1].reason is FailReason.CANCELLED
        assert "engine closed" in events[-1].detail


@pytest.mark.parametrize("ending", ["clean", "crash"])
async def test_background_child_does_not_hold_the_turn_open(engine, kit: Kit, ending):
    """REQ-RUN-7: a child that keeps the agent's pipes open neither delays the end nor survives it."""
    knobs = {"mode": "orphan", "child_pid": kit.tmp / "child.pid"}
    if ending == "crash":
        knobs.update(after=kit.started_after(), exit=3)
    began = time.monotonic()
    events = await run(engine, kit, env=kit.env(**knobs), timeout_s=60)
    assert time.monotonic() - began < 10
    assert isinstance(events[-1], Done if ending == "clean" else Failed)
    if ending == "crash":
        assert events[-1].reason is FailReason.PROVIDER_ERROR
    assert await wait_dead(int((kit.tmp / "child.pid").read_text()))
