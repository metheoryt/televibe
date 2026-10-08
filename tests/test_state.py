import json
import subprocess
from datetime import UTC, datetime

import pytest

from conftest import Kit, collect, marker_files, until, wait_file
from televibe.engine import Engine
from televibe.errors import TelevibeError
from televibe.events import Done, Started
from televibe.markers import Markers
from televibe.procinfo import start_time


def _old_marker(kit: Kit, turn_id: str, pgid: int | None, proc_start: str | None) -> None:
    markers = Markers(kit.tmp / "state")
    markers.open()
    session = kit.provider.resume_session(kit.cwd, "old-session")
    markers.write(
        turn_id,
        {
            "turn_id": turn_id, "tag": {"chat": 7}, "session": session.dump(),
            "started_at": datetime.now(UTC).isoformat(), "pgid": pgid, "proc_start": proc_start,
            "session_id": "old-session",
        },
    )


async def test_marker_contents_while_running(engine, kit: Kit):
    """REQ-STATE-1, REQ-STATE-6: the marker holds id, tag, session, start, pgid, process start, session id — no secrets."""
    env = kit.env(mode="hang", after=kit.started_after(), child_pid=kit.tmp / "child.pid", SECRET_KNOB="turn-env-secret")
    async with engine.turn("hi", session=kit.session(), env=env, tag={"chat": 1}) as turn:
        assert isinstance(await anext(aiter(turn)), Started)
        await wait_file(kit.tmp / "child.pid")
        [path] = marker_files(kit.tmp)
        text = path.read_text()
        marker = json.loads(text)
        assert marker["turn_id"] == turn.id and path.name == f"{turn.id}.json"
        assert marker["tag"] == {"chat": 1}
        assert json.loads(marker["session"])["id"] == marker["session_id"]
        assert marker["pgid"] == kit.record()["pgid"] and marker["proc_start"] == start_time(marker["pgid"])
        datetime.fromisoformat(marker["started_at"])
        assert "s3cret-credential" not in text and "turn-env-secret" not in text
        turn.cancel()


async def test_marker_outlives_the_terminal_event(engine, kit: Kit):
    """REQ-STATE-2: the marker is removed when the context exits, not when Done is produced."""
    async with engine.turn("hi", session=kit.session(), env=kit.env()) as turn:
        events = await collect(turn)
        assert isinstance(events[-1], Done)
        assert len(marker_files(kit.tmp)) == 1
    assert marker_files(kit.tmp) == []


async def test_stranded_kills_a_live_agent(kit: Kit):
    """REQ-STATE-3, REQ-STATE-4: a marker from an earlier process is reported, and its live agent killed."""
    agent = subprocess.Popen(["sleep", "60"], start_new_session=True)
    _old_marker(kit, "a" * 32, agent.pid, start_time(agent.pid))
    async with Engine(kit.tmp / "state") as engine:
        [stranded] = await engine.stranded()
    assert stranded.turn_id == "a" * 32 and stranded.tag == {"chat": 7} and stranded.killed is True
    assert stranded.session.id == "old-session" and stranded.session.cwd == kit.cwd.resolve()
    assert stranded.started_at.tzinfo is not None
    assert agent.wait(timeout=5) == -9


async def test_stranded_never_kills_a_recycled_pid(kit: Kit):
    """REQ-STATE-4: a pid whose start time differs is not killed; a dead pid is not killed either."""
    stranger = subprocess.Popen(["sleep", "60"], start_new_session=True)
    gone = subprocess.Popen(["true"], start_new_session=True)
    gone.wait()
    try:
        _old_marker(kit, "b" * 32, stranger.pid, "Thu Jan  1 00:00:00 1970")
        _old_marker(kit, "c" * 32, gone.pid, "Thu Jan  1 00:00:00 1970")
        _old_marker(kit, "d" * 32, None, None)
        async with Engine(kit.tmp / "state") as engine:
            stranded = await engine.stranded()
        assert sorted((s.turn_id[0], s.killed) for s in stranded) == [("b", False), ("c", False), ("d", False)]
        assert stranger.poll() is None
    finally:
        stranger.kill()
        stranger.wait()


async def test_live_turns_are_not_stranded(engine, kit: Kit):
    """REQ-STATE-3: stranded() reports only markers left by an earlier process."""
    env = kit.env(mode="hang", after=kit.started_after(), child_pid=kit.tmp / "child.pid")
    async with engine.turn("hi", session=kit.session(), env=env) as turn:
        assert isinstance(await anext(aiter(turn)), Started)
        assert await engine.stranded() == []
        with pytest.raises(TelevibeError):
            engine.forget(turn.id)
        turn.cancel()


async def test_markers_stay_until_forgotten(kit: Kit):
    """REQ-STATE-5: stranded() keeps returning a marker until forget() removes it."""
    _old_marker(kit, "e" * 32, None, None)
    async with Engine(kit.tmp / "state") as engine:
        assert [s.turn_id for s in await engine.stranded()] == ["e" * 32]
        assert [s.turn_id for s in await engine.stranded()] == ["e" * 32]
        engine.forget("e" * 32)
        assert await engine.stranded() == []


async def test_forget_rejects_a_path(kit: Kit):
    """REQ-STATE-5, REQ-API-2: a turn id is never used as a path."""
    victim = kit.tmp / "state" / "precious.json"
    async with Engine(kit.tmp / "state") as engine:
        victim.write_text("{}")
        for bad in ("../precious", "/etc/passwd", "", "A" * 32):
            with pytest.raises(TelevibeError):
                engine.forget(bad)
    assert victim.exists()


async def test_stranded_session_runs_after_rebinding(kit: Kit):
    """REQ-STATE-3: the stranded session is unbound; load_session(dump()) gives one a turn can run on."""
    _old_marker(kit, "f" * 32, None, None)
    async with Engine(kit.tmp / "state") as engine:
        [stranded] = await engine.stranded()
        with pytest.raises(TelevibeError, match="load_session"):
            engine.turn("again", session=stranded.session)
        session = kit.provider.load_session(stranded.session.dump())
        async with engine.turn("again", session=session, env=kit.env("resume.jsonl")) as turn:
            events = await collect(turn)
        assert isinstance(events[-1], Done)


async def test_one_engine_per_state_dir(kit: Kit):
    """REQ-STATE-3: a second engine on a state_dir in use raises, so stranded() never kills another engine's live turn."""
    async with Engine(kit.tmp / "state"):
        with pytest.raises(TelevibeError, match="state_dir"):
            async with Engine(kit.tmp / "state"):
                pass
    async with Engine(kit.tmp / "state"):
        pass
