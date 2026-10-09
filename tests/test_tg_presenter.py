import asyncio
import dataclasses
from datetime import timedelta, timezone

import pytest
from tgkit import BOT_API_REACTIONS, SESSION, FakeBot, message, script

from televibe.events import (
    Done,
    Failed,
    FailReason,
    Limits,
    LimitWindow,
    Message,
    Started,
    ToolUse,
)
from televibe.telegram import presenter as presenter_module
from televibe.telegram.presenter import Presenter, Reactions, Texts

DONE = Done(SESSION, "the **answer**", {})


async def test_accepted_sets_the_queued_reaction():
    """REQ-PRESENT-1: accepted() puts 👀 on the message."""
    bot = FakeBot()
    await Presenter(bot, message(message_id=10)).accepted()
    assert bot.names() == ["set_message_reaction"]
    assert bot.calls[0][1]["chat_id"] == -1001 and bot.calls[0][1]["message_id"] == 10
    assert bot.reactions() == ["👀"]


async def test_done_turn_in_a_group():
    """REQ-PRESENT-2, REQ-PRESENT-3, REQ-PRESENT-4, REQ-PRESENT-5: 👨‍💻 on Started, the answer as a reply, then 👌."""
    bot = FakeBot()
    sent = await Presenter(bot, message(message_id=10)).show(script(Started(SESSION), Message("draft"), DONE))
    assert bot.reactions() == ["👨‍💻", "👌"]
    [(name, kw)] = bot.sends()
    assert name == "send_rich_message"
    assert kw["rich_message"].markdown == "the **answer**"
    assert kw["reply_parameters"].message_id == 10 and kw["reply_parameters"].allow_sending_without_reply is True
    assert kw["message_thread_id"] is None
    assert sent is not None and sent.message_id == 1001
    assert bot.names()[-1] == "set_message_reaction"  # the final reaction comes after the answer


async def test_answer_goes_to_the_forum_topic():
    """REQ-PRESENT-4: in a forum topic the answer is sent into that topic."""
    bot = FakeBot()
    await Presenter(bot, message(thread_id=5)).show(script(Started(SESSION), DONE))
    assert bot.sends()[0][1]["message_thread_id"] == 5


async def test_failed_turn_sends_partial_and_reason_but_not_detail(caplog):
    """REQ-PRESENT-5: partial, then the reason's text; detail goes to the log only; then 🤷."""
    bot = FakeBot()
    failed = Failed(FailReason.TIMEOUT, SESSION, "/Users/secret/kb: killed", "half an answer")
    await Presenter(bot, message()).show(script(Started(SESSION), failed))
    [(_, kw)] = bot.sends()
    assert kw["rich_message"].markdown == "half an answer\n\n" + Texts().timeout
    assert "/Users/secret" not in repr(bot.calls)
    assert "/Users/secret/kb: killed" in caplog.text
    assert bot.reactions() == ["👨‍💻", "🤷"]


async def test_failed_before_started_sends_the_reason_only():
    """REQ-PRESENT-5: no partial, no Started: just the reason's text and 🤷."""
    bot = FakeBot()
    await Presenter(bot, message()).show(script(Failed(FailReason.SPAWN_ERROR, None, "no binary", "")))
    assert bot.sends()[0][1]["rich_message"].markdown == Texts().spawn_error
    assert bot.reactions() == ["🤷"]


async def test_rejected_rich_message_falls_back_to_plain_text():
    """REQ-PRESENT-6: a rejected rich message is re-sent as plain text, with no parse mode."""
    bot = FakeBot(fail={"send_rich_message": 1})
    sent = await Presenter(bot, message(thread_id=5)).show(script(Started(SESSION), DONE))
    assert [name for name, _ in bot.sends()] == ["send_rich_message", "send_message"]
    kw = bot.sends()[1][1]
    assert kw["text"] == "the **answer**" and kw["parse_mode"] is None and kw["message_thread_id"] == 5
    assert kw["reply_parameters"].allow_sending_without_reply is True
    assert sent is not None


async def test_last_resort_is_plain_text_outside_the_topic():
    """REQ-PRESENT-6: if the topic rejects plain text too, the answer goes to the chat without a topic."""
    bot = FakeBot(fail={"send_rich_message": 1, "send_message": 1})
    sent = await Presenter(bot, message(thread_id=5)).show(script(Started(SESSION), DONE))
    assert [name for name, _ in bot.sends()] == ["send_rich_message", "send_message", "send_message"]
    assert "message_thread_id" not in bot.sends()[2][1]
    assert sent is not None


