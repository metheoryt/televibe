import inspect
import os
from types import MappingProxyType

import pytest

from conftest import Kit, collect, run
from televibe.access import Access
from televibe.engine import Engine
from televibe.errors import TelevibeError
from televibe.events import Done, Failed, FailReason, Message, Queued, Started, ToolUse


async def test_successful_turn(engine, kit: Kit):
    """REQ-TURN-1, REQ-TURN-3, REQ-TURN-8, REQ-TURN-9, REQ-SESSION-6: Started first, then progress, then Done."""
    events = await run(engine, kit)
    assert isinstance(events[0], Started) and events[0].session.id and events[0].session.started
    assert sum(isinstance(e, Started) for e in events) == 1
    assert not any(isinstance(e, Queued) for e in events)
    assert any(isinstance(e, ToolUse) for e in events) and any(isinstance(e, Message) for e in events)
    done = events[-1]
    assert isinstance(done, Done) and done.text == kit.expected_text() and done.usage
    assert done.session == events[0].session


async def test_prompt_on_stdin_then_closed(engine, kit: Kit):
    """REQ-RUN-1: the prompt is written to stdin and stdin is closed (the fake reads to EOF)."""
    events = await run(engine, kit, prompt="say hi", env=kit.env(), timeout_s=10)
    assert isinstance(events[-1], Done)
    assert kit.record()["prompt"] == "say hi"


async def test_large_unicode_prompt_arrives_whole(engine, kit: Kit):
    """REQ-RUN-1: a prompt bigger than a pipe buffer, with non-ASCII text, arrives byte-exact."""
    prompt = "Привет, мир ✓ " * 80_000  # ~1.3 MB in UTF-8
    events = await run(engine, kit, prompt=prompt, env=kit.env(), timeout_s=30)
    assert isinstance(events[-1], Done)
    assert kit.record()["prompt"] == prompt


async def test_runs_in_the_session_cwd(engine, kit: Kit):
    """REQ-SESSION-4: the process cwd is the session's cwd; Codex also gets -C."""
    await run(engine, kit, env=kit.env())
    record = kit.record()
    assert record["cwd"] == str(kit.cwd.resolve())
    if kit.kind == "codex":
        assert record["argv"][record["argv"].index("-C") + 1] == str(kit.cwd.resolve())


async def test_environment_is_built_from_the_allowlist(engine, kit: Kit, monkeypatch):
    """REQ-ENV-1, REQ-ACCOUNT-2, REQ-ACCOUNT-3: allowlist, account var, credentials, then the turn's env."""
    monkeypatch.setenv("LEAKY_SECRET", "nope")
    monkeypatch.setenv("LC_ALL", "C")
    await run(engine, kit, env={**kit.env(), "FAKE_CREDENTIAL": "turn-wins"})
    env = kit.record()["env"]
    assert "LEAKY_SECRET" not in env and env["LC_ALL"] == "C"
    assert env[kit.provider.account_var] == str(kit.provider.account.home)
    assert env["FAKE_CREDENTIAL"] == "turn-wins"
    allowed = {"PATH", "HOME", "USER", "LOGNAME", "SHELL", "LANG", "TERM", "TMPDIR", "TZ", kit.provider.account_var}
    unexpected = {k for k in env if k not in allowed and not k.startswith(("LC_", "FAKE_", "__CF"))}
    assert unexpected == set()


async def test_second_turn_resumes(engine, kit: Kit):
    """REQ-SESSION-2, REQ-CLAUDE-1, REQ-CODEX-1: the session from Done resumes by id."""
    first = (await run(engine, kit))[-1]
    await run(engine, kit, session=first.session, env=kit.env("resume.jsonl"))
    argv = kit.record()["argv"]
    if kit.kind == "claude":
        assert argv[argv.index("--resume") + 1] == first.session.id
    else:
        assert argv[-3:] == ["resume", first.session.id, "-"]


async def test_crash_mid_stream_keeps_partial(engine, kit: Kit):
    """REQ-TURN-4, REQ-RUN-5: no terminal record → provider_error, with the session and the messages so far."""
    events = await run(engine, kit, env=kit.env(after=kit.terminal_index(), exit=3))
    failed = events[-1]
    assert isinstance(failed, Failed) and failed.reason is FailReason.PROVIDER_ERROR
    assert failed.session == events[0].session
    messages = [e.text for e in events if isinstance(e, Message)]
    assert messages and failed.partial == "\n\n".join(messages)
    assert "3" in failed.detail


