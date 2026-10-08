"""A fake `claude` / `codex` for the contract tests (REQ-TEST-2).

It replays a recorded stream from tests/fixtures. FAKE_* variables, passed as
the turn's env, change what it does:

FAKE_FIXTURE    recorded stdout to replay; {{SESSION_ID}} becomes the session id
FAKE_STDERR     file whose text goes to stderr after the replay
FAKE_AFTER      replay only the first N lines
FAKE_SLEEP      seconds to sleep before the replay
FAKE_EXIT       exit code (default 0)
FAKE_MODE       hang: start a child, then sleep forever
                orphan: start a child that keeps stdout/stderr open, then exit
                flood: write 1 MiB to stderr before anything else
                badlines: write a non-JSON line, a JSON array and a line over 8 MiB first
FAKE_CHILD_PID  file to write the pid of the child that hang/orphan start
FAKE_RECORD     file to write argv, cwd, env, prompt, pid and pgid to
"""

import json
import os
import subprocess
import sys
import time
import uuid
from pathlib import Path


def _out(line: str) -> None:
    sys.stdout.write(line + "\n")
    sys.stdout.flush()


def _session_id(kind: str, argv: list[str]) -> str:
    if kind == "claude":
        for flag in ("--session-id", "--resume"):
            if flag in argv:
                return argv[argv.index(flag) + 1]
        return "no-session-flag"
    if "resume" in argv:
        return argv[argv.index("resume") + 1]
    return str(uuid.uuid4())


def main(kind: str) -> int:
    argv = sys.argv[1:]
    prompt = sys.stdin.read()  # returns only once televibe closes stdin (REQ-RUN-1)
    env = os.environ
    session_id = _session_id(kind, argv)
    if "FAKE_RECORD" in env:
        Path(env["FAKE_RECORD"]).write_text(
            json.dumps(
                {"argv": argv, "cwd": os.getcwd(), "env": dict(env), "prompt": prompt, "pid": os.getpid(), "pgid": os.getpgid(0)}
            )
        )
    mode = env.get("FAKE_MODE", "")
    if mode == "flood":
        sys.stderr.write("e" * (1 << 20))
        sys.stderr.flush()
    if mode == "badlines":
        _out("this is not json")
        _out("[1, 2, 3]")
        _out('{"pad": "' + "x" * (8 * 1024 * 1024) + '"}')
    time.sleep(float(env.get("FAKE_SLEEP", "0")))
    lines = Path(env["FAKE_FIXTURE"]).read_text().splitlines() if "FAKE_FIXTURE" in env else []
    for line in lines[: int(env.get("FAKE_AFTER", len(lines)))]:
        _out(line.replace("{{SESSION_ID}}", session_id))
    if "FAKE_STDERR" in env:
        sys.stderr.write(Path(env["FAKE_STDERR"]).read_text().replace("{{SESSION_ID}}", session_id))
        sys.stderr.flush()
    if mode in ("hang", "orphan"):
        child = subprocess.Popen(["sleep", "600"])  # inherits stdout/stderr and the process group
        if "FAKE_CHILD_PID" in env:
            Path(env["FAKE_CHILD_PID"]).write_text(str(child.pid))
        if mode == "hang":
            time.sleep(600)
    return int(env.get("FAKE_EXIT", "0"))
