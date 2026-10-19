"""Contract-test kit: every provider against fake agent binaries (REQ-TEST-2)."""

import asyncio
import json
import os
from dataclasses import dataclass
from pathlib import Path

import pytest

from replay import FIXTURES, read_fixture
from televibe.account import Account
from televibe.providers import ClaudeCode, Codex
from televibe.providers.base import Provider

FAKES_BIN = Path(__file__).parent / "fakes" / "bin"
_STARTED = {"claude": ("system", "init"), "codex": ("thread.started", None)}
_TERMINAL = {"claude": "result", "codex": "turn.completed"}


@dataclass
class Kit:
    kind: str
    provider: Provider
    cwd: Path
    tmp: Path

    def fixture(self, name: str) -> str:
        return str(FIXTURES / self.kind / name)

    def env(self, fixture: str | None = "ok.jsonl", *, record: str = "record.json", **knobs: object) -> dict[str, str]:
        env = {"FAKE_RECORD": str(self.tmp / record)}
        if fixture is not None:
            env["FAKE_FIXTURE"] = self.fixture(fixture)
        env.update({f"FAKE_{name.upper()}": str(value) for name, value in knobs.items()})
        return env

    def record(self, name: str = "record.json") -> dict:
        return json.loads((self.tmp / name).read_text())

    def session(self):
        return self.provider.new_session(self.cwd)

    def started_after(self, name: str = "ok.jsonl") -> int:
        kind, subtype = _STARTED[self.kind]
        records = read_fixture(self.kind, name)
        return 1 + next(i for i, r in enumerate(records) if r.get("type") == kind and (subtype is None or r.get("subtype") == subtype))

    def terminal_index(self, name: str = "ok.jsonl") -> int:
        return next(i for i, r in enumerate(read_fixture(self.kind, name)) if r.get("type") == _TERMINAL[self.kind])

    def expected_text(self, name: str = "ok.jsonl") -> str:
        records = read_fixture(self.kind, name)
        if self.kind == "claude":
            return next(r["result"] for r in records if r.get("type") == "result")
        return [r["item"]["text"] for r in records if r.get("type") == "item.completed" and r["item"].get("type") == "agent_message"][-1]


@pytest.fixture
def fake_path(monkeypatch):
    monkeypatch.setenv("PATH", f"{FAKES_BIN}{os.pathsep}{os.environ.get('PATH', '')}")


@pytest.fixture(params=["claude", "codex"])
def kit(request, tmp_path, fake_path) -> Kit:
    cwd = tmp_path / "work"
    cwd.mkdir()
    account = Account(tmp_path / "account", credentials={"FAKE_CREDENTIAL": "s3cret-credential"})
    provider = ClaudeCode(account) if request.param == "claude" else Codex(account)
    return Kit(request.param, provider, cwd, tmp_path)


@pytest.fixture
async def engine(tmp_path):
    from televibe.engine import Engine

    async with Engine(tmp_path / "state", max_concurrent=4) as engine:
        yield engine


async def collect(turn) -> list:
    """Every event of an entered turn; asserts exactly one terminal event, and that it is last (REQ-TURN-1)."""
    from televibe.events import TERMINAL

    events = [event async for event in turn]
    terminals = [e for e in events if isinstance(e, TERMINAL)]
    assert len(terminals) == 1 and events[-1] is terminals[0], events
    assert turn._runner.events.empty()
    return events


async def run(engine, kit: Kit, prompt: str = "hi", session=None, **turn_kwargs) -> list:
    """Run one turn to its end; without an explicit env the fake replays ok.jsonl."""
    turn_kwargs.setdefault("env", kit.env())
    async with engine.turn(prompt, session=session or kit.session(), **turn_kwargs) as turn:
        return await collect(turn)


async def until(predicate, timeout: float = 10.0) -> None:
    loop = asyncio.get_running_loop()
    deadline = loop.time() + timeout
    while not predicate():
        assert loop.time() < deadline, "condition not met in time"
        await asyncio.sleep(0.02)


async def wait_file(path: Path, timeout: float = 10.0) -> None:
    await until(lambda: path.exists() and path.stat().st_size > 0, timeout)


async def wait_dead(pid: int, timeout: float = 5.0) -> bool:
    loop = asyncio.get_running_loop()
    deadline = loop.time() + timeout
    while loop.time() < deadline:
        try:
            os.kill(pid, 0)
        except ProcessLookupError:
            return True
        await asyncio.sleep(0.05)
    return False


def marker_files(tmp: Path) -> list[Path]:
    return sorted((tmp / "state" / "turns").glob("*.json"))