async def test_every_send_failing_returns_none_without_raising(caplog):
    """REQ-PRESENT-2, REQ-PRESENT-6, REQ-PRESENT-7: show returns None and raises nothing; each failure is logged."""
    bot = FakeBot(fail={"send_rich_message": 99, "send_message": 99})
    assert await Presenter(bot, message(thread_id=5)).show(script(Started(SESSION), DONE)) is None
    assert len(bot.sends()) == 3
    assert caplog.text.count("televibe: sending the answer") == 3
    assert bot.reactions()[-1] == "🤷"  # nothing was sent, so the turn is not marked done


async def test_forbidden_reactions_do_not_stop_the_answer():
    """REQ-PRESENT-7: a chat that forbids reactions still gets the answer."""
    bot = FakeBot(fail={"set_message_reaction": 99})
    presenter = Presenter(bot, message())
    await presenter.accepted()
    assert await presenter.show(script(Started(SESSION), DONE)) is not None


def test_reactions_and_texts_defaults():
    """REQ-PRESENT-8: frozen dataclasses, English defaults, reactions a bot may set, a text for every reason."""
    reactions, texts = Reactions(), Texts()
    assert {reactions.queued, reactions.working, reactions.done, reactions.failed} <= BOT_API_REACTIONS
    assert all(texts.for_reason(reason).isascii() and texts.for_reason(reason) for reason in FailReason)
    assert "{" not in texts.for_reason(FailReason.USAGE_LIMIT)  # no reset time known: no raw placeholder
    with pytest.raises(dataclasses.FrozenInstanceError):
        reactions.done = "🔥"  # type: ignore[misc]
    with pytest.raises(dataclasses.FrozenInstanceError):
        texts.timeout = "x"  # type: ignore[misc]


async def test_bot_replaces_reactions_and_texts():
    """REQ-PRESENT-8: a bot replaces any reaction or text."""
    bot = FakeBot()
    presenter = Presenter(bot, message(), reactions=Reactions(failed="😢"), texts=Texts(timeout="Слишком долго."))
    await presenter.show(script(Failed(FailReason.TIMEOUT, None, "", "")))
    assert bot.reactions() == ["😢"]
    assert bot.sends()[0][1]["rich_message"].markdown == "Слишком долго."


async def test_done_none_clears_the_reaction_after_the_answer():
    """REQ-PRESENT-5, REQ-PRESENT-8: with done=None the answer clears the reaction instead of setting one."""
    bot = FakeBot()
    presenter = Presenter(bot, message(), reactions=Reactions(done=None))
    await presenter.accepted()
    sent = await presenter.show(script(Started(SESSION), DONE))
    assert sent is not None
    assert bot.reactions() == ["👀", "👨‍💻", None]
    assert bot.calls[-1] == ("set_message_reaction", {"chat_id": -1001, "message_id": 10, "reaction": []})
    assert bot.names()[-2] == "send_rich_message"  # cleared only after the answer is out


async def test_done_none_still_marks_failure():
    """REQ-PRESENT-5, REQ-PRESENT-6, REQ-PRESENT-8: done=None changes only the answered Done; failures still get 🤷."""
    bot = FakeBot()
    await Presenter(bot, message(), reactions=Reactions(done=None)).show(script(Failed(FailReason.TIMEOUT, None, "", "")))
    assert bot.reactions() == ["🤷"]

    bot = FakeBot(fail={"send_rich_message": 99, "send_message": 99})
    sent = await Presenter(bot, message(), reactions=Reactions(done=None)).show(script(Started(SESSION), DONE))
    assert sent is None and bot.reactions() == ["👨‍💻", "🤷"]


async def test_failed_when_sent_false_clears_the_reaction_after_a_failure_text():
    """REQ-PRESENT-5, REQ-PRESENT-8: a sent failure text leaves no reaction; only a turn that sent nothing gets 🤷."""
    reactions = Reactions(done=None, failed="🥴", failed_when_sent=False)
    bot = FakeBot()
    sent = await Presenter(bot, message(), reactions=reactions).show(
        script(Started(SESSION), Failed(FailReason.TIMEOUT, SESSION, "", "")))
    assert sent is not None and bot.reactions() == ["👨‍💻", None]
    assert bot.names()[-2] == "send_rich_message"

    bot = FakeBot(fail={"send_rich_message": 99, "send_message": 99})
    sent = await Presenter(bot, message(), reactions=reactions).show(
        script(Started(SESSION), Failed(FailReason.TIMEOUT, SESSION, "", "")))
    assert sent is None and bot.reactions() == ["👨‍💻", "🥴"]


