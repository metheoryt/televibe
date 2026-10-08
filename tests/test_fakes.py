import json
import os
import subprocess

from conftest import FAKES_BIN, Kit


def test_fake_replays_with_the_session_id(kit: Kit):
    """REQ-TEST-2: the fakes replay recorded output with the session id from argv."""
    argv = [str(FAKES_BIN / kit.kind)] + (["--resume", "S-1"] if kit.kind == "claude" else ["exec", "resume", "S-1", "-"])
    result = subprocess.run(argv, input="the prompt", capture_output=True, text=True, env={**os.environ, **kit.env()}, timeout=10)
    lines = [json.loads(line) for line in result.stdout.splitlines()]
    assert lines and "S-1" in result.stdout and "{{SESSION_ID}}" not in result.stdout
    assert kit.record()["prompt"] == "the prompt"
