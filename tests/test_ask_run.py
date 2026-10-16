"""One-shot calls against the fake claude (SPEC section 12, REQ-TEST-2)."""

import asyncio
import json
from pathlib import Path

import pytest
from conftest import read_fixture, wait_dead, wait_file

from televibe import Account, AskFailed, ClaudeCode, Engine, FailReason, Limits

SCHEMA = {"type": "object", "required": ["word"], "properties": {"word": {"type": "string"}}}
FIXTURES = Path(__file__).parent / "fixtures" / "claude"


@pytest.fixture
def claude(tmp_path, fake_path):
    return ClaudeCode(Account(tmp_path / "acc", credentials={"FAKE_CREDENTIAL": "s3cret"}))


def env(tmp_path, fixture="ask.jsonl", **knobs):
    out = {"FAKE_RECORD": str(tmp_path / "record.json")}
    if fixture is not None:
        out["FAKE_FIXTURE"] = str(FIXTURES / fixture)
    out.update({f"FAKE_{k.upper()}": str(v) for k, v in knobs.items()})
    return out


def record(tmp_path):
    return json.loads((tmp_path / "record.json").read_text())


async def test_ask_returns_the_structured_output(claude, tmp_path):
    """REQ-ASK-1, REQ-ASK-2, REQ-ASK-3: data is the result's structured_output; usage passed through;
    the fake saw the flags, the prompt on stdin, the account var and credential, and no API key."""
    answer = await claude.ask("Which word?", schema=SCHEMA, system="one word", model="sonnet", env=env(tmp_path))
    result = next(r for r in read_fixture("claude", "ask.jsonl") if r["type"] == "result")
    assert answer.data == result["structured_output"]
    assert answer.usage == result["usage"]
    rec = record(tmp_path)
    assert rec["prompt"] == "Which word?"
    assert rec["argv"][:5] == ["-p", "--output-format", "stream-json", "--verbose", "--json-schema"]
    assert "--tools" in rec["argv"] and rec["argv"][rec["argv"].index("--tools") + 1] == ""
    assert rec["argv"][-2:] == ["--system-prompt", "one word"]
    assert rec["env"]["CLAUDE_CONFIG_DIR"] == str(claude.account.home)
    assert rec["env"]["FAKE_CREDENTIAL"] == "s3cret"
    assert "ANTHROPIC_API_KEY" not in rec["env"]


async def test_ask_passes_the_schema_compact_and_utf8(claude, tmp_path):
    """REQ-ASK-2: the schema reaches argv as one compact JSON string with non-ASCII intact."""
    schema = {**SCHEMA, "description": "одно слово"}
    await claude.ask("hi", schema=schema, env=env(tmp_path))
    argv = record(tmp_path)["argv"]
    assert json.loads(argv[argv.index("--json-schema") + 1]) == schema
    assert "одно слово" in argv[argv.index("--json-schema") + 1]


async def test_ask_runs_in_a_fresh_directory_that_is_removed(claude, tmp_path):
    """REQ-ASK-3: cwd is an empty temporary directory of the call's own, gone afterwards."""
    await claude.ask("hi", schema=SCHEMA, env=env(tmp_path))
    cwd = Path(record(tmp_path)["cwd"])
    assert cwd != Path.cwd() and cwd != claude.account.home and not cwd.is_relative_to(tmp_path)
    assert not cwd.exists()


async def test_ask_reports_limits(claude, tmp_path):
    """REQ-ASK-1: the last Limits of the call rides on the answer; ask.jsonl has a rate_limit_event."""
    answer = await claude.ask("hi", schema=SCHEMA, env=env(tmp_path))
    records = read_fixture("claude", "ask.jsonl")
    if any(r["type"] == "rate_limit_event" for r in records):
        assert isinstance(answer.limits, Limits)
    else:
        assert answer.limits is None


async def test_ask_without_structured_output_is_provider_error(claude, tmp_path):
    """REQ-ASK-4: a result that has no structured_output (an ordinary turn's result) is provider_error."""
    with pytest.raises(AskFailed) as info:
        await claude.ask("hi", schema=SCHEMA, env=env(tmp_path, fixture="ok.jsonl"))
    assert info.value.reason is FailReason.PROVIDER_ERROR and "structured output" in info.value.detail


async def test_ask_refused_at_the_limit_is_usage_limit(claude, tmp_path):
    """REQ-ASK-4, REQ-CLAUDE-3: the recorded limit refusal is usage_limit."""
    with pytest.raises(AskFailed) as info:
        await claude.ask("hi", schema=SCHEMA, env=env(tmp_path, fixture="limit.jsonl", exit=1))
    assert info.value.reason is FailReason.USAGE_LIMIT


async def test_ask_exits_without_result_is_provider_error(claude, tmp_path):
    """REQ-ASK-4, REQ-RUN-5: no result record → provider_error with the stderr tail (a rejected schema looks like this)."""
    (tmp_path / "err.txt").write_text("Error: --json-schema is not valid JSON Schema\n")
    with pytest.raises(AskFailed) as info:
        await claude.ask("hi", schema=SCHEMA, env=env(tmp_path, fixture=None, exit=2, stderr=tmp_path / "err.txt"))
    assert info.value.reason is FailReason.PROVIDER_ERROR and "not valid JSON Schema" in info.value.detail


