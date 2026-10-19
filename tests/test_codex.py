import tomllib
from pathlib import Path

import pytest

from replay import FIXTURES, replay
from televibe.access import Access
from televibe.account import Account
from televibe.errors import TelevibeError
from televibe.events import Done, FailReason, Message, Started, ToolUse, Warning
from televibe.providers.base import Failure, TurnOptions
from televibe.providers.codex import Codex, toml_basic_string


@pytest.fixture
def codex(tmp_path):
    return Codex(Account(tmp_path / "acc"))


def test_argv_new_session(codex, tmp_path):
    """REQ-CODEX-1, REQ-ACCESS-3, REQ-SESSION-4: exec --json with -C, the sandbox flag, and '-' for stdin."""
    session = codex.new_session(tmp_path)
    assert codex.argv(session, TurnOptions(Access.READ_ONLY, None, None, ())) == [
        "codex", "exec", "--json", "--skip-git-repo-check", "-C", str(tmp_path.resolve()),
        "--sandbox", "read-only", "-",
    ]


def test_argv_resumed_with_every_option(codex, tmp_path):
    """REQ-CODEX-1, REQ-CODEX-2, REQ-ACCESS-3: options come before `resume <id>`."""
    session = codex.resume_session(tmp_path, "T")
    options = TurnOptions(Access.WORKSPACE_WRITE, "gpt-5", 'say "hi"', (Path("/a"),))
    assert codex.argv(session, options) == [
        "codex", "exec", "--json", "--skip-git-repo-check", "-C", str(tmp_path.resolve()),
        "--sandbox", "workspace-write", "-m", "gpt-5", "--add-dir", "/a",
        "-c", 'developer_instructions="say \\"hi\\""', "resume", "T", "-",
    ]


def test_full_access_flag(codex, tmp_path):
    """REQ-ACCESS-3: FULL maps to --dangerously-bypass-approvals-and-sandbox."""
    argv = codex.argv(codex.new_session(tmp_path), TurnOptions(Access.FULL, None, None, ()))
    assert "--dangerously-bypass-approvals-and-sandbox" in argv and "--sandbox" not in argv


def test_never_asks_for_approval(codex, tmp_path):
    """REQ-ACCESS-5: no approval-waiting option is ever passed."""
    for access in codex.access_levels:
        argv = codex.argv(codex.resume_session(tmp_path, "T"), TurnOptions(access, None, None, ()))
        assert not {"-a", "--ask-for-approval", "--approve-for-me"} & set(argv)


def test_codex_names_its_own_sessions(codex, tmp_path):
    """REQ-SESSION-5: Codex refuses a fixed id."""
    with pytest.raises(TelevibeError):
        codex.new_session(tmp_path, id="mine")


@pytest.mark.parametrize("value", ['quote " here', "back\\slash", "two\nlines", "tab\there", "ctrl\x01", "юникод ✓", ""])
def test_toml_basic_string_round_trips(value):
    """REQ-CODEX-2: quotes, backslashes and newlines in instructions survive."""
    assert tomllib.loads(f"v = {toml_basic_string(value)}")["v"] == value


def test_parse_ok(codex, tmp_path):
    """REQ-CODEX-3, REQ-TURN-8, REQ-TURN-9: thread.started, error item, messages, command, turn.completed."""
    events, outcome = replay(codex, codex.new_session(tmp_path), "ok.jsonl")
    assert [type(e) for e in events] == [Started, Warning, Message, ToolUse, Message]
    assert events[0].session.id == "{{SESSION_ID}}" and events[0].session.started
    assert events[3] == ToolUse("command_execution", "/bin/zsh -lc ls")
    assert isinstance(outcome, Done) and outcome.text == events[4].text
    assert outcome.usage["output_tokens"] == 113


def test_parse_readonly_refusal(codex, tmp_path):
    """REQ-CODEX-3, REQ-TURN-8: Done.text is the last agent_message."""
    events, outcome = replay(codex, codex.new_session(tmp_path), "readonly.jsonl")
    messages = [e.text for e in events if isinstance(e, Message)]
    assert len(messages) == 2 and outcome.text == messages[-1]


def test_session_lost_from_recorded_output(codex, tmp_path):
    """REQ-RUN-6: non-zero exit, no thread.started, and the recorded stderr is session_lost."""
    stderr = (FIXTURES / "codex" / "lost.stderr").read_text()
    events, outcome = replay(codex, codex.resume_session(tmp_path, "T"), "lost.jsonl", 1, stderr)
    assert events == [] and outcome.reason is FailReason.SESSION_LOST


def test_other_failures_are_provider_error(codex, tmp_path):
    """REQ-RUN-5, REQ-RUN-6: anything else that fails is provider_error."""
    parser = codex.parser(codex.resume_session(tmp_path, "T"))
    assert parser.finish(1, "something else") == Failure(FailReason.PROVIDER_ERROR, "something else")
    assert "2" in parser.finish(2, "").detail
    parser.feed({"type": "thread.started", "thread_id": "T"})
    assert parser.finish(1, "no rollout found for thread id T").reason is FailReason.PROVIDER_ERROR
