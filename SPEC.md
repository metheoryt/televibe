# televibe — specification

**This document is the law.** Each requirement has an id and is claimed by a
test that names that id. Where the code and this document disagree, the code
is wrong. A requirement changes by editing this file first.

Section 13 lists what is not measured yet. Nothing there is a requirement.
Section 14 lists what is out of scope. Nothing there is deferred by accident.

---

## 1. What televibe is

A Python library that runs a headless coding agent (Claude Code, Codex) for
one turn. It returns the turn as a typed stream of events, and the turn can be
resumed later as the same session.

The caller decides everything about the conversation: when a turn starts, what
the prompt is, which session it continues, and what happens to the output.
The core decides nothing about presentation. It never sends a message
anywhere and knows nothing about Telegram or any other chat. An optional layer,
`televibe.telegram` (section 12), shows turns in Telegram and follows reply
chains. It is built on the core and installed separately, as an extra.

What televibe owns:

- starting the agent process with the right flags, environment and directory;
- turning the agent's output into common events;
- ordering turns, so two turns never write to one session or one lane at once;
- making sure every turn ends with exactly one terminal event;
- killing what it started: on timeout, on cancel, and when the caller walks
  away;
- remembering in-flight turns on disk, so a restart can find and report them.

### SCOPE

- **REQ-SCOPE-1** — No module under `src/` outside `televibe/telegram/`
  imports `aiogram`, `telegram`, `slack` or any other chat client. Modules in
  `televibe/telegram/` import no chat client but aiogram. A test fails the
  build otherwise.
- **REQ-SCOPE-2** — The package has no required runtime dependencies. A test
  reads `pyproject.toml` and fails if `dependencies` is not empty, or if the
  optional dependencies are anything but the `telegram` extra holding aiogram
  (REQ-TGPKG-1).
- **REQ-SCOPE-3** — Python 3.12 or newer, Linux and macOS. Killing a process
  group (section 7) has no Windows equivalent here, and importing the package
  on Windows raises a clear error.

---

## 2. Public API

```python
from televibe import Account, Access, ClaudeCode, Codex, Engine

claude = ClaudeCode(account=Account(home=Path("~/.claude-mybot")))

async with Engine(state_dir=Path("~/.local/state/mybot"), max_concurrent=1) as engine:
    session = claude.new_session(cwd=Path("~/kb"))
    async with engine.turn("Summarize README.md", session=session,
                           access=Access.READ_ONLY, lane="kb") as turn:
        async for event in turn:
            ...
```

### API

- **REQ-API-1** — The public surface is exactly: `Engine`, `Turn`, `Session`,
  `Account`, `Access`, `ClaudeCode`, `Codex`, `FailReason`, `Stranded`, the
  event classes of section 5, and `TelevibeError` with its subclasses. A test
  pins `televibe.__all__`.
- **REQ-API-2** — Every misuse that can be caught before a process starts
  raises `TelevibeError` (or a subclass) at the call. Examples: an access level
  the provider does not support, a session from another provider, a forbidden
  environment variable, or a turn on a closed engine. Nothing is silently
  adjusted.
- **REQ-API-3** — `engine.turn(...)` accepts: `prompt` (str), `session`
  (Session), `access` (Access, default `READ_ONLY`), `lane` (str or None),
  `model` (str or None), `instructions` (str or None, appended to the agent's
  system prompt), `env` (mapping, default empty), `add_dirs` (sequence of
  paths, default empty), `timeout_s` (positive number, default 900), `tag`
  (JSON-serializable mapping, default empty).

---

## 3. Accounts and isolation

An **account** is one directory where an agent keeps its login, settings,
plugins, MCP servers, hooks and session transcripts. For Claude Code this is
`CLAUDE_CONFIG_DIR`; for Codex it is `CODEX_HOME`.

### ACCOUNT

- **REQ-ACCOUNT-1** — `Account(home, credentials={})` requires `home`. There
  is no default, and televibe never falls back to `~/.claude` or `~/.codex`. A
  person's own agent setup is never used by accident.
