"""Live tests: a real two-turn session per provider (REQ-TEST-4).

Skipped unless an account directory is given:
  TELEVIBE_LIVE_CLAUDE_HOME=~/.claude-televibe uv run pytest -m live
  TELEVIBE_LIVE_CODEX_HOME=~/.codex-televibe uv run pytest -m live
Optional: TELEVIBE_LIVE_CLAUDE_TOKEN (passed as CLAUDE_CODE_OAUTH_TOKEN).
"""

import os
import uuid

import pytest

from televibe import Account, ClaudeCode, Codex, Done, Engine

pytestmark = [pytest.mark.live, pytest.mark.timeout(600)]


@pytest.mark.parametrize("kind", ["claude", "codex"])
async def test_two_turn_session(kind, tmp_path):
    """REQ-TEST-4: start a session, then resume it, against the real CLI."""
    home = os.environ.get(f"TELEVIBE_LIVE_{kind.upper()}_HOME")
    if not home:
        pytest.skip(f"set TELEVIBE_LIVE_{kind.upper()}_HOME to run")
    token = os.environ.get("TELEVIBE_LIVE_CLAUDE_TOKEN") if kind == "claude" else None
    account = Account(home, credentials={"CLAUDE_CODE_OAUTH_TOKEN": token} if token else None)
    provider = ClaudeCode(account) if kind == "claude" else Codex(account)
    word = f"televibe-{uuid.uuid4().hex[:8]}"
    async with Engine(tmp_path / "state") as engine:
        async with engine.turn(f"Remember the word {word}. Answer with the single word ok.", session=provider.new_session(tmp_path)) as turn:
            first = [e async for e in turn][-1]
        assert isinstance(first, Done), first
        async with engine.turn("Which word did I ask you to remember? Answer with that word only.", session=first.session) as turn:
            second = [e async for e in turn][-1]
        assert isinstance(second, Done), second
        assert word in second.text and second.session.id == first.session.id
