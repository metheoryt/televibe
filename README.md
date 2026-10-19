# televibe

Run headless coding agents (Claude Code, Codex) one turn at a time, as
resumable sessions with a typed event stream.

televibe starts the agent, turns its output into common events
(`Started`, `Message`, `ToolUse`, `Warning`, then exactly one `Done` or
`Failed`), and kills what it started on timeout, cancel, or when the caller
walks away. Claude Code also reports the account's subscription usage as
`Limits` (five-hour and seven-day windows), and a call refused at the limit
fails with `FailReason.USAGE_LIMIT`. `Done.text` is the final answer; when
the agent answers, calls a bookkeeping tool and closes with a short line,
`engine.turn(..., quiet_tools={"<tool name>"})` keeps the answer whole, and
`Done.last_message` still has the agent's own last text. What to do with the output is the caller's
business: televibe never sends a message anywhere.

A one-shot call answers in a JSON schema with no session, no tools and no
place in the engine's queue: `await claude.ask(prompt, schema={...},
system="...")` returns `Answer(data, usage, limits)` or raises `AskFailed`
with the same reasons a turn fails for. It is for the model-only chores a bot
has around its turns — cleaning a transcript, classifying a message. See
SPEC.md section 12.

The optional Telegram layer does: `pip install 'televibe[telegram]'` adds
`televibe.telegram`, which shows a turn in a chat (reactions, typing or a live
draft, the answer) and keeps one session per reply chain. It is a set of parts
for an aiogram bot, not a framework. Every reaction can be replaced, and
`Reactions(done=None, failed_when_sent=False)` leaves a reaction only when
nothing could be sent at all. A live
draft shows the usage ("5h 35% · 7d 13%"); `Texts` relabels it and words the
limit message, and `Presenter(tz=...)` sets the zone of its reset time. A
person can also address the bot with a reaction: `reaction_added(update, "🙏")`
says whether this update just put that emoji on a message, and a message the
bot stored drives a turn like a live one. See SPEC.md section 13.

### Controls

An agent may end its answer with one fenced `control` block: an offer button, a
form of tick buttons, or a poll (SPEC.md section 13, CONTROL).

````text
```control
offer Да
```

```control
poll multiple
? Что из этого займы?
- Арман 20 000
- ничего
```

```control
form
- ты | *вы
- он | она
```
````

Pass `Presenter(..., controls=ControlTexts(...))` and the Presenter strips the
block from the answer and sends the keyboard or the poll. televibe keeps no
storage, so pressing is the bot's code:

- a callback handler calls `parse_press(cq.data)`, ignores `None`, answers the
  callback, and replaces the keyboard with `answered_keyboard(...)`;
  `Chains.of(chat_id, message_id)` gives the chain of the pressed message;
- a `poll_answer` handler maps `poll_id` back to the poll message the bot
  recorded when it was sent. A retracted vote arrives as a `poll_answer` with
  empty `option_ids`.

Install: `pip install televibe`, or `pip install 'televibe[telegram]'` with the
Telegram layer. Python 3.12+, Linux and macOS.

Status: 0.7, alpha. Tests: `uv run pytest`; live tests against real agents:
see `tests/test_live.py`. MIT licensed.

- [SPEC.md](https://github.com/metheoryt/televibe/blob/main/SPEC.md) is the specification. It is the law: numbered
  requirements, each claimed by a test.