- **REQ-ACCOUNT-2** — The provider sets its account variable
  (`CLAUDE_CONFIG_DIR` or `CODEX_HOME`) to `home` on every process it starts.
- **REQ-ACCOUNT-3** — `credentials` is a mapping of environment variables the
  account needs to log in, for example a long-lived token. It is passed to
  the child process and never written to disk by televibe. It is never logged,
  never included in an event, and never included in `repr()`.

### ENV

- **REQ-ENV-1** — The child environment is built from an allowlist, not copied
  from `os.environ`. Only these names are inherited: `PATH`, `HOME`, `USER`,
  `LOGNAME`, `SHELL`, `LANG`, `TERM`, `TMPDIR`, `TZ`, and `LC_*`. On top of
  these come the account variable, then `credentials`, then the turn's `env`.
- **REQ-ENV-2** — `ANTHROPIC_API_KEY`, `ANTHROPIC_AUTH_TOKEN` and
  `OPENAI_API_KEY` are refused in `credentials` and in a turn's `env`
  (`TelevibeError` at the call). televibe v1 runs agents on subscriptions only,
  and an API key would bill the API without anyone noticing.
- **REQ-ENV-3** — `CLAUDE_CONFIG_DIR` and `CODEX_HOME` are refused in
  `credentials` and in a turn's `env`. An account is chosen through
  `Account`, not overridden per turn or by its own credentials.
- **REQ-ENV-4** — A turn's `env` may hold tokens too. Like `credentials`, it is
  never logged, never included in an event, and never included in `repr()`.

---

## 4. Sessions

A **session** is one agent conversation that later turns can continue. It
belongs to one provider, one account and one working directory.

Measured on 2026-10-08 (Claude Code 2.1.294, codex-cli 0.161.0): both agents
resume a session by id from **any** directory without an error, and silently
run the continued turn in the new directory. Resuming in the wrong directory
is therefore a quiet bug, not a failure. It is the reason the directory lives
inside the session.

### SESSION

- **REQ-SESSION-1** — `Session` holds the provider kind, the account `home`,
  `cwd` (resolved to an absolute path at creation), `id` (str or None), and
  `started` (bool: whether the agent already has this session, so the next
  turn resumes it). It is immutable.
- **REQ-SESSION-2** — Sessions are created only by a provider:
  `provider.new_session(cwd, id=None)` for a new one,
  `provider.resume_session(cwd, id)` for one the agent already has, and
  `provider.load_session(text)` for one stored earlier. `load_session` raises
  if the stored provider kind or account `home` differs from the provider's.
  The session in `Started`, `Done` and `Failed` has `started` set.
- **REQ-SESSION-3** — `session.dump()` returns a string that `load_session`
  accepts, and that round-trips to an equal session. The string is JSON and
  contains no credentials.
- **REQ-SESSION-4** — Every turn on a session runs with `cwd` as the process
  working directory. The agent's own directory flag is set to it too, where
  one exists (`codex exec -C`).
- **REQ-SESSION-5** — `new_session(cwd, id=...)` with a fixed id is accepted
  only by a provider that lets the caller choose the id. Claude Code does
  (`--session-id`); Codex does not, and raises. A caller can then derive ids
  from its own keys and keep no table of them: `new_session(cwd, id=X)` for
  the first turn, `resume_session(cwd, X)` for every later one. Measured on
  2026-10-08 (Claude Code 2.1.294): Claude Code keys a transcript by
  directory and id, and `--session-id X` in another directory silently starts
  an empty session with the same id.
- **REQ-SESSION-6** — A new session without a fixed id gets its id from the
  agent. The id reaches the caller in `Started` and in the terminal event.
  For Claude Code, televibe generates a UUID4 and passes it as `--session-id`.
- **REQ-SESSION-7** — Two turns never run on the same session id at once. A
  second turn on a busy session waits in the queue (section 6). A session
  transcript that two processes write at once is corrupted.

