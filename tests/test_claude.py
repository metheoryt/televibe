import uuid
from pathlib import Path

import pytest

from replay import FIXTURES, read_fixture, replay
from televibe.access import Access
from televibe.account import Account
from televibe.errors import TelevibeError
from televibe.events import Done, FailReason, LimitWindow, Limits, Message, Started, ToolUse
from televibe.providers.base import Failure, TurnOptions
from televibe.providers.claude import ClaudeCode

READ = TurnOptions(Access.READ_ONLY, None, None, ())


@pytest.fixture
def claude(tmp_path):
    return ClaudeCode(Account(tmp_path / "acc"))


def test_new_session_gets_a_uuid4(claude, tmp_path):
    """REQ-SESSION-6: for Claude Code televibe generates a UUID4 and passes it as --session-id."""
    session = claude.prepare(claude.new_session(tmp_path))
    assert uuid.UUID(session.id).version == 4 and session.started is False
    assert claude.prepare(claude.new_session(tmp_path, id="mine")).id == "mine"


def test_argv_new_session(claude, tmp_path):
    """REQ-CLAUDE-1, REQ-ACCESS-2: the base command, plan mode, and --session-id for a new session."""
    session = claude.prepare(claude.new_session(tmp_path))
    assert claude.argv(session, READ) == [
        "claude", "-p", "--output-format", "stream-json", "--verbose",
        "--permission-mode", "plan", "--session-id", session.id,
    ]


def test_argv_every_option(claude, tmp_path):
    """REQ-CLAUDE-1, REQ-ACCESS-2: model, instructions, add_dirs, bypassPermissions, --resume."""
    session = claude.resume_session(tmp_path, "S")
    options = TurnOptions(Access.FULL, "opus", "be brief", (Path("/a"), Path("/b")))
    assert claude.argv(session, options) == [
        "claude", "-p", "--output-format", "stream-json", "--verbose",
        "--permission-mode", "bypassPermissions", "--model", "opus",
        "--append-system-prompt", "be brief", "--add-dir", "/a", "--add-dir", "/b", "--resume", "S",
    ]


def test_workspace_write_unsupported(claude):
    """REQ-ACCESS-1, REQ-ACCESS-2: Claude Code has no headless WORKSPACE_WRITE."""
    with pytest.raises(TelevibeError):
        claude.check_access(Access.WORKSPACE_WRITE)


def test_never_a_mode_that_waits_for_a_person(claude, tmp_path):
    """REQ-ACCESS-5: only plan and bypassPermissions are ever passed."""
    session = claude.resume_session(tmp_path, "S")
    for access in claude.access_levels:
        argv = claude.argv(session, TurnOptions(access, None, None, ()))
        assert argv[argv.index("--permission-mode") + 1] in {"plan", "bypassPermissions"}


def test_parse_ok(claude, tmp_path):
    """REQ-CLAUDE-2, REQ-TURN-8, REQ-TURN-9: init → Started, tool_use → ToolUse, text → Message, result → Done."""
    events, outcome = replay(claude, claude.prepare(claude.new_session(tmp_path)), "ok.jsonl")
    assert isinstance(events[0], Started) and events[0].session.id and events[0].session.started
    reads = [e for e in events if isinstance(e, ToolUse) and e.name == "Read"]
    assert reads and reads[0].detail
    assert any(isinstance(e, Message) for e in events)
    result = next(r for r in read_fixture("claude", "ok.jsonl") if r["type"] == "result")
    assert isinstance(outcome, Done)
    assert outcome.text == result["result"]
    assert outcome.usage == result["usage"]
    assert outcome.session == events[0].session


def test_parse_resume_ignores_thinking_only_events(claude, tmp_path):
    """REQ-CLAUDE-2: an assistant event with only a thinking block yields nothing."""
    events, outcome = replay(claude, claude.resume_session(tmp_path, "{{SESSION_ID}}"), "resume.jsonl")
    assert [type(e) for e in events] == [Started, Message, Limits]
    assert isinstance(outcome, Done)


def test_rate_limit_event_is_limits(claude, tmp_path):
    """REQ-CLAUDE-2, REQ-TURN-10: an allowed call reports its usage windows too."""
    events, _ = replay(claude, claude.prepare(claude.new_session(tmp_path)), "ok.jsonl")
    assert [e for e in events if isinstance(e, Limits)] == [
        Limits(LimitWindow(0.35, 1791464400), LimitWindow(0.13, 1791986400), rejected=False, resets_at=1791464400),
    ]


def test_limits_without_unified_windows(claude, tmp_path):
    """REQ-TURN-10: unifiedWindows is internal to the CLI; the top-level fields still give one window."""
    parser = claude.parser(claude.resume_session(tmp_path, "S"))
    info = {"status": "allowed_warning", "resetsAt": 100, "rateLimitType": "seven_day", "utilization": 0.91}
    assert parser.feed({"type": "rate_limit_event", "rate_limit_info": info}) == [
        Limits(None, LimitWindow(0.91, 100), rejected=False, resets_at=100),
    ]
    assert parser.feed({"type": "rate_limit_event", "rate_limit_info": {"status": "allowed"}}) == [
        Limits(None, None, rejected=False, resets_at=None),
    ]
    assert parser.feed({"type": "rate_limit_event"}) == []


def test_usage_limit_from_recorded_output(claude, tmp_path):
    """REQ-CLAUDE-3, REQ-TURN-7: a call refused at the usage limit is usage_limit, and the CLI's own
    limit line is not a Message, so it never reaches Failed.partial."""
    events, outcome = replay(claude, claude.prepare(claude.new_session(tmp_path)), "limit.jsonl", returncode=1)
    assert [type(e) for e in events] == [Started, Limits]
    assert events[1] == Limits(LimitWindow(1.0, 1791540600), LimitWindow(0.39, 1791986400), rejected=True,
                               resets_at=1791540600)
    assert outcome.reason is FailReason.USAGE_LIMIT and "session limit" in outcome.detail


def test_error_result_is_failure(claude, tmp_path):
    """REQ-TURN-7: a result with is_error ends in Failed, never Done."""
    _, outcome = replay(claude, claude.resume_session(tmp_path, "{{SESSION_ID}}"), "lost.jsonl", returncode=1)
    assert outcome == Failure(FailReason.PROVIDER_ERROR, outcome.detail)
    assert "No conversation found" in outcome.detail


def test_session_lost_from_recorded_output(claude, tmp_path):
    """REQ-RUN-6: is_error result plus the recorded stderr is session_lost."""
    stderr = (FIXTURES / "claude" / "lost.stderr").read_text()
    _, outcome = replay(claude, claude.resume_session(tmp_path, "{{SESSION_ID}}"), "lost.jsonl", 1, stderr)
    assert outcome.reason is FailReason.SESSION_LOST


def test_no_result_is_provider_error(claude, tmp_path):
    """REQ-RUN-5: no terminal record → provider_error with stderr, or the exit code when stderr is empty."""
    parser = claude.parser(claude.resume_session(tmp_path, "S"))
    assert parser.finish(3, "boom\n") == Failure(FailReason.PROVIDER_ERROR, "boom")
    assert "3" in parser.finish(3, "").detail