async def test_stderr_tail_is_the_detail(engine, kit: Kit):
    """REQ-RUN-5: detail carries the last 4 KiB of stderr."""
    stderr = kit.tmp / "stderr.txt"
    stderr.write_text("x" * 10_000 + "ошибка в конце: THE END")
    events = await run(engine, kit, env=kit.env(after=1, exit=3, stderr=stderr))
    failed = events[-1]
    assert failed.reason is FailReason.PROVIDER_ERROR
    assert failed.detail.endswith("THE END") and len(failed.detail.encode()) <= 4096


async def test_stderr_flood_does_not_block(engine, kit: Kit):
    """REQ-RUN-2: stderr is drained while stdout is read."""
    events = await run(engine, kit, env=kit.env(mode="flood"), timeout_s=20)
    assert isinstance(events[-1], Done)


async def test_broken_lines_do_not_end_the_turn(engine, kit: Kit):
    """REQ-RUN-3: a non-JSON line, a JSON array and a line over 8 MiB are skipped."""
    events = await run(engine, kit, env=kit.env(mode="badlines"), timeout_s=30)
    assert isinstance(events[-1], Done)


async def test_spawn_error_is_an_event(engine, kit: Kit, monkeypatch, tmp_path):
    """REQ-RUN-4: a process that cannot start ends in Failed(spawn_error), never an exception."""
    empty = tmp_path / "empty-bin"
    empty.mkdir()
    monkeypatch.setenv("PATH", str(empty))
    events = await run(engine, kit)
    assert events[-1].reason is FailReason.SPAWN_ERROR and events[-1].session is None
    monkeypatch.undo()
    gone = kit.provider.new_session(tmp_path / "deleted-checkout")
    events = await run(engine, kit, session=gone)
    assert events[-1].reason is FailReason.SPAWN_ERROR and "deleted-checkout" in events[-1].detail


async def test_session_lost(engine, kit: Kit):
    """REQ-RUN-6, REQ-TURN-7: the recorded missing-session output is session_lost."""
    session = kit.provider.resume_session(kit.cwd, "11111111-2222-3333-4444-555555555555")
    events = await run(engine, kit, session=session, env=kit.env("lost.jsonl", stderr=kit.fixture("lost.stderr"), exit=1))
    assert isinstance(events[-1], Failed) and events[-1].reason is FailReason.SESSION_LOST


async def test_error_result_without_lost_stderr(engine, kit: Kit):
    """REQ-TURN-7, REQ-RUN-6: an error without the recorded stderr is provider_error, never Done."""
    session = kit.provider.resume_session(kit.cwd, "11111111-2222-3333-4444-555555555555")
    events = await run(engine, kit, session=session, env=kit.env("lost.jsonl", exit=1))
    assert isinstance(events[-1], Failed) and events[-1].reason is FailReason.PROVIDER_ERROR


async def test_unentered_turn_starts_nothing(engine, kit: Kit):
    """REQ-TURN-6: building a Turn starts nothing; iterating it without entering raises."""
    turn = engine.turn("hi", session=kit.session(), env=kit.env())
    with pytest.raises(TelevibeError):
        turn.__aiter__()
    assert not (kit.tmp / "record.json").exists()


async def test_turn_is_iterated_once(engine, kit: Kit):
    """REQ-TURN-5: a second iteration raises."""
    async with engine.turn("hi", session=kit.session(), env=kit.env()) as turn:
        await collect(turn)
        with pytest.raises(TelevibeError):
            turn.__aiter__()


@pytest.mark.parametrize(
    ("kwargs", "match"),
    [
        ({"env": {"OPENAI_API_KEY": "x"}}, "OPENAI_API_KEY"),
        ({"env": {"ANTHROPIC_API_KEY": "x"}}, "ANTHROPIC_API_KEY"),
        ({"env": {"CODEX_HOME": "/x"}}, "CODEX_HOME"),
        ({"env": {"CLAUDE_CONFIG_DIR": "/x"}}, "CLAUDE_CONFIG_DIR"),
        ({"timeout_s": 0}, "timeout_s"),
        ({"timeout_s": -1}, "timeout_s"),
        ({"timeout_s": True}, "timeout_s"),
        ({"timeout_s": float("inf")}, "timeout_s"),
        ({"tag": {"x": object()}}, "tag"),
        ({"add_dirs": "/repo"}, "add_dirs"),
        ({"lane": 5}, "lane"),
        ({"model": 5}, "model"),
        ({"instructions": 5}, "instructions"),
        ({"access": "read_only"}, "access"),
    ],
)
async def test_misuse_raises_at_the_call(engine, kit: Kit, kwargs, match):
    """REQ-API-2, REQ-ENV-2, REQ-ENV-3: misuse raises TelevibeError before any process starts."""
    with pytest.raises(TelevibeError, match=match):
        engine.turn("hi", session=kit.session(), **kwargs)


