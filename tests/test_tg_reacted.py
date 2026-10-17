from datetime import UTC, datetime

from aiogram.types import (
    Chat,
    Message,
    MessageReactionUpdated,
    ReactionTypeCustomEmoji,
    ReactionTypeEmoji,
    User,
)
from conftest import Kit
from tgkit import FakeBot, message

from televibe.telegram import Chains, MemoryChainStore, Presenter, reaction_added


def update(old: list, new: list, *, message_id: int = 10) -> MessageReactionUpdated:
    return MessageReactionUpdated(
        chat=Chat(id=7, type="private"),
        message_id=message_id,
        user=User(id=7, is_bot=False, first_name="Ann"),
        date=datetime.now(UTC),
        old_reaction=old,
        new_reaction=new,
    )


def emoji(e: str) -> ReactionTypeEmoji:
    return ReactionTypeEmoji(emoji=e)


def test_added_when_new_and_not_old():
    """REQ-REACTED-1: the emoji is in new_reaction and was not in old_reaction."""
    assert reaction_added(update([], [emoji("🙏")]), "🙏")
    assert reaction_added(update([emoji("💔")], [emoji("🙏")]), "🙏")


def test_not_added_when_kept_removed_or_other():
    """REQ-REACTED-1: a kept, removed or different emoji is not an addition."""
    assert not reaction_added(update([emoji("🙏")], [emoji("🙏"), emoji("👍")]), "🙏")
    assert not reaction_added(update([emoji("🙏")], []), "🙏")
    assert not reaction_added(update([], [emoji("👍")]), "🙏")
    assert not reaction_added(update([], []), "🙏")


def test_custom_and_paid_reactions_never_match():
    """REQ-REACTED-1: only plain emoji reactions count."""
    custom = ReactionTypeCustomEmoji(custom_emoji_id="🙏")
    assert not reaction_added(update([], [custom]), "🙏")


def test_variation_selector_is_ignored():
    """REQ-REACTED-1: U+FE0F on either side does not change the answer."""
    assert reaction_added(update([], [emoji("❤️")]), "❤")
    assert reaction_added(update([], [emoji("❤")]), "❤️")
    assert not reaction_added(update([emoji("❤")], [emoji("❤️")]), "❤")


async def test_a_turn_from_a_stored_message(engine, kit: Kit):
    """REQ-REACTED-2: a message rebuilt from its JSON drives Presenter and Chains; Telegram is reached
    only through the bot given, and a stored reply to an answer continues that chain."""
    bot, chains = FakeBot(), Chains(MemoryChainStore())
    first = message("private", chat_id=7, message_id=10)
    presenter = Presenter(bot, first, heartbeat_s=0.05)
    chain = chains.new()
    async with chains.turn(engine, chain, first, provider=kit.provider, cwd=kit.cwd, prompt="hi",
                           env=kit.env(record="10.json")) as turn:
        sent = await presenter.show(turn)
    assert sent is not None
    await chains.link(chain, sent)

    stored = message("private", chat_id=7, message_id=11, reply_to=sent).model_dump_json(exclude_none=True)
    rebuilt = Message.model_validate_json(stored)  # what a bot has when a reaction names message 11
    assert await chains.find(rebuilt) == chain
    presenter = Presenter(bot, rebuilt, heartbeat_s=0.05)
    await presenter.accepted()
    async with chains.turn(engine, chain, rebuilt, provider=kit.provider, cwd=kit.cwd, prompt="again",
                           env=kit.env(record="11.json")) as turn:
        again = await presenter.show(turn)
    assert again is not None
    assert bot.reactions()[-3:] == ["👀", "👨‍💻", "👌"]
    assert bot.sends()[-1][1]["reply_parameters"].message_id == 11
    argv = kit.record("11.json")["argv"]
    assert ("--resume" in argv) if kit.kind == "claude" else ("resume" in argv)
