import asyncio

from televibe.scheduler import Scheduler, Ticket


async def _pending(scheduler, ticket, log, name):
    task = asyncio.create_task(scheduler.acquire(ticket, lambda: log.append(name)))
    await asyncio.sleep(0)
    return task


async def test_slot_limit():
    """REQ-QUEUE-1: at most max_concurrent run at once."""
    s, log = Scheduler(2), []
    a, b, c = Ticket(None, None), Ticket(None, None), Ticket(None, None)
    assert await s.acquire(a, lambda: log.append("a"))
    assert await s.acquire(b, lambda: log.append("b"))
    waiting = await _pending(s, c, log, "c")
    assert log == ["c"] and not waiting.done()
    s.release(a)
    assert await waiting is True


async def test_lane_runs_alone():
    """REQ-QUEUE-2: a turn does not start while another of its lane runs."""
    s, log = Scheduler(5), []
    assert await s.acquire(Ticket("kb", None), lambda: log.append("a"))
    waiting = await _pending(s, Ticket("kb", None), log, "b")
    assert await s.acquire(Ticket("other", None), lambda: log.append("c"))
    assert log == ["b"] and not waiting.done()


async def test_session_runs_alone():
    """REQ-QUEUE-3, REQ-SESSION-7: never two turns on one session id at once."""
    s, log = Scheduler(5), []
    key = ("claude", "/acc", "S")
    first = Ticket(None, key)
    assert await s.acquire(first, lambda: log.append("a"))
    waiting = await _pending(s, Ticket(None, key), log, "b")
    assert log == ["b"]
    s.release(first)
    assert await waiting is True


async def test_blocked_turn_does_not_hold_back_others():
    """REQ-QUEUE-4: when a slot frees, the oldest turn whose lane and session are free starts."""
    s, log = Scheduler(2), []
    assert await s.acquire(Ticket("kb", None), lambda: None)
    other = Ticket(None, None)
    assert await s.acquire(other, lambda: None)
    blocked = await _pending(s, Ticket("kb", None), log, "blocked")
    free = await _pending(s, Ticket(None, None), log, "free")
    s.release(other)
    await asyncio.sleep(0)
    assert free.done() and await free is True
    assert not blocked.done()


async def test_withdraw_and_stray_release():
    """REQ-CANCEL-2: a queued turn can be removed; releasing what is not held does nothing."""
    s = Scheduler(1)
    held = Ticket(None, None)
    assert await s.acquire(held, lambda: None)
    queued = Ticket(None, None)
    waiting = await _pending(s, queued, [], "q")
    s.withdraw(queued)
    assert await waiting is False
    s.release(queued)
    s.release(held)
    s.release(held)
    assert await s.acquire(Ticket(None, None), lambda: None)