---

## 5. The turn and its events

A turn is one prompt sent to one session. `engine.turn(...)` returns a `Turn`.
The caller enters it as an async context manager and iterates it.

Events, in the order they can appear:

| Event | Fields | Meaning |
|---|---|---|
| `Queued` | — | The turn is waiting for a free slot, lane or session. |
| `Started` | `session` | The agent process is running and reported its session id. |
| `Message` | `text` | One complete assistant message. |
| `ToolUse` | `name`, `detail` | The agent started a tool (a command, an edit, a search). |
| `Warning` | `text` | The agent reported a problem that did not end the turn. |
| `Done` | `session`, `text`, `usage` | Terminal: the turn finished. `text` is the final answer. |
| `Failed` | `reason`, `session`, `detail`, `partial` | Terminal: the turn did not finish. |

`FailReason` is one of `timeout`, `cancelled`, `session_lost`,
`provider_error`, `spawn_error`.

### TURN

- **REQ-TURN-1** — **Every turn ends with exactly one terminal event**, `Done`
  or `Failed`, and nothing follows it. This holds for a crash of the agent, a
  hang, a timeout, a cancel, a spawn failure, and a closing engine. It is the
  requirement the library exists to keep.
- **REQ-TURN-2** — `Queued` is emitted only if the turn cannot start at once.
  It is emitted at most once.
- **REQ-TURN-3** — `Started` is emitted exactly once for a turn whose agent
  reported its session, before any `Message` or `ToolUse`. `Started.session`
  has a non-empty id. A turn that fails before the agent reports a session
  has no `Started`.
- **REQ-TURN-4** — `Failed.session` is the session with its id when the agent
  reported one before failing, and `None` otherwise. `Failed.partial` is the
  text of the `Message` events emitted before the failure, joined with blank
  lines.
- **REQ-TURN-5** — A `Turn` is iterated by one consumer, once. A second
  iteration raises `TelevibeError`.
- **REQ-TURN-6** — Entering the turn's context is what queues it. Building a
  `Turn` without entering it starts nothing.
  Iterating a turn that was not entered raises `TelevibeError`.
- **REQ-TURN-7** — A turn whose agent reports an error result ends in
  `Failed`, never in `Done`. Claude Code's `result` event with
  `is_error: true` is such a report.
- **REQ-TURN-8** — `Done.text` is the agent's final answer: the `result`
  field for Claude Code, and the last `agent_message` for Codex.
- **REQ-TURN-9** — `Done.usage` is the agent's token counts, passed through
  as a plain mapping. Its keys depend on the provider.

---

## 6. Ordering: slots, lanes and sessions

### QUEUE

- **REQ-QUEUE-1** — At most `max_concurrent` agent processes run at once
  across the engine (default 1).
- **REQ-QUEUE-2** — A turn with a `lane` does not start while another turn of
  the same lane runs. The caller picks lanes; for example, all turns that
  write one git checkout share one lane.
- **REQ-QUEUE-3** — A turn does not start while another turn runs on the same
  session id (REQ-SESSION-7).
- **REQ-QUEUE-4** — When a slot frees, the oldest queued turn whose lane and
  session are both free starts. A blocked turn does not hold back an
  unrelated one behind it.
- **REQ-QUEUE-5** — The timeout counts from the moment the process starts,
  not from queueing.
- **REQ-QUEUE-6** — `Engine` is an async context manager. A turn on an engine
  that is not entered, or already closed, raises `TelevibeError`.

---

## 7. Cancel, timeout and walking away

### CANCEL

- **REQ-CANCEL-1** — The agent runs in its own process group. Killing a turn
  sends `SIGKILL` to the whole group, so commands the agent started die with
  it.
- **REQ-CANCEL-2** — `turn.cancel()` is idempotent and safe at any point.
  Before the process starts, it removes the turn from the queue. While the
  process runs, it kills it. Either way the turn ends with
  `Failed(cancelled)`.