async def test_ask_spawn_error(tmp_path, monkeypatch):
    """REQ-ASK-4, REQ-RUN-4: a binary that cannot start is spawn_error, not an OSError."""
    monkeypatch.setenv("PATH", str(tmp_path / "nowhere"))
    claude = ClaudeCode(Account(tmp_path / "acc"))
    with pytest.raises(AskFailed) as info:
        await claude.ask("hi", schema=SCHEMA, env=env(tmp_path))
    assert info.value.reason is FailReason.SPAWN_ERROR


async def test_ask_timeout_kills_the_group(claude, tmp_path):
    """REQ-ASK-3, REQ-ASK-4, REQ-CANCEL-4: past timeout_s the call is timeout and the child it started is dead."""
    pidfile = tmp_path / "child.pid"
    with pytest.raises(AskFailed) as info:
        await claude.ask("hi", schema=SCHEMA, timeout_s=1, env=env(tmp_path, mode="hang", child_pid=pidfile, after=1))
    assert info.value.reason is FailReason.TIMEOUT
    assert await wait_dead(int(pidfile.read_text()))
    assert await wait_dead(record(tmp_path)["pid"])


async def test_ask_answer_before_the_deadline_is_kept(claude, tmp_path):
    """REQ-ASK-4, REQ-RUN-7: a result read in time is the answer even when the agent outlives timeout_s, as a turn ends Done."""
    pidfile = tmp_path / "child.pid"
    answer = await claude.ask("hi", schema=SCHEMA, timeout_s=1, env=env(tmp_path, mode="hang", child_pid=pidfile))
    assert answer.data == {"word": "Yes"}
    assert await wait_dead(record(tmp_path)["pid"])
    assert await wait_dead(int(pidfile.read_text()))


async def test_ask_kills_a_lingering_agent_after_the_grace(claude, tmp_path, monkeypatch):
    """REQ-ASK-3, REQ-RUN-7: after the result the agent gets EXIT_GRACE_S to exit, then the group is killed; the call does not wait for timeout_s."""
    import televibe.turn

    monkeypatch.setattr(televibe.turn, "EXIT_GRACE_S", 0.3)
    pidfile = tmp_path / "child.pid"
    started = asyncio.get_running_loop().time()
    answer = await claude.ask("hi", schema=SCHEMA, timeout_s=10, env=env(tmp_path, mode="hang", child_pid=pidfile))
    assert answer.data == {"word": "Yes"}
    assert asyncio.get_running_loop().time() - started < 5
    assert await wait_dead(record(tmp_path)["pid"])
    assert await wait_dead(int(pidfile.read_text()))


async def test_ask_cancel_kills_the_process(claude, tmp_path):
    """REQ-ASK-3, REQ-ASK-4: cancelling the awaiting task kills the process; CancelledError propagates, no AskFailed."""
    pidfile = tmp_path / "child.pid"
    task = asyncio.create_task(claude.ask("hi", schema=SCHEMA, env=env(tmp_path, mode="hang", child_pid=pidfile)))
    await wait_file(pidfile)  # the fake writes the record first, the child's pid last: wait for the child to exist
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert await wait_dead(record(tmp_path)["pid"])
    assert await wait_dead(int(pidfile.read_text()))


async def test_ask_waits_for_exit_then_kills(claude, tmp_path):
    """REQ-ASK-3, REQ-RUN-7: a child that holds stdout after the result does not keep the call open."""
    pidfile = tmp_path / "child.pid"
    answer = await claude.ask("hi", schema=SCHEMA, env=env(tmp_path, mode="orphan", child_pid=pidfile))
    assert answer.data
    assert await wait_dead(int(pidfile.read_text()))


async def test_ask_takes_no_slot_and_leaves_no_marker(claude, tmp_path):
    """REQ-ASK-6: with the engine's only slot held by a hanging turn, ask() still returns, and the state dir holds one marker."""
    async with Engine(tmp_path / "state", max_concurrent=1) as engine:
        session = claude.new_session(tmp_path)
        turn_env = {"FAKE_FIXTURE": str(FIXTURES / "ok.jsonl"), "FAKE_MODE": "hang", "FAKE_RECORD": str(tmp_path / "turn.json")}
        async with engine.turn("hold the slot", session=session, env=turn_env) as turn:
            await wait_file(tmp_path / "turn.json")
            answer = await asyncio.wait_for(claude.ask("hi", schema=SCHEMA, env=env(tmp_path)), 10)
            assert answer.data
            markers = list((tmp_path / "state" / "turns").glob("*.json"))
            assert len(markers) == 1 and json.loads(markers[0].read_text())["turn_id"] == turn.id
            turn.cancel()
            async for _ in turn:
                pass