async def test_bad_prompt_and_session(engine, kit: Kit):
    """REQ-API-2, REQ-API-3: prompt must be a non-empty str; session must be a Session."""
    for prompt in ("", 5, None):
        with pytest.raises(TelevibeError, match="prompt"):
            engine.turn(prompt, session=kit.session())
    with pytest.raises(TelevibeError, match="session"):
        engine.turn("hi", session="not a session")


async def test_unsupported_access(engine, kit: Kit):
    """REQ-ACCESS-1: a level the provider does not declare raises at the call."""
    if kit.kind == "claude":
        with pytest.raises(TelevibeError):
            engine.turn("hi", session=kit.session(), access=Access.WORKSPACE_WRITE)
    else:
        engine.turn("hi", session=kit.session(), access=Access.WORKSPACE_WRITE)


async def test_defaults(engine, kit: Kit):
    """REQ-API-3, REQ-ACCESS-4: the turn's parameters and defaults; READ_ONLY unless asked."""
    params = inspect.signature(Engine.turn).parameters
    assert {name: p.default for name, p in params.items() if name not in ("self", "prompt", "session")} == {
        "access": Access.READ_ONLY, "lane": None, "model": None, "instructions": None,
        "env": MappingProxyType({}), "add_dirs": (), "timeout_s": 900, "tag": MappingProxyType({}),
    }
    await run(engine, kit, env=kit.env())
    argv = kit.record()["argv"]
    assert ("plan" in argv) if kit.kind == "claude" else ("read-only" in argv)


async def test_engine_must_be_open(tmp_path, kit: Kit):
    """REQ-QUEUE-6: a turn on an engine that is not entered, or already closed, raises."""
    engine = Engine(tmp_path / "state")
    with pytest.raises(TelevibeError):
        engine.turn("hi", session=kit.session())
    async with engine:
        turn = engine.turn("hi", session=kit.session(), env=kit.env())
    with pytest.raises(TelevibeError):
        engine.turn("hi", session=kit.session())
    with pytest.raises(TelevibeError):
        await turn.__aenter__()
    with pytest.raises(TelevibeError):
        async with engine:
            pass


async def test_credentials_never_logged(engine, kit: Kit, caplog):
    """REQ-ACCOUNT-3: credentials are never logged or put in an event."""
    caplog.set_level("DEBUG", logger="televibe")
    events = await run(engine, kit, env=kit.env(after=1, exit=3))
    assert "s3cret-credential" not in caplog.text
    assert all("s3cret-credential" not in repr(e) for e in events)


@pytest.mark.parametrize(
    ("kwargs", "match"),
    [
        ({"prompt": "private words \ud800"}, "prompt"),
        ({"model": "a\0b"}, "model"),
        ({"instructions": "a\0b"}, "instructions"),
        ({"add_dirs": ["/a\0b"]}, "add_dirs"),
    ],
)
async def test_values_the_os_refuses_raise_at_the_call(engine, kit: Kit, kwargs, match):
    """REQ-API-2: a prompt that is not UTF-8 text, or a NUL in a command-line value, raises before anything starts."""
    kwargs = {"prompt": "hi", **kwargs}
    with pytest.raises(TelevibeError, match=match) as raised:
        engine.turn(kwargs.pop("prompt"), session=kit.session(), env=kit.env(), **kwargs)
    assert "private words" not in str(raised.value)  # the prompt is never echoed


async def test_a_failed_marker_update_does_not_end_the_turn(engine, kit: Kit, monkeypatch, caplog):
    """REQ-TURN-1, REQ-STATE-1: a marker that cannot be updated is logged; the turn still finishes."""
    real_write = engine._markers.write

    def write(turn_id, data):
        if data.get("session_id"):  # the update at Started
            raise OSError("disk full")
        real_write(turn_id, data)

    monkeypatch.setattr(engine._markers, "write", write)
    events = await run(engine, kit, session=kit.session())
    assert isinstance(events[0], Started) and isinstance(events[-1], Done)
    assert "disk full" in caplog.text


async def test_a_parser_bug_is_reported_not_swallowed(engine, kit: Kit, monkeypatch, caplog):
    """REQ-TURN-1: an error inside televibe while reading ends the turn as an internal error, and is logged."""
    real_parser = kit.provider.parser

    def broken(session):
        parser = real_parser(session)

        def feed(record):
            raise RuntimeError("parser bug")

        parser.feed = feed
        return parser

    monkeypatch.setattr(kit.provider, "parser", broken)
    events = await run(engine, kit)
    assert events[-1].reason is FailReason.PROVIDER_ERROR and "parser bug" in events[-1].detail
    assert "parser bug" in caplog.text