- **REQ-CANCEL-3** — A cancel that lands while the process is being spawned
  kills the process as soon as it exists.
- **REQ-CANCEL-4** — A turn that runs longer than `timeout_s` is killed and
  ends with `Failed(timeout)`.
- **REQ-CANCEL-5** — **Leaving the turn's context kills the turn** if it has
  not ended, whatever the exit path: a `break`, an exception, or a cancelled
  task. An agent left running with nobody reading it keeps spending the
  subscription.
- **REQ-CANCEL-6** — A `Turn` that is garbage-collected while its process
  still runs is killed by a finalizer, and a warning is logged. This is the
  backstop for a caller who entered the turn without `async with` (calling
  `__aenter__` directly) and dropped it.
- **REQ-CANCEL-7** — Closing the engine cancels every queued and running turn.
  Each ends with `Failed(cancelled)` and `detail` saying the engine closed.

---

## 8. Running the agent process

### RUN

- **REQ-RUN-1** — The prompt is written to the child's stdin, and then stdin
  is closed. stdin is never left open: Codex waits on an open stdin
  ("Reading additional input from stdin...").
- **REQ-RUN-2** — stderr is read concurrently with stdout. A child that fills
  its stderr pipe while only stdout is read blocks forever.
- **REQ-RUN-3** — A stdout line that is not valid JSON, not a JSON object, or
  longer than the read limit (8 MiB) is skipped. One bad line does not end the
  turn.
- **REQ-RUN-4** — A process that cannot be started ends the turn with
  `Failed(spawn_error)`. It is never raised to the caller.
- **REQ-RUN-5** — A process that exits without a terminal record ends with
  `Failed(provider_error)`. `detail` carries the last 4 KiB of stderr; if
  stderr is empty, it carries the exit code.
- **REQ-RUN-6** — `session_lost` is recognized only from recorded outputs.
  Anything else that fails is `provider_error`. The recorded outputs are:
  - Claude Code: a `result` event with `is_error: true`, plus stderr
    containing `No conversation found with session ID`;
  - Codex: a non-zero exit, no `thread.started` event, plus stderr containing
    `no rollout found for thread id`.
- **REQ-RUN-7** — A turn ends when the agent's process has exited. After a
  terminal record the agent gets 10 seconds to exit, then it is killed. Once
  the agent's process exits, its pipes get 2 seconds to drain, and then
  everything left in its process group is killed. A background command that
  holds the agent's stdout cannot keep the turn open.

---

## 9. Access levels

`Access` names how much the agent may change: `READ_ONLY`, `WORKSPACE_WRITE`
(write inside `cwd` and `add_dirs`) and `FULL`.

**The same name does not give the same guarantee across providers.** Codex
`read-only` is an operating-system sandbox. Claude Code's `plan` is Claude
Code's own permission check in the same process, with the same user and the
same credentials. It is a strong default, but it is not containment.

### ACCESS

- **REQ-ACCESS-1** — Each provider declares the access levels it supports. A
  turn with another level raises `TelevibeError` at the call.
- **REQ-ACCESS-2** — Claude Code: `READ_ONLY` maps to
  `--permission-mode plan` and `FULL` to `--permission-mode bypassPermissions`.
  `WORKSPACE_WRITE` is not supported: Claude Code has no headless mode that
  allows edits and refuses everything else without prompting a person.
- **REQ-ACCESS-3** — Codex: `READ_ONLY` maps to `--sandbox read-only`,
  `WORKSPACE_WRITE` to `--sandbox workspace-write`, and `FULL` to
  `--dangerously-bypass-approvals-and-sandbox`.
- **REQ-ACCESS-4** — The default is `READ_ONLY`. Write access is something the
  caller asks for.
- **REQ-ACCESS-5** — No mode that waits for a person to approve an action is
  ever passed. Behind a library, nobody answers, and the turn hangs until the
  timeout.

---

## 10. Providers

### CLAUDE

