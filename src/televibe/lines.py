"""JSON records from an agent's stdout, one per line (REQ-RUN-3)."""

import asyncio
import json
from collections.abc import AsyncIterator
from typing import Any

LINE_LIMIT = 8 * 1024 * 1024
_CHUNK = 64 * 1024


def _decode(line: bytes) -> dict[str, Any] | None:
    line = line.strip()
    if not line:
        return None
    try:
        value = json.loads(line)
    except ValueError:  # includes UnicodeDecodeError
        return None
    return value if isinstance(value, dict) else None


async def json_records(stream: asyncio.StreamReader, limit: int = LINE_LIMIT) -> AsyncIterator[dict[str, Any]]:
    """Yield each line that is a JSON object; skip everything else, including lines over `limit` bytes."""
    buf = bytearray()
    skipping = False  # inside a line that already went over the limit
    while chunk := await stream.read(_CHUNK):
        start = 0
        while (newline := chunk.find(b"\n", start)) >= 0:
            if skipping:
                skipping = False
            else:
                buf += chunk[start:newline]
                if len(buf) <= limit and (record := _decode(bytes(buf))) is not None:
                    yield record
            buf.clear()
            start = newline + 1
        if not skipping:
            buf += chunk[start:]
            if len(buf) > limit:
                buf.clear()
                skipping = True
    if buf and not skipping and (record := _decode(bytes(buf))) is not None:
        yield record
