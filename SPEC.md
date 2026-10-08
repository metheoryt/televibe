# televibe — specification

**This document is the law.** Each requirement has an id and is claimed by a
test that names that id. Where the code and this document disagree, the code
is wrong. A requirement changes by editing this file first.

Section 12 lists what is not measured yet. Nothing there is a requirement.
Section 13 lists what is out of scope. Nothing there is deferred by accident.

---

## 1. What televibe is

A Python library that runs a headless coding agent (Claude Code, Codex) for
one turn. It returns the turn as a typed stream of events, and the turn can be
resumed later as the same session.

The caller decides everything about the conversation: when a turn starts, what
the prompt is, which session it continues, and what happens to the output.
televibe decides nothing about presentation. It never sends a message anywhere.
It knows nothing about Telegram or any other chat.

What televibe owns:

- starting the agent process with the right flags, environment and directory;
- turning the agent's output into common events;
- ordering turns, so two turns never write to one session or one lane at once;
- making sure every turn ends with exactly one terminal event;
- killing what it started: on timeout, on cancel, and when the caller walks
  away;
- remembering in-flight turns on disk, so a restart can find and report them.

### SCOPE

- **REQ-SCOPE-1** — No module under `src/` imports `aiogram`, `telegram`,
  `slack` or any other chat client. A test fails the build if one does.
- **REQ-SCOPE-2** — The package has no runtime dependencies. A test reads
  `pyproject.toml` and fails if `dependencies` is not empty.
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
- **REQ-ENV-3** — `CLAUDE_CONFIG_DIR` and `CODEX_HOME` are refused in a turn's
  `env`. An account is chosen through `Account`, not overridden per turn.

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
- **REQ-STATE-4** — Before returning, `stranded()` kills the recorded process
  group, but only if its leader still exists **and** its start time matches
  the recorded one. A recycled pid is never killed. `killed` says whether a
  kill happened.
- **REQ-STATE-5** — `engine.forget(turn_id)` removes a stranded marker. The
  markers stay until then, so a caller that crashes while handling them sees
  them again.
- **REQ-STATE-6** — A marker never contains `credentials` or the turn's `env`.

---

## 12. Not measured yet

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

## 13. Out of scope for v1

- Agents calling each other, account pools, and switching accounts on a limit.
  These are the next specification.
- Text streamed word by word. Claude Code can do it
  (`--include-partial-messages`); Codex `exec --json` cannot. v1 events are
  the part both share.
- Anything about chats, rendering or delivery. That belongs to a Telegram adapter
  built on televibe.
- MCP servers, plugins and hooks per turn. They belong to the account.
- Windows.

## 14. Tests

- **REQ-TEST-1** — Every requirement id above appears in at least one test
  name or docstring. A test fails the build if one does not.
- **REQ-TEST-2** — Contract tests (sections 4 to 8, and 11) run against every
  provider through **fake agent binaries**. The fakes replay output recorded
  from real runs. Environment knobs make them hang, crash mid-stream, lose the
  session, flood stderr, or emit a broken line.
- **REQ-TEST-3** — Recorded outputs live in `tests/fixtures/<provider>/`, with
  the CLI version they were recorded with. Local paths and ids in them are
  replaced with placeholders.
- **REQ-TEST-4** — One live test per provider runs a real two-turn session
  (start, then resume). It is marked `live` and skipped unless an account
  directory is given in the environment.
