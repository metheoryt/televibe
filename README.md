# televibe

Run headless coding agents (Claude Code, Codex) one turn at a time, as
resumable sessions with a typed event stream.

televibe starts the agent, turns its output into common events
(`Started`, `Message`, `ToolUse`, `Warning`, then exactly one `Done` or
`Failed`), and kills what it started on timeout, cancel, or when the caller
walks away. What to do with the output is the caller's business: televibe never
sends a message anywhere.

Status: v1 implemented, not yet published. Tests: `uv run pytest`; live tests
against real agents: see `tests/test_live.py`.

- [SPEC.md](SPEC.md) is the specification. It is the law: numbered
  requirements, each claimed by a test.
