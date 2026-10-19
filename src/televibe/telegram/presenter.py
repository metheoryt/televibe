"""Showing one turn in Telegram: reactions, typing or a live draft, then the answer (section 13, PRESENT)."""

from __future__ import annotations

import asyncio
import logging
import time
from collections.abc import AsyncIterable, Awaitable, Callable
from dataclasses import dataclass
from datetime import UTC, datetime, tzinfo

from aiogram import Bot
from aiogram.enums import ChatAction
from aiogram.exceptions import TelegramRetryAfter
from aiogram.types import (
    InlineKeyboardMarkup,
    InputPollOption,
    Message,
    ReactionTypeEmoji,
    ReplyParameters,
)

from televibe.events import Done, Event, Failed, FailReason, Limits, Started, ToolUse
from televibe.events import Message as AgentMessage
from televibe.telegram.controls import (
    Control,
    ControlTexts,
    Poll,
    keyboard,
    poll_fallback_text,
    split_control,
)
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
    """The reaction on the bot's trigger message at each moment of a turn. All are on the Bot API's list.

    `done` may be None: the answer then clears the reaction instead, for a bot whose reply is
    signal enough that the turn is over. With `failed_when_sent` False a sent failure text
    clears the reaction too, and `failed` marks only a turn whose every send failed."""

    queued: str = "👀"
    working: str = "👨‍💻"
    done: str | None = "👌"
    failed: str = "🤷"
    failed_when_sent: bool = True


