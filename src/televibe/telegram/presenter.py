"""Showing one turn in Telegram: reactions, typing or a live draft, then the answer (section 12, PRESENT)."""

from __future__ import annotations

import asyncio
import logging
import time
from collections.abc import AsyncIterable, Awaitable, Callable
from dataclasses import dataclass

from aiogram import Bot
from aiogram.enums import ChatAction
from aiogram.exceptions import TelegramRetryAfter
from aiogram.types import Message, ReactionTypeEmoji, ReplyParameters

from televibe.events import Done, Event, Failed, FailReason, Started, ToolUse
from televibe.events import Message as AgentMessage
from televibe.telegram.render import (
    status_line,
    to_draft_message,
    to_plain_text,
    to_rich_message,
)

log = logging.getLogger(__name__)

TYPING_EVERY_S = 4.0
"""Telegram shows a typing action for about five seconds."""
QUIET_TIMEOUT_S = 5.0
"""How long a reaction, draft or typing action may take before it is given up on."""
RETRY_WAIT_MAX_S = 30.0
"""The most time sending the answer spends waiting out Telegram's flood control, over all tries."""
_FLOOD_RETRIES = 3
"""Retries of one form of the answer under flood control, before the next form is tried."""


@dataclass(frozen=True, slots=True)
class Reactions:
    """The reaction on the bot's trigger message at each moment of a turn. All are on the Bot API's list."""

    queued: str = "👀"
    working: str = "👨‍💻"
    done: str = "👌"
    failed: str = "🤷"


@dataclass(frozen=True, slots=True)
class Texts:
    """What the chat reads when a turn fails, per FailReason."""

    timeout: str = "The agent ran out of time."
    cancelled: str = "The turn was cancelled."
    session_lost: str = "The agent lost this conversation. Send a new message, not a reply, to start over."
    provider_error: str = "The agent failed."
    spawn_error: str = "The agent could not be started."

    def for_reason(self, reason: FailReason) -> str:
        return getattr(self, reason.value)


_REACTIONS = Reactions()
_TEXTS = Texts()


