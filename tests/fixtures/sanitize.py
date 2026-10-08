"""Turn a raw agent recording into a fixture (REQ-TEST-3).

Usage: python tests/fixtures/sanitize.py <raw file> <out file> --session-id <id>

The session id becomes {{SESSION_ID}}, every other UUID the zero UUID, and
every absolute path under a home or temp directory {{PATH}}. Claude Code hook
events and the account's tool, MCP, plugin and skill lists are dropped: they
describe the recording person's own setup, not the stream format.
"""

import argparse
import json
import re
from pathlib import Path

UUID = re.compile(r"[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}")
# "Application Support" is the one space macOS puts in a common path.
LOCAL_PATH = re.compile(r"(?:/Users|/home|/private|/var/folders|/tmp)/(?:[^\s\"'`]| (?=Support/))*")
ZERO_UUID = "00000000-0000-0000-0000-000000000000"
ACCOUNT_LISTS = ("tools", "mcp_servers", "slash_commands", "agents", "skills", "plugins")


def clean_text(text: str, session_id: str) -> str:
    text = text.replace(session_id, "{{SESSION_ID}}")
    text = UUID.sub(ZERO_UUID, text)
    return LOCAL_PATH.sub("{{PATH}}", text)


def clean_value(value, session_id: str):
    if isinstance(value, str):
        return clean_text(value, session_id)
    if isinstance(value, list):
        return [clean_value(item, session_id) for item in value]
    if isinstance(value, dict):
        return {key: clean_value(item, session_id) for key, item in value.items()}
    return value


def clean_record(record: dict, session_id: str) -> dict | None:
    if record.get("type") == "system" and str(record.get("subtype", "")).startswith("hook_"):
        return None
    if record.get("type") == "system" and record.get("subtype") == "init":
        record = {**record, **{key: [] for key in ACCOUNT_LISTS if key in record}}
    if record.get("type") == "assistant":
        content = record.get("message", {}).get("content", [])
        for block in content:
            if isinstance(block, dict) and block.get("type") == "thinking":
                block["signature"] = ""
    return clean_value(record, session_id)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("raw", type=Path)
    parser.add_argument("out", type=Path)
    parser.add_argument("--session-id", required=True)
    args = parser.parse_args()
    text = args.raw.read_text()
    if args.raw.suffix == ".jsonl":
        lines = []
        for line in text.splitlines():
            if not line.strip():
                continue
            record = clean_record(json.loads(line), args.session_id)
            if record is not None:
                lines.append(json.dumps(record, ensure_ascii=False))
        args.out.write_text("".join(f"{line}\n" for line in lines))
    else:
        args.out.write_text(clean_text(text, args.session_id))


if __name__ == "__main__":
    main()