@dataclass(frozen=True, slots=True)
class Texts:
    """What the chat reads: a text per FailReason, and the labels of the usage windows in the draft's status.

    `usage_limit` is a `str.format` template over `resets_at`, an aware datetime, so a bot picks its
    own time format; `for_reason` fills it in."""

    timeout: str = "The agent ran out of time."
    cancelled: str = "The turn was cancelled."
    session_lost: str = "The agent lost this conversation. Send a new message, not a reply, to start over."
    provider_error: str = "The agent failed."
    spawn_error: str = "The agent could not be started."
    usage_limit: str = "The usage limit is reached. It resets at {resets_at:%H:%M %Z}."
    five_hour: str = "5h"
    seven_day: str = "7d"

    def for_reason(self, reason: FailReason, resets_at: datetime | None = None) -> str:
        """The text for `reason`. For usage_limit with no `resets_at` it is `provider_error`, and a
        template that does not format is returned as it is: a raw placeholder never reaches the chat."""
        if reason is not FailReason.USAGE_LIMIT:
            return getattr(self, reason.value)
        if resets_at is None:
            return self.provider_error
        try:
            return self.usage_limit.format(resets_at=resets_at)
        except (AttributeError, KeyError, IndexError, ValueError):
            log.warning("televibe: Texts.usage_limit does not format: %r", self.usage_limit)
            return self.usage_limit


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
        tz: tzinfo = UTC,
        controls: ControlTexts | None = None,
    ) -> None:
        self._bot, self._message = bot, message
        self._chat_id = message.chat.id
        self._thread_id = message.message_thread_id if message.is_topic_message else None
        self._private = message.chat.type == "private"
        self._reactions, self._texts = reactions, texts
        self._heartbeat_s, self._model, self._tz = heartbeat_s, model, tz
        self._text = ""
        self._note = ""
        self._tool: str | None = None
        self._limits: Limits | None = None
        self._started_at = 0.0
        self._controls = controls
        self.control: Control | None = None
        """The control actually sent as one, after `show()`; None when off, absent or unsendable (REQ-PRESENT-11)."""
        self.control_message: Message | None = None
        """The message carrying `control`: the answer for an offer or a form, the poll message for a poll."""

    async def accepted(self) -> None:
        """Mark the message as taken, before it waits in a chain (REQ-PRESENT-1)."""
        await self._react(self._reactions.queued)

    def note(self, text: str | None) -> None:
        """Show `text` in the live draft above the latest message text, from the next heartbeat on; None clears it.

        Only a private chat has a draft: in a group the note shows nowhere. The answer never carries it."""
        self._note = text or ""

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
                elif isinstance(event, Limits):
                    self._limits = event
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
                rich_message=to_draft_message(self._draft_text(), self._status())))
            await asyncio.sleep(self._heartbeat_s)

    async def _typing(self) -> None:
        while True:
            await self._quietly("typing", lambda: self._bot.send_chat_action(
                chat_id=self._chat_id, action=ChatAction.TYPING, message_thread_id=self._thread_id))
            await asyncio.sleep(TYPING_EVERY_S)

    def _draft_text(self) -> str:
        return "\n\n".join(part for part in (self._note, self._text) if part.strip())

    def _status(self) -> str:
        return status_line(model=self._model, elapsed_s=time.monotonic() - self._started_at,
                           usage=self._usage(), tool=self._tool)

    def _usage(self) -> list[tuple[str, float]]:
        if self._limits is None:
            return []
        windows = ((self._texts.five_hour, self._limits.five_hour), (self._texts.seven_day, self._limits.seven_day))
        return [(label, window.utilization) for label, window in windows if window is not None]

    def _reason_text(self, reason: FailReason) -> str:
        """The reason's text, the usage limit's with the latest reset time in `tz`."""
        unix = self._limits.resets_at if self._limits is not None else None
        resets_at = datetime.fromtimestamp(unix, self._tz) if unix is not None else None
        return self._texts.for_reason(reason, resets_at)

    async def _finish(self, end: Done | Failed) -> Message | None:
        control: Control | None = None
        if isinstance(end, Done):
            text, reaction = end.text, self._reactions.done
            if self._controls is not None:
                text, control = split_control(text)
                if not text.strip() and control is not None:  # a block alone is no silence (REQ-PRESENT-6)
                    text = control.question if isinstance(control, Poll) else "…"
        else:
            log.warning("televibe: turn failed in chat %s (%s): %s", self._chat_id, end.reason, end.detail)
            reason = self._reason_text(end.reason)
            partial = split_control(end.partial)[0] if self._controls is not None else end.partial  # REQ-PRESENT-12
            text = f"{partial}\n\n{reason}" if partial.strip() else reason
            reaction = self._reactions.failed if self._reactions.failed_when_sent else None
        markup = None
        if self._controls is not None and control is not None and not isinstance(control, Poll):
            markup = keyboard(control, self._controls)
        sent, with_markup = await self._send(text, markup)
        if sent is not None and control is not None and with_markup:
            if isinstance(control, Poll):
                await self._send_poll(control, sent)
            else:
                self.control, self.control_message = control, sent
        await self._react(reaction if sent is not None else self._reactions.failed)
        return sent

    async def _send_poll(self, poll: Poll, answer: Message) -> None:
        """A non-anonymous poll replying to the answer, in the answer's own thread; one Telegram refuses
        goes out as numbered text, and then `control` and `control_message` stay None (REQ-PRESENT-11)."""
        thread_id = answer.message_thread_id
        reply = ReplyParameters(message_id=answer.message_id, allow_sending_without_reply=True)
        sent = await self._try("the poll", [("a poll", lambda: self._bot.send_poll(
            chat_id=self._chat_id, message_thread_id=thread_id, question=poll.question,
            question_parse_mode=None, description_parse_mode=None,
            options=[InputPollOption(text=option) for option in poll.options], is_anonymous=False,
            allows_multiple_answers=poll.multiple, description=poll.description, reply_parameters=reply))])
        if sent is not None:
            self.control, self.control_message = poll, sent
            return
        text = poll_fallback_text(poll)
        await self._try("the poll as text", [("plain text", lambda: self._bot.send_message(
            chat_id=self._chat_id, message_thread_id=thread_id,
            text=text, parse_mode=None, reply_parameters=reply))])

    async def _send(self, text: str, markup: InlineKeyboardMarkup | None = None) -> tuple[Message | None, bool]:
        """A turn never ends in silence (REQ-PRESENT-6): rich, plain, plain outside the topic.

        `markup` rides on every form (REQ-PRESENT-11); if Telegram rejects it, a last plain text goes
        without it. Returns the message and whether the markup is on it."""
        reply = ReplyParameters(message_id=self._message.message_id, allow_sending_without_reply=True)
        attempts: list[tuple[str, Callable[[], Awaitable[Message]]]] = [
            ("a rich message", lambda: self._bot.send_rich_message(
                chat_id=self._chat_id, message_thread_id=self._thread_id,
                rich_message=to_rich_message(text), reply_parameters=reply, reply_markup=markup)),
            ("plain text", lambda: self._bot.send_message(
                chat_id=self._chat_id, message_thread_id=self._thread_id,
                text=to_plain_text(text), parse_mode=None, reply_parameters=reply, reply_markup=markup)),
        ]
        if self._thread_id is not None:
            attempts.append(("plain text outside the topic", lambda: self._bot.send_message(
                chat_id=self._chat_id, text=to_plain_text(text), parse_mode=None, reply_parameters=reply,
                reply_markup=markup)))
        sent = await self._try("the answer", attempts)
        if sent is not None or markup is None:
            return sent, True
        bare: list[tuple[str, Callable[[], Awaitable[Message]]]] = [("plain text without the markup", lambda: self._bot.send_message(
            chat_id=self._chat_id, text=to_plain_text(text), parse_mode=None, reply_parameters=reply))]
        return await self._try("the answer", bare), False

    async def _try(self, what_sent: str, attempts: list[tuple[str, Callable[[], Awaitable[Message]]]]) -> Message | None:
        """The first form of `attempts` that goes out.

        Flood control is waited out and the same form retried, within RETRY_WAIT_MAX_S in all:
        stepping down would hit the same limit. Any other failure steps down to the next form."""
        waited = 0.0
        for what, attempt in attempts:
            for retry in range(_FLOOD_RETRIES + 1):
                try:
                    return await attempt()
                except TelegramRetryAfter as exc:
                    if retry == _FLOOD_RETRIES or waited + exc.retry_after > RETRY_WAIT_MAX_S:
                        log.warning("televibe: sending %s as %s hit flood control in chat %s", what_sent, what, self._chat_id)
                        break
                    waited += exc.retry_after
                    await asyncio.sleep(exc.retry_after)
                except Exception:
                    log.warning("televibe: sending %s as %s failed in chat %s", what_sent, what, self._chat_id, exc_info=True)
                    break
        log.error("televibe: every way of sending %s failed in chat %s", what_sent, self._chat_id)
        return None

    async def _react(self, emoji: str | None) -> None:
        """Set `emoji` as the reaction, or clear the reaction when it is None."""
        reaction = [ReactionTypeEmoji(emoji=emoji)] if emoji is not None else []
        await self._quietly(f"reaction {emoji or '(clear)'}", lambda: self._bot.set_message_reaction(
            chat_id=self._chat_id, message_id=self._message.message_id, reaction=reaction))

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
