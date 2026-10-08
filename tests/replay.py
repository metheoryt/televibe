"""Feed a recorded fixture through a provider's parser, without a process."""

import json
from pathlib import Path

FIXTURES = Path(__file__).parent / "fixtures"


def read_fixture(kind: str, name: str) -> list[dict]:
    text = (FIXTURES / kind / name).read_text()
    return [json.loads(line) for line in text.splitlines() if line.strip()]


def replay(provider, session, name: str, returncode: int = 0, stderr: str = ""):
    parser = provider.parser(session)
    events = []
    for record in read_fixture(provider.kind, name):
        events.extend(parser.feed(record))
        if parser.terminal is not None:
            break
    return events, parser.finish(returncode, stderr)