- **REQ-CLAUDE-1** — The command is `claude -p --output-format stream-json
  --verbose --permission-mode <mode>`. It is followed by `--model` when set,
  `--append-system-prompt` when `instructions` is set, one `--add-dir` per
  entry of `add_dirs`, and exactly one of `--session-id <id>` (a new session)
  or `--resume <id>`. `--verbose` is required: without it the CLI refuses
  `stream-json` in print mode.
- **REQ-CLAUDE-2** — Parsing:
  - `system` with subtype `init` maps to `Started`;
  - `text` blocks of one `assistant` event map to one `Message`;
  - each `tool_use` block maps to a `ToolUse`;
  - `result` maps to `Done`, or to `Failed` when `is_error` (REQ-TURN-7).

### CODEX

- **REQ-CODEX-1** — Every turn runs
  `codex exec --json --skip-git-repo-check -C <cwd>` followed by the access
  flag, `-m` when `model` is set, one `--add-dir` per entry of `add_dirs`,
  and `-c developer_instructions=...` when `instructions` is set. A resumed
  session then adds `resume <id>`. The prompt goes to stdin, named by a final
  `-` (REQ-RUN-1). These options come before `resume` because
  `codex exec resume` rejects `--sandbox`, `-C` and `--add-dir` after it
  (exit code 2; measured with codex-cli 0.161.0 on 2026-10-08).
  Measured on 2026-10-08: `--sandbox read-only` and `-C` placed before
  `resume` hold on the resumed turn (a write was refused; `pwd` was the `-C`
  directory while the process ran in `/`).
- **REQ-CODEX-2** — `instructions` is passed as
  `-c developer_instructions=<value>`, where the value is a TOML basic string.
  Quotes, backslashes and newlines in `instructions` survive: a test passes
  each of them through.
- **REQ-CODEX-3** — Parsing:
  - `thread.started` maps to `Started`;
  - `item.completed` of type `agent_message` maps to `Message`;
  - `item.started` of any other type except `reasoning` maps to `ToolUse`
    (`detail` is the command for `command_execution`);
  - `item.completed` of type `error` maps to `Warning`;
  - `turn.completed` maps to `Done`.

---

## 11. State on disk

The only state televibe keeps is one marker file per in-flight turn. Session
history is the agent's own transcript, kept in the account.

### STATE

- **REQ-STATE-1** — A marker `<state_dir>/turns/<turn_id>.json` is written
  before the process starts. It holds `turn_id`, `tag`, the session dump, and
  the start time. Once known, the process group id, the process start time
  and the session id are added. Writes are atomic: a temp file, then a rename.
- **REQ-STATE-2** — The marker is removed when the turn's context exits after
  the terminal event, not when the event is produced. A caller that crashes
  between receiving `Done` and acting on it finds the turn again after a
  restart.
- **REQ-STATE-3** — `engine.stranded()` returns a `Stranded` for every marker
  left by an earlier process: `turn_id`, `tag`, `session` (with its id, if it
  was known), `started_at`, and `killed` (bool). It is called by the caller,
  typically right after entering the engine.
  The `session` there is not bound to a provider:
  `provider.load_session(stranded.session.dump())` gives one a turn can run
  on, and `engine.turn` with the unbound one raises `TelevibeError`.
  One engine at a time uses a `state_dir`: entering a second `Engine` on a
  `state_dir` that is in use raises `TelevibeError`, so `stranded()` never
  reports, or kills, a live turn of another engine.
- **REQ-STATE-4** — Before returning, `stranded()` kills the recorded process
  group, but only if its leader still exists **and** its start time matches
  the recorded one. A recycled pid is never killed. `killed` says whether a
  kill happened.
- **REQ-STATE-5** — `engine.forget(turn_id)` removes a stranded marker. The
  markers stay until then, so a caller that crashes while handling them sees
  them again.
- **REQ-STATE-6** — A marker never contains `credentials` or the turn's `env`.

---

## 12. The Telegram layer

