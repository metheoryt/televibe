"""One turn's lifecycle (Runner) and the caller's handle on it (Turn)."""

from __future__ import annotations

import asyncio
import logging
import uuid
import weakref
from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any

from televibe.env import child_env
from televibe.errors import TelevibeError
from televibe.events import TERMINAL, Done, Event, Failed, FailReason, Message, Queued, Started
from televibe.lines import json_records
from televibe.procinfo import kill_group, start_time
from televibe.providers.base import Failure, Parser, Provider, TurnOptions
from televibe.scheduler import Ticket
from televibe.session import Session

if TYPE_CHECKING:
    from televibe.engine import Engine

log = logging.getLogger("televibe")

EXIT_GRACE_S = 10.0  # REQ-RUN-7: time to exit after a terminal record
DRAIN_S = 2.0  # REQ-RUN-7: time for the pipes after the agent exited
STDERR_TAIL = 4096  # REQ-RUN-5


@dataclass(frozen=True, slots=True)
class TurnSpec:
    prompt: str
    options: TurnOptions
    lane: str | None
    env: dict[str, str] = field(repr=False)  # REQ-ENV-4: may hold tokens
    timeout_s: float
    tag: dict[str, Any]


class _Tail:
    """The last `size` bytes of a stream, read until EOF (REQ-RUN-2)."""

    def __init__(self, size: int) -> None:
        self._size = size
        self._buf = bytearray()

    async def drain(self, stream: asyncio.StreamReader) -> None:
        while chunk := await stream.read(64 * 1024):
            self._buf += chunk
            if len(self._buf) > self._size:
                del self._buf[: -self._size]

    def text(self) -> str:
        return self._buf.decode("utf-8", "replace")


async def _leader_exit(proc: asyncio.subprocess.Process) -> None:
    """Resolve once the agent's own process has exited.

    Not `proc.wait()`: on Python 3.12 it also waits for every pipe to close, so a
    background child holding stdout keeps it pending; `returncode` is set at exit
    either way (measured 2026-10-08 on 3.12.15 and 3.14.8).
    """
    while proc.returncode is None:
        await asyncio.sleep(0.05)


async def _feed_stdin(stdin: asyncio.StreamWriter, prompt: str) -> None:
    """Write the prompt and close stdin, always (REQ-RUN-1)."""
    try:
        stdin.write(prompt.encode())
        await stdin.drain()
    except (BrokenPipeError, ConnectionResetError):
        pass
    finally:
        stdin.close()


