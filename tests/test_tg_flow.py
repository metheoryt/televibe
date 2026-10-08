from conftest import Kit
from tgkit import FakeBot, message

from televibe.telegram import Chains, MemoryChainStore, Presenter


async def test_a_reply_chain_from_mention_to_follow_up(engine, kit: Kit):
    """REQ-PRESENT-2, REQ-CHAIN-9: the spec's example end to end, with a chain turn, then a reply that continues it."""
    bot, chains = FakeBot(), Chains(MemoryChainStore())

    async def on_message(msg):
        presenter = Presenter(bot, msg, heartbeat_s=0.05)
        await presenter.accepted()
        chain = await chains.find(msg) or chains.new()
        async with chains.turn(engine, chain, msg, provider=kit.provider, cwd=kit.cwd, prompt="hi",
                               env=kit.env(record=f"{msg.message_id}.json")) as turn:
            sent = await presenter.show(turn)
        if sent is not None:
            await chains.link(chain, sent)
        return chain, sent

    first = message("private", chat_id=7, message_id=10)
    chain, sent = await on_message(first)
    assert sent is not None and bot.reactions() == ["👀", "👨‍💻", "👌"]
    assert bot.sends()[0][1]["rich_message"].markdown == kit.expected_text()
    assert "send_rich_message_draft" in bot.names()

    follow_up = message("private", chat_id=7, message_id=11, reply_to=sent)
    assert await chains.find(follow_up) == chain
    again, _ = await on_message(follow_up)
    assert again == chain
    argv = kit.record("11.json")["argv"]
    assert ("--resume" in argv) if kit.kind == "claude" else ("resume" in argv)