REJECTED = Limits(LimitWindow(1.0, 1791540600), LimitWindow(0.39, 1791986400), rejected=True, resets_at=1791540600)
LIMIT_FAILED = Failed(FailReason.USAGE_LIMIT, SESSION, "You've hit your session limit", "")


async def test_usage_limit_says_when_it_resets():
    """REQ-PRESENT-5, REQ-PRESENT-8: usage_limit reads the reset time of the last Limits, in the presenter's tz."""
    bot = FakeBot()
    await Presenter(bot, message()).show(script(Started(SESSION), REJECTED, LIMIT_FAILED))
    assert bot.sends()[0][1]["rich_message"].markdown == "The usage limit is reached. It resets at 10:10 UTC."
    assert bot.reactions()[-1] == "🤷"

    bot = FakeBot()
    texts = Texts(usage_limit="Лимит исчерпан, сброс в {resets_at:%H:%M}.")
    almaty = timezone(timedelta(hours=5))
    await Presenter(bot, message(), texts=texts, tz=almaty).show(script(Started(SESSION), REJECTED, LIMIT_FAILED))
    assert bot.sends()[0][1]["rich_message"].markdown == "Лимит исчерпан, сброс в 15:10."


async def test_usage_limit_text_never_shows_a_raw_placeholder():
    """REQ-PRESENT-5: with no reset time known the chat reads provider_error; a text that does not format is sent as is."""
    bot = FakeBot()
    await Presenter(bot, message()).show(script(Started(SESSION), LIMIT_FAILED))
    assert bot.sends()[0][1]["rich_message"].markdown == Texts().provider_error

    bot = FakeBot()
    texts = Texts(usage_limit="Лимит {когда}")
    await Presenter(bot, message(), texts=texts).show(script(Started(SESSION), REJECTED, LIMIT_FAILED))
    assert bot.sends()[0][1]["rich_message"].markdown == "Лимит {когда}"


async def test_draft_shows_usage_with_the_bots_labels():
    """REQ-PRESENT-3, REQ-PRESENT-8: the draft's status holds the latest usage windows, labelled by Texts."""
    bot = FakeBot()
    allowed = Limits(LimitWindow(0.42, 1), None, rejected=False, resets_at=1)
    events = script(Started(SESSION), REJECTED, allowed, Message("partial"), 0.2, DONE)
    texts = Texts(five_hour="5ч", seven_day="7д")
    await Presenter(bot, message("private", chat_id=7), heartbeat_s=0.05, texts=texts).show(events)
    last = [kw for name, kw in bot.calls if name == "send_rich_message_draft"][-1]["rich_message"].markdown
    assert last.startswith("<tg-thinking>0:00 · 5ч 42%</tg-thinking>")


PULSES = ("send_rich_message_draft", "send_chat_action")


def _pulses_stop_before_the_answer(bot: FakeBot) -> None:
    names = bot.names()
    answer = next(i for i, name in enumerate(names) if name in ("send_rich_message", "send_message"))
    assert all(name not in PULSES for name in names[answer:])


async def test_private_chat_gets_a_heartbeat_draft():
    """REQ-PRESENT-3: in a private chat a draft is re-sent every heartbeat_s, unchanged or not; no typing."""
    bot = FakeBot()
    events = script(Started(SESSION), Message("partial"), ToolUse("Bash", "ls"), 0.35, DONE)
    await Presenter(bot, message("private", chat_id=7, message_id=10), heartbeat_s=0.05, model="sonnet").show(events)
    drafts = [kw for name, kw in bot.calls if name == "send_rich_message_draft"]
    assert len(drafts) >= 4
    assert all(kw["draft_id"] == 10 and kw["chat_id"] == 7 for kw in drafts)
    last = drafts[-1]["rich_message"].markdown
    assert last.startswith("<tg-thinking>sonnet · 0:00 · Bash</tg-thinking>") and last.endswith("partial")
    assert "send_chat_action" not in bot.names()
    _pulses_stop_before_the_answer(bot)