`televibe.telegram` is an optional layer for bots that run turns from
Telegram messages. It is a set of parts, not a framework: the bot keeps its
own aiogram handlers and its own policy — who may write, whether a message is
addressed to the bot, what the prompt is — and uses the parts for what every
such bot repeats. It is installed with the `telegram` extra:
`pip install televibe[telegram]`.

```python
from televibe import Access, ClaudeCode, Engine
from televibe.telegram import Chains, Presenter

chains = Chains(store)                      # the bot's own ChainStore

async def on_mention(message):
    presenter = Presenter(bot, message)
    await presenter.accepted()              # 👀 at once, even while queued
    chain = await chains.find(message) or chains.new()
    async with chains.turn(engine, chain, message, provider=claude, cwd=kb,
                           prompt=text, access=access_for(message.from_user)) as turn:
        sent = await presenter.show(turn)   # 👨‍💻, typing or draft, the answer
    if sent is not None:
        await chains.link(chain, sent)
```

The layer adds nothing to the core: a turn here is a core turn, with the same
events and the same guarantees (section 5).

### TGPKG

- **REQ-TGPKG-1** — aiogram is required only by the `telegram` extra, declared
  as `aiogram>=3.31,<4`. 3.31 is the version rich messages and drafts were
  measured on; earlier versions are not checked.
- **REQ-TGPKG-2** — `import televibe` does not import `televibe.telegram` or
  aiogram. A test checks `sys.modules` in a fresh interpreter.
- **REQ-TGPKG-3** — Importing `televibe.telegram` without aiogram installed
  raises `ImportError` whose message names `televibe[telegram]`.
- **REQ-TGPKG-4** — The public surface of `televibe.telegram` is exactly:
  `Chain`, `ChainStore`, `Chains`, `MemoryChainStore`, `Presenter`,
  `Reactions`, `Texts`, and the submodule `render`. A test pins
  `televibe.telegram.__all__`.

### RENDER

`televibe.telegram.render` turns an answer into what Telegram accepts. The
answer is GitHub-flavored Markdown, which Telegram's rich message parses
itself, so rendering is a length limit plus fallbacks.

- **REQ-RENDER-1** — `to_rich_message(text)` returns a rich message of at most
  32768 characters. A longer text is cut, an unclosed code fence is closed,
  and a truncation note is appended. An empty or blank text becomes a short
  "no output" note: the message is never empty.
- **REQ-RENDER-2** — `to_plain_text(text)` returns plain text of at most 4096
  characters under the same rules. It is the fallback when a rich message is
  rejected.
- **REQ-RENDER-3** — `to_draft_message(text, status)` puts `status` in a
  thinking block above the text. `status_line(...)` is never empty: it holds at
  least the elapsed time. Angle brackets from a tool name are replaced, so a
  name cannot break the thinking block.

### PRESENT

`Presenter(bot, message, *, reactions=Reactions(), texts=Texts(),
heartbeat_s=8.0, model=None)` shows one turn started by `message`.

| Moment | Reaction on `message` | Also |
|---|---|---|
| `accepted()` | `reactions.queued` (👀) | — |
| `Started` | `reactions.working` (👨‍💻) | typing in a group, a live draft in a private chat |
| `Done`, answer sent | `reactions.done` (👌) | — |
| `Failed`, answer sent | `reactions.failed` (🤷) | — |
| no way of sending worked | `reactions.failed` (🤷) | — |

- **REQ-PRESENT-1** — `accepted()` sets the `queued` reaction. The bot calls it
  before `chains.turn`, so a message waiting in a chain is marked at once.
- **REQ-PRESENT-2** — `show(events)` consumes an async iterable of core events
  (a core `Turn` or a chain turn) to its terminal event and returns the sent
  answer as an aiogram `Message`, or `None` if every way of sending failed.
