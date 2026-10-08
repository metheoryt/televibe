# Recorded agent outputs

Each file is a real run, sanitized by `sanitize.py`: the session id is
`{{SESSION_ID}}`, other UUIDs are zero, local paths are `{{PATH}}`, and the
recording account's hooks, tools, MCP servers, plugins and skills are dropped.
The fake agents in `tests/fakes/` replay them (REQ-TEST-2).

| Fixture | CLI version | What it is |
|---|---|---|
| `claude/ok.jsonl` | Claude Code 2.1.294 | New session (`--session-id`), plan mode, one `Read` tool use, success. |
| `claude/resume.jsonl` | Claude Code 2.1.294 | `--resume` from another directory, success. |
| `claude/lost.jsonl` | Claude Code 2.1.294 | `--resume` of an unknown id: `result` with `is_error: true`. |
| `claude/lost.stderr` | Claude Code 2.1.294 | stderr of the same run. |
| `codex/ok.jsonl` | codex-cli 0.161.0 | New session: a hook warning, two messages around a command. |
| `codex/resume.jsonl` | codex-cli 0.161.0 | `exec resume` of the same thread. |
| `codex/readonly.jsonl` | codex-cli 0.161.0 | `--sandbox read-only`, asked to write a file: refused in the answer. |
| `codex/resume_readonly.jsonl` | codex-cli 0.161.0 | Options before `resume`: read-only held, `-C` directory used. |
| `codex/lost.jsonl` | codex-cli 0.161.0 | `exec resume` of an unknown id: no JSON at all. |
| `codex/lost.stderr` | codex-cli 0.161.0 | stderr of the same run. |
