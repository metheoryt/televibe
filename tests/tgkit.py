"""Telegram messages and a fake aiogram Bot for the Telegram layer's tests (REQ-TEST-2)."""

import asyncio
from collections.abc import Mapping
from datetime import UTC, datetime
from pathlib import Path

from aiogram.exceptions import TelegramBadRequest, TelegramRetryAfter
from aiogram.types import Chat, Message, User

from televibe.session import Session

SESSION = Session("claude", Path("/home/acc"), Path("/work"), "s-1", True)

# The Bot API's list of emoji a bot may set as a reaction (ReactionTypeEmoji).
BOT_API_REACTIONS = frozenset(
    "👍 👎 ❤ 🔥 🥰 👏 😁 🤔 🤯 😱 🤬 😢 🎉 🤩 🤮 💩 🙏 👌 🕊 🤡 🥱 🥴 😍 🐳 ❤‍🔥 🌚 🌭 💯 🤣 ⚡ 🍌 🏆 💔 🤨 😐 🍓 🍾 💋 🖕 "
    "😈 😴 😭 🤓 👻 👨‍💻 👀 🎃 🙈 😇 😨 🤝 ✍ 🤗 🫡 🎅 🎄 ☃ 💅 🤪 🗿 🆒 💘 🙉 🦄 😘 💊 🙊 😎 👾 🤷‍♂ 🤷 🤷‍♀ 😡".split()
)


def message(
    chat_type: str = "supergroup",
    *,
    chat_id: int = -1001,
    message_id: int = 10,
    thread_id: int | None = None,
    reply_to: Message | None = None,
) -> Message:
    """A message as aiogram hands it to a handler; `thread_id` puts it in a forum topic."""
    return Message(
        message_id=message_id,
        date=datetime.now(UTC),
        chat=Chat(id=chat_id, type=chat_type),
        message_thread_id=thread_id,
        is_topic_message=True if thread_id is not None else None,
        reply_to_message=reply_to,
        from_user=User(id=7, is_bot=False, first_name="Ann"),
    )


class FakeBot:
    """Records every call as (method, kwargs).

    `fail` maps a method to how many of its calls raise a Telegram error (99 = all).
    `flood` maps a method to how many of its calls hit flood control (retry after 0 s; 99 = all).
    `hang` names methods whose calls never return.
    """

    def __init__(
        self,
        *,
        fail: Mapping[str, int] | None = None,
        flood: Mapping[str, int] | None = None,
        hang: tuple[str, ...] = (),
    ) -> None:
        self.calls: list[tuple[str, dict]] = []
        self.fail = dict(fail or {})
        self.flood = dict(flood or {})
        self.hang = set(hang)
        self._last_id = 1000

    def names(self) -> list[str]:
        return [name for name, _ in self.calls]

    def reactions(self) -> list[str | None]:
        """Each reaction set, in order; None where the reaction was cleared."""
        return [kw["reaction"][0].emoji if kw["reaction"] else None
                for name, kw in self.calls if name == "set_message_reaction"]

    def sends(self) -> list[tuple[str, dict]]:
        return [(name, kw) for name, kw in self.calls if name in ("send_rich_message", "send_message")]

    async def _call(self, name: str, kwargs: dict) -> None:
        self.calls.append((name, kwargs))
        if name in self.hang:
            await asyncio.Event().wait()
        if self.flood.get(name, 0) > 0:
            self.flood[name] -= 1
            raise TelegramRetryAfter(method=None, message=f"Too Many Requests: {name}", retry_after=0)
        if self.fail.get(name, 0) > 0:
            self.fail[name] -= 1
            raise TelegramBadRequest(method=None, message=f"Bad Request: {name} rejected")

    def _sent(self, kwargs: dict) -> Message:
        self._last_id += 1
        return Message(
            message_id=self._last_id,
            date=datetime.now(UTC),
            chat=Chat(id=kwargs["chat_id"], type="supergroup"),
            message_thread_id=kwargs.get("message_thread_id"),
        )

    async def set_message_reaction(self, **kwargs) -> bool:
        await self._call("set_message_reaction", kwargs)
        return True

    async def send_chat_action(self, **kwargs) -> bool:
        await self._call("send_chat_action", kwargs)
        return True

    async def send_rich_message_draft(self, **kwargs) -> bool:
        await self._call("send_rich_message_draft", kwargs)
        return True

    async def send_rich_message(self, **kwargs) -> Message:
        await self._call("send_rich_message", kwargs)
        return self._sent(kwargs)

    async def send_message(self, **kwargs) -> Message:
        await self._call("send_message", kwargs)
        return self._sent(kwargs)


async def script(*steps):
    """Yield core events in order; a number among the steps is a pause in seconds."""
    for step in steps:
        if isinstance(step, (int, float)):
            await asyncio.sleep(step)
        else:
            yield step
