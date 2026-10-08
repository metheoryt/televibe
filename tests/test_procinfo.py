import asyncio
import os
import subprocess

from televibe.procinfo import group_alive, kill_group, start_time


async def wait_gone(pid: int, timeout: float = 5.0) -> bool:
    loop = asyncio.get_running_loop()
    deadline = loop.time() + timeout
    while loop.time() < deadline:
        try:
            os.kill(pid, 0)
        except ProcessLookupError:
            return True
        await asyncio.sleep(0.05)
    return False


def test_start_time_is_stable_and_none_when_gone():
    """REQ-STATE-4: a process's start time can be read twice and compared."""
    proc = subprocess.Popen(["sleep", "30"])
    try:
        first = start_time(proc.pid)
        assert first and start_time(proc.pid) == first
    finally:
        proc.kill()
        proc.wait()
    assert start_time(proc.pid) is None


async def test_kill_group_kills_the_agents_children():
    """REQ-CANCEL-1: SIGKILL to the group takes the commands the agent started with it."""
    proc = subprocess.Popen(
        ["sh", "-c", "sleep 60 & echo $!; wait"], start_new_session=True, stdout=subprocess.PIPE, text=True
    )
    child = int(proc.stdout.readline())
    assert group_alive(proc.pid)
    kill_group(proc.pid)
    proc.wait(timeout=5)
    assert await wait_gone(child)
    assert not group_alive(proc.pid)


def test_kill_group_of_nothing_is_quiet():
    """REQ-CANCEL-2: killing what is already gone is safe."""
    proc = subprocess.Popen(["true"], start_new_session=True)
    proc.wait()
    kill_group(proc.pid)