- **REQ-PRESENT-3** — On `Started` the reaction becomes `working`. In a private
  chat a draft starts: the latest message text with a status line (elapsed
  time, current tool), re-sent every `heartbeat_s` seconds whether or not
  anything changed. In any other chat a typing action is sent every 4 seconds
  instead. Both stop before the answer is sent.
- **REQ-PRESENT-4** — The answer is a reply to `message`, in its forum topic
  if it has one, sent with `allow_sending_without_reply`, so a deleted
  `message` does not lose the answer.
- **REQ-PRESENT-5** — `Done` sends `Done.text`. `Failed` sends `Failed.partial`,
  if any, followed by the text `texts` gives for `Failed.reason`. `Failed.detail`
  is logged, never sent: it can hold local paths. Then the reaction becomes
  `done` or `failed`.
- **REQ-PRESENT-6** — **A turn never ends in silence.** Sending tries, in
  order: a rich message, plain text, plain text outside the forum topic. Each
  failure is logged and the next is tried. Flood control is the exception: it
  is waited out and the same form is retried, a few times and at most 30
  seconds in all, since a plainer form would hit the same limit.
- **REQ-PRESENT-7** — A failed reaction, draft or typing action is logged and
  ignored. A chat may forbid reactions; the answer still arrives. Each such
  call gets a few seconds; one that takes longer is given up on, so it cannot
  hold the answer or the chain.
  `show` raises nothing for a Telegram error; it does propagate cancellation.
- **REQ-PRESENT-8** — `Reactions` and `Texts` are frozen dataclasses with
  English defaults. A bot replaces any of them. The default reactions are all
  in the Bot API's list of reactions a bot may set.

### CHAIN

A **chain** is a conversation in a chat that follows replies: an answer to a
message in a chain continues that chain's session. The bot decides the policy
for each message — continue the chain found, start a new one, or fork — and
`Chains` provides the mechanism: finding the chain, running its turns one at
a time, and keeping its session current.

`ChainStore` is a protocol of four async methods. The bot implements it on
whatever storage it has; `MemoryChainStore` is the in-memory one, for tests
and simple bots.

```python
class ChainStore(Protocol):
    async def chain_of(self, chat_id: int, message_id: int) -> str | None: ...
    async def link(self, chat_id: int, message_id: int, chain_id: str) -> None: ...
    async def session_of(self, chain_id: str) -> str | None: ...   # a session dump
    async def save(self, chain_id: str, session: str) -> None: ...
```

- **REQ-CHAIN-1** — `await chains.find(message)` returns the chain of the
  message `message` replies to, or `None` when it replies to nothing or to a
  message no chain holds. `chains.new()` returns a chain with a fresh id and
  stores nothing.
- **REQ-CHAIN-2** — `chains.fork(chain)` raises `TelevibeError`. Forking a
  session needs core support (Claude Code has `--fork-session`; Codex is not
  measured), and v1 of the core has none.
- **REQ-CHAIN-3** — `chains.turn(engine, chain, message, *, provider, cwd,
  prompt, **kwargs)` is an async context manager. `kwargs` may not hold
  `session`: the chain chooses it (REQ-CHAIN-5). On entering it first links
  `message` to the chain, so a reply to `message` that arrives while this turn
  runs joins the same chain. The rest of `kwargs` goes to `engine.turn`
  unchanged.
