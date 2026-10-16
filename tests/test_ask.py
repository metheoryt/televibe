"""One-shot calls (SPEC section 12): arguments, argv, surface. Process tests are in test_ask_run.py."""

import json

import pytest

from televibe import (
    Account,
    Answer,
    AskFailed,
    ClaudeCode,
    Codex,
    FailReason,
    TelevibeError,
)
from televibe.oneshot import AskSpec, check_ask_args

SCHEMA = {"type": "object", "required": ["word"], "properties": {"word": {"type": "string", "description": "одно слово"}}}


@pytest.fixture
def claude(tmp_path):
    return ClaudeCode(Account(tmp_path / "acc"))


def test_answer_and_askfailed_shapes():
    """REQ-ASK-1, REQ-ASK-4: Answer carries data, usage, limits; AskFailed is a TelevibeError with reason and detail."""
    answer = Answer({"word": "ok"}, {"input_tokens": 1}, None)
    assert answer.data == {"word": "ok"} and answer.usage == {"input_tokens": 1} and answer.limits is None
    failed = AskFailed(FailReason.TIMEOUT, "no result within 3 s")
    assert isinstance(failed, TelevibeError)
    assert failed.reason is FailReason.TIMEOUT and failed.detail == "no result within 3 s"
    assert str(failed) == "timeout: no result within 3 s"


def test_check_ask_args_builds_a_spec():
    """REQ-ASK-2: the schema is compact JSON with non-ASCII kept; defaults are None, {}, 180."""
    spec = check_ask_args("hi", SCHEMA, None, None, {}, 180)
    assert isinstance(spec, AskSpec)
    assert spec.schema_json == json.dumps(SCHEMA, separators=(",", ":"), ensure_ascii=False)
    assert "одно слово" in spec.schema_json and " " not in spec.schema_json.replace("одно слово", "")
    assert spec.system is None and spec.model is None and spec.env == {} and spec.timeout_s == 180.0
    assert "FAKE" not in repr(AskSpec("p", "{}", None, None, {"FAKE_TOKEN": "x"}, 1.0))


@pytest.mark.parametrize(
    "kwargs",
    [
        {"prompt": ""},
        {"prompt": "a\udc80b"},
        {"schema": "not a mapping"},
        {"schema": {"x": object()}},
        {"system": 5},
        {"system": "a\udc80b"},
        {"model": 5},
        {"model": "x\ud800"},
        {"schema": {"description": "\ud800"}},
        {"env": {"ANTHROPIC_API_KEY": "sk"}},
        {"env": {"CLAUDE_CONFIG_DIR": "/x"}},
        {"timeout_s": 0},
        {"timeout_s": float("inf")},
        {"timeout_s": True},
    ],
)
def test_check_ask_args_refuses_misuse(kwargs):
    """REQ-ASK-5, REQ-API-2: every misuse is a TelevibeError before any process."""
    args = {"prompt": "hi", "schema": SCHEMA, "system": None, "model": None, "env": {}, "timeout_s": 180, **kwargs}
    with pytest.raises(TelevibeError):
        check_ask_args(**args)


def test_ask_refuses_unencodable_prompt(claude):
    """REQ-ASK-5: a lone surrogate cannot reach stdin; refused at the call like engine.turn."""
    with pytest.raises(TelevibeError, match="UTF-8"):
        check_ask_args("a\udc80b", SCHEMA, None, None, {}, 180)


def test_ask_argv(claude):
    """REQ-ASK-2: the fixed flags, then --model and --system-prompt only when given."""
    spec = check_ask_args("hi", SCHEMA, None, None, {}, 180)
    assert claude.ask_argv(spec) == [
        "claude", "-p", "--output-format", "stream-json", "--verbose",
        "--json-schema", spec.schema_json, "--tools", "", "--strict-mcp-config", "--no-session-persistence",
    ]
    spec = check_ask_args("hi", SCHEMA, "be terse", "sonnet", {}, 180)
    argv = claude.ask_argv(spec)
    assert argv[-4:] == ["--model", "sonnet", "--system-prompt", "be terse"]
    assert "--permission-mode" not in argv and "--session-id" not in argv and "--resume" not in argv


async def test_codex_has_no_one_shot_call(tmp_path):
    """REQ-ASK-1: Codex.ask raises TelevibeError until a recording exists (section 14)."""
    with pytest.raises(TelevibeError, match="codex"):
        await Codex(Account(tmp_path / "acc")).ask("hi", schema=SCHEMA)