class Presenter:
    """Shows one turn started by `message`. Make one per turn."""

    def __init__(
        self,
        bot: Bot,
        message: Message,
        *,
        reactions: Reactions = _REACTIONS,
        texts: Texts = _TEXTS,
        heartbeat_s: float = 8.0,
        model: str | None = None,
    ) -> None:
        self._bot, self._message = bot, message
        self._chat_id = message.chat.id
        self._thread_id = message.message_thread_id if message.is_topic_message else None
        self._private = message.chat.type == "private"
        self._reactions, self._texts = reactions, texts
        self._heartbeat_s, self._model = heartbeat_s, model
        self._text = ""
        self._tool: str | None = None
        self._started_at = 0.0

    async def accepted(self) -> None:
        """Mark the message as taken, before it waits in a chain (REQ-PRESENT-1)."""
        await self._react(self._reactions.queued)

    async def show(self, events: AsyncIterable[Event]) -> Message | None:
        """Consume a turn to its terminal event; return the sent answer, or None if no way of sending worked."""
        pulse: asyncio.Task[None] | None = None
        try:
            async for event in events:
                if isinstance(event, Started):
                    self._started_at = time.monotonic()
                    await self._react(self._reactions.working)
                    if pulse is None:
                        pulse = asyncio.create_task(self._drafts() if self._private else self._typing())
                elif isinstance(event, AgentMessage):
                    self._text, self._tool = event.text, None
                elif isinstance(event, ToolUse):
                    self._tool = event.name
                elif isinstance(event, (Done, Failed)):
                    await _stop(pulse)  # before the answer, so no draft or typing outlives it
                    return await self._finish(event)
        finally:
            await _stop(pulse)
        return None

    async def _drafts(self) -> None:
        """Re-send the draft every heartbeat_s whether or not it changed: a draft left alone expires."""
        while True:
            await self._quietly("draft", lambda: self._bot.send_rich_message_draft(
                chat_id=self._chat_id, draft_id=self._message.message_id, message_thread_id=self._thread_id,
                rich_message=to_draft_message(self._text, self._status())))
            await asyncio.sleep(self._heartbeat_s)

    async def _typing(self) -> None:
        while True:
            await self._quietly("typing", lambda: self._bot.send_chat_action(
                chat_id=self._chat_id, action=ChatAction.TYPING, message_thread_id=self._thread_id))
            await asyncio.sleep(TYPING_EVERY_S)

    def _status(self) -> str:
        return status_line(model=self._model, elapsed_s=time.monotonic() - self._started_at, tool=self._tool)

    async def _finish(self, end: Done | Failed) -> Message | None:
        if isinstance(end, Done):
            text, reaction = end.text, self._reactions.done
        else:
            log.warning("televibe: turn failed in chat %s (%s): %s", self._chat_id, end.reason, end.detail)
            reason = self._texts.for_reason(end.reason)
            text = f"{end.partial}\n\n{reason}" if end.partial.strip() else reason
            reaction = self._reactions.failed
        sent = await self._send(text)
        await self._react(reaction if sent is not None else self._reactions.failed)
        return sent

    async def _send(self, text: str) -> Message | None:
        """A turn never ends in silence (REQ-PRESENT-6): rich, plain, plain outside the topic.

        Flood control is waited out and the same form retried, within RETRY_WAIT_MAX_S in all:
        stepping down would hit the same limit. Any other failure steps down to the next form."""
        reply = ReplyParameters(message_id=self._message.message_id, allow_sending_without_reply=True)
        attempts: list[tuple[str, Callable[[], Awaitable[Message]]]] = [
            ("a rich message", lambda: self._bot.send_rich_message(
                chat_id=self._chat_id, message_thread_id=self._thread_id,
                rich_message=to_rich_message(text), reply_parameters=reply)),
            ("plain text", lambda: self._bot.send_message(
                chat_id=self._chat_id, message_thread_id=self._thread_id,
                text=to_plain_text(text), parse_mode=None, reply_parameters=reply)),
        ]
        if self._thread_id is not None:
            attempts.append(("plain text outside the topic", lambda: self._bot.send_message(
                chat_id=self._chat_id, text=to_plain_text(text), parse_mode=None, reply_parameters=reply)))
        waited = 0.0
        for what, attempt in attempts:
            for retry in range(_FLOOD_RETRIES + 1):
                try:
                    return await attempt()
                except TelegramRetryAfter as exc:
                    if retry == _FLOOD_RETRIES or waited + exc.retry_after > RETRY_WAIT_MAX_S:
                        log.warning("televibe: sending the answer as %s hit flood control in chat %s", what, self._chat_id)
                        break
                    waited += exc.retry_after
                    await asyncio.sleep(exc.retry_after)
                except Exception:
                    log.warning("televibe: sending the answer as %s failed in chat %s", what, self._chat_id, exc_info=True)
                    break
        log.error("televibe: every way of sending the answer failed in chat %s", self._chat_id)
        return None

    async def _react(self, emoji: str) -> None:
        await self._quietly(f"reaction {emoji}", lambda: self._bot.set_message_reaction(
            chat_id=self._chat_id, message_id=self._message.message_id, reaction=[ReactionTypeEmoji(emoji=emoji)]))

    async def _quietly(self, what: str, call: Callable[[], Awaitable[object]]) -> None:
        """A failed reaction, draft or typing action is logged and ignored (REQ-PRESENT-7).

        It gets QUIET_TIMEOUT_S, not aiogram's minute-long request timeout: a stalled reaction
        must not hold the answer, and through it the chain."""
        try:
            async with asyncio.timeout(QUIET_TIMEOUT_S):
                await call()
        except Exception:
            log.warning("televibe: %s failed in chat %s", what, self._chat_id, exc_info=True)


async def _stop(task: asyncio.Task[None] | None) -> None:
    """Cancel the pulse and wait for it. asyncio.wait, not `await task`, so a cancellation of
    the caller is not swallowed along with the pulse's own CancelledError."""
    if task is None or task.done():
        return
    task.cancel()
    await asyncio.wait([task])
