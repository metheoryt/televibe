# televibe

Run headless coding agents (Claude Code, Codex) one turn at a time, as
resumable sessions with a typed event stream.

televibe starts the agent, turns its output into common events
(`Started`, `Message`, `ToolUse`, `Warning`, then exactly one `Done` or
`Failed`), and kills what it started on timeout, cancel, or when the caller
walks away. Claude Code also reports the account's subscription usage as
`Limits` (five-hour and seven-day windows), and a call refused at the limit
fails with `FailReason.USAGE_LIMIT`. What to do with the output is the caller's
business: televibe never sends a message anywhere.

The optional Telegram layer does: `pip install 'televibe[telegram]'` adds
`televibe.telegram`, which shows a turn in a chat (reactions, typing or a live
draft, the answer) and keeps one session per reply chain. It is a set of parts
for an aiogram bot, not a framework. Every reaction can be replaced, and
`Reactions(done=None, failed_when_sent=False)` leaves a reaction only when
nothing could be sent at all. A live
draft shows the usage ("5h 35% · 7d 13%"); `Texts` relabels it and words the
limit message, and `Presenter(tz=...)` sets the zone of its reset time. See SPEC.md
section 12.

Install: `pip install televibe`, or `pip install 'televibe[telegram]'` with the
Telegram layer. Python 3.12+, Linux and macOS.

Status: 0.1, alpha. Tests: `uv run pytest`; live tests against real agents:
see `tests/test_live.py`. MIT licensed.

- [SPEC.md](https://github.com/metheoryt/televibe/blob/main/SPEC.md) is the specification. It is the law: numbered
  requirements, each claimed by a test.
