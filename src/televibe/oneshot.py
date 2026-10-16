"""One-shot calls: an answer in a JSON schema, with no session, no tools and no place in the queue (section 12)."""

from __future__ import annotations

import asyncio
import json
import math
import tempfile
from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from televibe.env import check_turn_env
from televibe.errors import TelevibeError
from televibe.events import Done, FailReason, Limits

if TYPE_CHECKING:
    from televibe.providers.base import Parser

DEFAULT_TIMEOUT_S = 180.0


@dataclass(frozen=True, slots=True)
class Answer:
    """What a one-shot call returned (REQ-ASK-1)."""

    data: Any
    usage: dict[str, Any]
    limits: Limits | None


class AskFailed(TelevibeError):
    """A one-shot call that did not return an answer (REQ-ASK-4)."""

    def __init__(self, reason: FailReason, detail: str) -> None:
        super().__init__(f"{reason.value}: {detail}")
        self.reason = reason
        self.detail = detail


@dataclass(frozen=True, slots=True)
class AskSpec:
    prompt: str
    schema_json: str
    system: str | None
    model: str | None
    env: dict[str, str] = field(repr=False)  # may hold tokens (REQ-ENV-4)
    timeout_s: float = DEFAULT_TIMEOUT_S


def _check_utf8(name: str, text: str) -> None:
    try:
        text.encode()
    except UnicodeEncodeError:
        raise TelevibeError(f"{name} must be text that encodes to UTF-8 (it holds a lone surrogate)") from None


def check_ask_args(
    prompt: str,
    schema: Mapping[str, Any],
    system: str | None,
    model: str | None,
    env: Mapping[str, str],
    timeout_s: float,
) -> AskSpec:
    """Every misuse raises TelevibeError before a process starts (REQ-ASK-5)."""
    if not isinstance(prompt, str) or not prompt:
        raise TelevibeError("prompt must be a non-empty str")
    _check_utf8("prompt", prompt)
    if not isinstance(schema, Mapping):
        raise TelevibeError("schema must be a mapping holding a JSON Schema")
    try:
        schema_json = json.dumps(dict(schema), separators=(",", ":"), ensure_ascii=False)
    except (TypeError, ValueError) as exc:
        raise TelevibeError(f"schema must be JSON-serializable: {exc}") from None
    _check_utf8("schema", schema_json)  # ensure_ascii=False keeps a lone surrogate, and argv cannot carry one
    for name, value in (("system", system), ("model", model)):
        if value is not None and not isinstance(value, str):
            raise TelevibeError(f"{name} must be a str or None")
        if value is not None and "\0" in value:
            raise TelevibeError(f"{name} must not contain a NUL character")
        if value is not None:
            _check_utf8(name, value)
    if isinstance(timeout_s, bool) or not isinstance(timeout_s, (int, float)) or not math.isfinite(timeout_s) or timeout_s <= 0:
        raise TelevibeError("timeout_s must be a positive, finite number of seconds")
    return AskSpec(prompt, schema_json, system, model, check_turn_env(env), float(timeout_s))


async def run_oneshot(argv: list[str], spec: AskSpec, env: dict[str, str], parser: Parser) -> Answer:
    """Run one call to its result (REQ-ASK-3, REQ-ASK-4). `env` is the full child environment."""
    # Imported here: providers.base imports this module for Answer, and turn imports providers.base.
    from televibe.lines import json_records
    from televibe.procinfo import kill_group
    from televibe.providers.base import Failure
    from televibe.turn import (
        DRAIN_S,
        EXIT_GRACE_S,
        STDERR_TAIL,
        _feed_stdin,
        _leader_exit,
        _Tail,
    )

    limits: list[Limits] = []

    async def read(stdout: asyncio.StreamReader) -> None:
        async for record in json_records(stdout):
            for event in parser.feed(record):
                if isinstance(event, Limits):
                    limits.append(event)
            if parser.terminal is not None:
                return

    with tempfile.TemporaryDirectory(prefix="televibe-ask-") as cwd:
        try:
            proc = await asyncio.create_subprocess_exec(
                *argv,
                stdin=asyncio.subprocess.PIPE,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                cwd=cwd,
                env=env,
                start_new_session=True,  # its own process group, killed as one (REQ-ASK-3)
            )
        except OSError as exc:
            raise AskFailed(FailReason.SPAWN_ERROR, f"could not start {argv[0]!r}: {exc}") from None
        assert proc.stdin is not None and proc.stdout is not None and proc.stderr is not None
        stderr = _Tail(STDERR_TAIL)
        stderr_task = asyncio.create_task(stderr.drain(proc.stderr))
        reader = asyncio.create_task(read(proc.stdout))
        exited = asyncio.create_task(_leader_exit(proc))
        timed_out = False
        try:
            async with asyncio.timeout(spec.timeout_s):
                await _feed_stdin(proc.stdin, spec.prompt)
                await asyncio.wait({reader, exited}, return_when=asyncio.FIRST_COMPLETED)
                if reader.done() and reader.exception() is None:
                    try:
                        await asyncio.wait_for(asyncio.shield(exited), EXIT_GRACE_S)
                    except TimeoutError:
                        kill_group(proc.pid)
                else:
                    await asyncio.wait({reader}, timeout=DRAIN_S)
        except TimeoutError:
            timed_out = True
        finally:
            kill_group(proc.pid)  # timeout, cancel, or stragglers holding the pipes (REQ-RUN-7)
            reader.cancel()
            await asyncio.wait({exited})
            await asyncio.wait({stderr_task}, timeout=DRAIN_S)
            stderr_task.cancel()
            await asyncio.gather(reader, stderr_task, return_exceptions=True)
        if not reader.cancelled() and (error := reader.exception()) is not None:
            raise error
        returncode = proc.returncode if proc.returncode is not None else -1
        outcome = parser.finish(returncode, stderr.text())
        if isinstance(outcome, Done) and parser.terminal is not None:
            # The result was read in time; an agent that lingers past timeout_s after it loses nothing (as a turn ends Done).
            data = getattr(parser, "structured_output", None)
            if data is None:
                raise AskFailed(FailReason.PROVIDER_ERROR, "the agent produced no structured output")
            return Answer(data, outcome.usage, limits[-1] if limits else None)
        if timed_out:
            raise AskFailed(FailReason.TIMEOUT, f"no result within {spec.timeout_s:g} s")
        assert isinstance(outcome, Failure)
        raise AskFailed(outcome.reason, outcome.detail)