- **REQ-CHAIN-4** — Turns of one chain run one at a time, in the order they
  entered. Turns of different chains do not wait for each other here (the
  core's slots still apply). The order is kept in memory: turns waiting when
  the process stops are lost; a turn that was running is reported by
  `engine.stranded()` (REQ-STATE-3).
- **REQ-CHAIN-5** — **The session is read when the turn's wait ends, not when
  it enters.** It is `provider.load_session(store.session_of(chain))`, or
  `provider.new_session(cwd)` when the chain has none. A turn queued behind a
  chain's first turn therefore resumes the session that turn started, instead
  of starting it again.
- **REQ-CHAIN-6** — The session is saved on `Started` and again on the
  terminal event when it carries a session. Saving on `Started` keeps a Codex
  session id even if the turn fails later.
- **REQ-CHAIN-7** — The turn's `tag` gets `chain`, `chat_id` and `message_id`,
  so a bot can answer a stranded turn after a restart. A caller `tag` that
  already has one of these keys raises `TelevibeError`.
- **REQ-CHAIN-8** — A store error before the core turn starts propagates to
  the caller. A store error while saving is logged and does not end the turn;
  the answer still arrives. If both saves of a chain's first turn fail, its
  next turn starts a new session: the conversation starts over rather than failing.
- **REQ-CHAIN-9** — `await chains.link(chain, sent)` links the bot's answer to
  the chain, so a reply to the answer continues it.

---

## 13. Not measured yet

These are open questions, not requirements. Each one is settled by a
measurement before it becomes a requirement.

- **Codex `turn.failed` and other error records.** Only the success stream and
  the missing-session case have been seen. Until a recording exists, a turn
  that ends without `turn.completed` is `provider_error` (REQ-RUN-5).
- **Whether Codex `developer_instructions` persists across `resume`** or must
  be passed again. It also depends on whether it adds to Codex's base
  instructions or replaces any of them. Only that it takes effect has been
  seen. Until then, televibe passes it on every turn that sets it.
- **Codex `workspace-write` with no person to approve.** `read-only` was
  measured: a refused write is reported in the answer, and nothing hangs.
  `workspace-write` with a command outside the workspace has not been tried.
- **Headless subscription login on Linux.** A box with no login has no
  `.credentials.json` in the account. `claude setup-token` makes a long-lived
  token; the documented way to pass it is the `CLAUDE_CODE_OAUTH_TOKEN`
  variable, which `Account.credentials` carries. This has not been tried on
  the deploy host yet.
- **Separate failure reasons for an exhausted limit or an expired login.**
  They are added once each is recorded. Until then, both are `provider_error`.
- **Whether `SIGTERM` before `SIGKILL` keeps the partial turn in the
  transcript.** `SIGKILL` is the one measured to work.
- **Reactions in the target groups.** The default reactions are on the Bot
  API's list, but a group can narrow the reactions it allows. REQ-PRESENT-7
  makes this harmless; whether a given group allows them is not checked.
- **The lowest aiogram that works.** 3.31 is the version measured; the
  floor may be lower.

## 14. Out of scope for v1

- Agents calling each other, account pools, and switching accounts on a limit.
  These are the next specification.
- Text streamed word by word. Claude Code can do it
  (`--include-partial-messages`); Codex `exec --json` cannot. v1 events are
  the part both share.
- Chats other than Telegram. A Slack or Discord layer would sit beside
  `televibe.telegram` under the same rule (REQ-SCOPE-1).
- In the Telegram layer: a stop button on the draft, forking a chain
  (REQ-CHAIN-2), a chain order that survives a restart, splitting a long answer
  into several messages, settings per chat, deciding whether a message is
  addressed to the bot, and choosing access by sender. The last two are the
  bot's policy.
- MCP servers, plugins and hooks per turn. They belong to the account.
- Windows.

## 15. Tests

- **REQ-TEST-1** — Every requirement id above appears in at least one test
  name or docstring. A test fails the build if one does not.
- **REQ-TEST-2** — Contract tests (sections 4 to 8, and 11) run against every
  provider through **fake agent binaries**. The fakes replay output recorded
  from real runs. Environment knobs make them hang, crash mid-stream, lose the
  session, flood stderr, or emit a broken line. The Telegram layer's tests
  (section 12) use the same fakes and a fake `Bot` that records its calls; no
  test talks to Telegram.
- **REQ-TEST-3** — Recorded outputs live in `tests/fixtures/<provider>/`, with
  the CLI version they were recorded with. Local paths and ids in them are
  replaced with placeholders.
- **REQ-TEST-4** — One live test per provider runs a real two-turn session
  (start, then resume). It is marked `live` and skipped unless an account
  directory is given in the environment.