class Runner:
    """Runs one turn to exactly one terminal event. Holds no reference to its Turn."""

    def __init__(self, engine: Engine, provider: Provider, session: Session, spec: TurnSpec) -> None:
        self.turn_id = uuid.uuid4().hex
        self.engine = engine
        self.provider = provider
        self.session = session
        self.spec = spec
        self.events: asyncio.Queue[Event] = asyncio.Queue()
        self.ended = False
        self.orphaned = False
        self._cancel_detail: str | None = None
        self._pid: int | None = None
        self._task: asyncio.Task[None] | None = None
        self._started: Session | None = None
        self._messages: list[str] = []
        self._marker: dict[str, Any] = {}
        key = (provider.kind, str(session.home), session.id) if session.id else None
        self._ticket = Ticket(spec.lane, key)

    # -- control, callable from anywhere on the loop ---------------------------------

    def start(self) -> None:
        self._task = asyncio.get_running_loop().create_task(self._main(), name=f"televibe-turn-{self.turn_id}")

    def cancel(self, detail: str) -> None:
        """Idempotent; safe before, during and after the process (REQ-CANCEL-2)."""
        if self.ended or self._cancel_detail is not None:
            return
        self._cancel_detail = detail
        self.engine._scheduler.withdraw(self._ticket)
        if self._pid is not None:
            kill_group(self._pid)

    def kill_now(self) -> None:
        """Thread-safe part of a cancel, for the garbage-collection finalizer."""
        if self._pid is not None and not self.ended:
            kill_group(self._pid)

    async def wait_ended(self) -> None:
        if self._task is not None:
            await asyncio.shield(self._task)

    def discard(self) -> None:
        """The caller is done with the turn: forget it and remove its marker (REQ-STATE-2)."""
        self.engine._markers.remove(self.turn_id)
        self.engine._runners.pop(self.turn_id, None)

    # -- events --------------------------------------------------------------------

    def _emit(self, event: Event) -> None:
        if isinstance(event, Started):
            self._started = event.session
            # A new Codex session learns its id only now; lock it from here on (REQ-SESSION-7).
            self.engine._scheduler.adopt(self._ticket, (self.provider.kind, str(event.session.home), event.session.id))
            self._marker.update(session=event.session.dump(), session_id=event.session.id)
            try:
                self.engine._markers.write(self.turn_id, self._marker)
            except OSError as exc:  # the turn itself is fine; only recovery after a crash loses detail
                log.warning("televibe: could not update the marker of turn %s: %s", self.turn_id, exc)
        elif isinstance(event, Message):
            self._messages.append(event.text)
        self.events.put_nowait(event)

    def _end(self, event: Done | Failed) -> None:
        self.ended = True
        self.events.put_nowait(event)

    def _fail(self, reason: FailReason, detail: str) -> None:
        self._end(Failed(reason, self._started, detail, "\n\n".join(self._messages)))

    # -- the turn ------------------------------------------------------------------

    async def _main(self) -> None:
        try:
            await self._run()
        except asyncio.CancelledError:
            if self._pid is not None:
                kill_group(self._pid)
            if not self.ended:
                self._fail(FailReason.CANCELLED, "the turn's task was cancelled")
            raise
        except Exception as exc:  # a bug in televibe must still end the turn (REQ-TURN-1)
            log.exception("televibe turn %s failed inside televibe", self.turn_id)
            if self._pid is not None:
                kill_group(self._pid)
            if not self.ended:
                self._fail(FailReason.PROVIDER_ERROR, f"televibe internal error: {exc!r}")
        finally:
            self.engine._scheduler.withdraw(self._ticket)
            self.engine._scheduler.release(self._ticket)
            if self.orphaned:
                self.discard()

    async def _run(self) -> None:
        granted = False
        if self._cancel_detail is None:
            granted = await self.engine._scheduler.acquire(self._ticket, lambda: self._emit(Queued()))
        if not granted or self._cancel_detail is not None:
            self._fail(FailReason.CANCELLED, self._cancel_detail or "cancelled")
            return

        spec, provider, session = self.spec, self.provider, self.session
        argv = provider.argv(session, spec.options)
        env = child_env(provider.account_var, provider.account, spec.env)
        self._marker = {
            "turn_id": self.turn_id,
            "tag": spec.tag,
            "session": session.dump(),
            "started_at": datetime.now(UTC).isoformat(),
            "pgid": None,
            "proc_start": None,
            "session_id": session.id if session.started else None,
        }
        self.engine._markers.write(self.turn_id, self._marker)  # REQ-STATE-1: before the process starts

        try:
            proc = await asyncio.create_subprocess_exec(
                *argv,
                stdin=asyncio.subprocess.PIPE,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                cwd=session.cwd,
                env=env,
                start_new_session=True,  # REQ-CANCEL-1: its own process group
            )
        except OSError as exc:
            self._fail(FailReason.SPAWN_ERROR, f"could not start {argv[0]!r} in {session.cwd}: {exc}")
            return
        self._pid = proc.pid
        if self._cancel_detail is not None:  # REQ-CANCEL-3: a cancel that landed during the spawn
            kill_group(proc.pid)
        deadline = asyncio.get_running_loop().time() + spec.timeout_s  # REQ-QUEUE-5
        self._marker.update(pgid=proc.pid, proc_start=await asyncio.to_thread(start_time, proc.pid))
        self.engine._markers.write(self.turn_id, self._marker)

        parser = provider.parser(session)
        stderr = _Tail(STDERR_TAIL)
        stderr_task = asyncio.create_task(stderr.drain(proc.stderr))
        reader = asyncio.create_task(self._read(proc.stdout, parser))
        exited = asyncio.create_task(_leader_exit(proc))
        timed_out = False
        try:
            async with asyncio.timeout_at(deadline):
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
            raise error  # a bug in televibe while reading; _main logs it and ends the turn

        returncode = proc.returncode if proc.returncode is not None else -1
        outcome = parser.finish(returncode, stderr.text())
        if isinstance(outcome, Done) and parser.terminal is not None:
            self._end(outcome)
        elif self._cancel_detail is not None:
            self._fail(FailReason.CANCELLED, self._cancel_detail)
        elif timed_out:
            self._fail(FailReason.TIMEOUT, f"no result within {spec.timeout_s:g} s")
        else:
            assert isinstance(outcome, Failure)
            self._fail(outcome.reason, outcome.detail)

    async def _read(self, stdout: asyncio.StreamReader, parser: Parser) -> None:
        async for record in json_records(stdout):
            for event in parser.feed(record):
                self._emit(event)
            if parser.terminal is not None:
                return


def _abandoned(runner: Runner, loop: asyncio.AbstractEventLoop) -> None:
    """Finalizer of a Turn that was entered and never exited (REQ-CANCEL-6)."""
    if runner.ended:
        return
    log.warning("televibe turn %s was garbage-collected while its agent ran; killing it", runner.turn_id)
    runner.orphaned = True
    runner.kill_now()
    if not loop.is_closed():
        loop.call_soon_threadsafe(runner.cancel, "the turn was garbage-collected")


class Turn:
    """One prompt sent to one session. Enter it, then iterate it once."""

    def __init__(self, runner: Runner) -> None:
        self._runner = runner
        self._entered = False
        self._iterated = False
        self._finalizer: weakref.finalize | None = None

    @property
    def id(self) -> str:
        return self._runner.turn_id

    def cancel(self) -> None:
        self._runner.cancel("cancelled by the caller")

    async def __aenter__(self) -> Turn:
        if self._entered:
            raise TelevibeError("a Turn is entered once")
        engine = self._runner.engine
        if engine._state != "open":
            raise TelevibeError("the engine is not open")
        self._entered = True
        engine._runners[self.id] = self._runner
        self._runner.start()  # REQ-TURN-6: entering is what queues it
        self._finalizer = weakref.finalize(self, _abandoned, self._runner, asyncio.get_running_loop())
        return self

    async def __aexit__(self, *exc_info: object) -> None:
        if self._finalizer is not None:
            self._finalizer.detach()
        runner = self._runner
        runner.cancel("the caller left the turn before it ended")  # REQ-CANCEL-5; no-op once ended
        try:
            await runner.wait_ended()
        except BaseException:
            runner.orphaned = True  # the runner removes the marker itself when it ends
            raise
        runner.discard()

    def __aiter__(self) -> AsyncIterator[Event]:
        if not self._entered:
            raise TelevibeError("enter the turn with `async with` before iterating it")
        if self._iterated:
            raise TelevibeError("a Turn is iterated once, by one consumer")
        self._iterated = True
        return self._events()

    async def _events(self) -> AsyncIterator[Event]:
        while True:
            event = await self._runner.events.get()
            yield event
            if isinstance(event, TERMINAL):
                return