async def test_group_gets_typing_every_few_seconds(monkeypatch):
    """REQ-PRESENT-3: in a group a typing action repeats every TYPING_EVERY_S; no draft."""
    monkeypatch.setattr(presenter_module, "TYPING_EVERY_S", 0.05)
    bot = FakeBot()
    await Presenter(bot, message(thread_id=5)).show(script(Started(SESSION), 0.3, DONE))
    typing = [kw for name, kw in bot.calls if name == "send_chat_action"]
    assert len(typing) >= 4
    assert all(kw["action"] == "typing" and kw["message_thread_id"] == 5 for kw in typing)
    assert "send_rich_message_draft" not in bot.names()
    _pulses_stop_before_the_answer(bot)


async def test_no_pulse_after_show_returns(monkeypatch):
    """REQ-PRESENT-3: the pulse is stopped for good once the answer is sent."""
    monkeypatch.setattr(presenter_module, "TYPING_EVERY_S", 0.02)
    bot = FakeBot()
    await Presenter(bot, message()).show(script(Started(SESSION), 0.1, DONE))
    assert "send_chat_action" in bot.names()
    count = len(bot.calls)
    await asyncio.sleep(0.1)
    assert len(bot.calls) == count


async def test_failing_draft_does_not_stop_the_turn():
    """REQ-PRESENT-7: every draft failing is logged and ignored; the answer still arrives."""
    bot = FakeBot(fail={"send_rich_message_draft": 99})
    sent = await Presenter(bot, message("private", chat_id=7), heartbeat_s=0.02).show(script(Started(SESSION), 0.1, DONE))
    assert sent is not None and bot.names().count("send_rich_message_draft") >= 2


async def test_hanging_draft_does_not_delay_the_answer():
    """REQ-PRESENT-3, REQ-PRESENT-7 (review focus 5): a draft call that never returns is cancelled at the end."""
    bot = FakeBot(hang=("send_rich_message_draft",))
    presenter = Presenter(bot, message("private", chat_id=7), heartbeat_s=0.02)
    sent = await asyncio.wait_for(presenter.show(script(Started(SESSION), 0.1, DONE)), 2)
    assert sent is not None
    assert bot.names().count("send_rich_message_draft") == 1  # the one call that hung


async def test_cancellation_propagates_and_stops_the_pulse(monkeypatch):
    """REQ-PRESENT-7: show propagates cancellation, and the pulse stops with it."""
    monkeypatch.setattr(presenter_module, "TYPING_EVERY_S", 0.02)
    bot = FakeBot()
    task = asyncio.create_task(Presenter(bot, message()).show(script(Started(SESSION), 60, DONE)))
    while "send_chat_action" not in bot.names():
        await asyncio.sleep(0.01)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    count = len(bot.calls)
    await asyncio.sleep(0.1)
    assert len(bot.calls) == count and not bot.sends()


async def test_flood_control_is_waited_out_with_the_same_form():
    """REQ-PRESENT-6: a rate limit is waited out and the same form is retried, not stepped down."""
    bot = FakeBot(flood={"send_rich_message": 2})
    sent = await Presenter(bot, message(thread_id=5)).show(script(Started(SESSION), DONE))
    assert [name for name, _ in bot.sends()] == ["send_rich_message"] * 3
    assert sent is not None and bot.reactions()[-1] == "👌"


async def test_endless_flood_control_still_ends(monkeypatch):
    """REQ-PRESENT-2, REQ-PRESENT-6: a rate limit that never lifts ends in None after a bounded number of tries."""
    bot = FakeBot(flood={"send_rich_message": 99, "send_message": 99})
    sent = await asyncio.wait_for(Presenter(bot, message(thread_id=5)).show(script(Started(SESSION), DONE)), 5)
    assert sent is None
    assert len(bot.sends()) < 30


async def test_hanging_reaction_does_not_hold_the_turn(monkeypatch):
    """REQ-PRESENT-7: a reaction call that never returns is given up on; the pulse runs and the answer goes out."""
    monkeypatch.setattr(presenter_module, "QUIET_TIMEOUT_S", 0.05)
    monkeypatch.setattr(presenter_module, "TYPING_EVERY_S", 0.02)
    bot = FakeBot(hang=("set_message_reaction",))
    presenter = Presenter(bot, message())
    await asyncio.wait_for(presenter.accepted(), 2)
    sent = await asyncio.wait_for(presenter.show(script(Started(SESSION), 0.2, DONE)), 2)
    assert sent is not None
    assert "send_chat_action" in bot.names()
